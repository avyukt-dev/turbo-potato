"""Authenticated Telegram interaction boundary for exact-version human approval."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from news_ai_common.config import AppSettings
from news_ai_database import ContentVariant, SocialAccount
from news_ai_domain import ReviewState
from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    RedisStreamConsumer,
    ReliableMessageProcessor,
    WorkerBatchResult,
)
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_publishing import CreatePublicationRequest, PublicationError, PublicationService
from news_ai_review import (
    ReviewActionRequest,
    ReviewCapability,
    ReviewerPrincipal,
    ReviewError,
    ReviewService,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

_CALLBACK_PATTERN = re.compile(r"^approve:([0-9a-f]{32}):([1-9][0-9]*)$")
_TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_MESSAGE_LIMIT = 4096
_TELEGRAM_MEDIA_CAPTION_LIMIT = 1024
TELEGRAM_REVIEW_CONSUMER_GROUP = "telegram-review-notifier"


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int
    type: str = Field(min_length=1, max_length=32)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    message_id: int = Field(ge=1)
    from_user: TelegramUser | None = Field(default=None, alias="from")
    chat: TelegramChat
    text: str | None = Field(default=None, max_length=4096)


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    from_user: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = Field(default=None, min_length=1, max_length=64)


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    update_id: int = Field(ge=0)
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class TelegramTransport(Protocol):
    async def send_message(
        self, *, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> None: ...

    async def send_photo(self, *, chat_id: int, photo: str, caption: str) -> None: ...

    async def answer_callback_query(self, *, callback_query_id: str, text: str) -> None: ...

    async def clear_reply_markup(self, *, chat_id: int, message_id: int) -> None: ...


class TelegramBotTransport:
    """Secret-safe Telegram Bot API transport."""

    def __init__(self, token: str, *, timeout_seconds: float = 10.0) -> None:
        if not token.strip():
            raise ValueError("Telegram bot token must not be blank")
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def send_message(
        self, *, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._call("sendMessage", payload)

    async def send_photo(self, *, chat_id: int, photo: str, caption: str) -> None:
        await self._call(
            "sendPhoto",
            {"chat_id": chat_id, "photo": photo, "caption": caption},
        )

    async def answer_callback_query(self, *, callback_query_id: str, text: str) -> None:
        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text, "show_alert": False},
        )

    async def clear_reply_markup(self, *, chat_id: int, message_id: int) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def _call(self, method: str, payload: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{_TELEGRAM_API_BASE}/bot{self._token}/{method}", json=payload
                )
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("Telegram Bot API is unavailable") from None
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("Telegram Bot API rejected the request")


class TelegramReviewController:
    """Translate allowlisted Telegram actions into canonical ReviewService calls."""

    def __init__(
        self,
        service: ReviewService,
        transport: TelegramTransport,
        *,
        chat_id: int,
        reviewer_user_id: int,
        principal: ReviewerPrincipal,
        publication_service: PublicationService | None = None,
        publication_account_identifier: str | None = None,
    ) -> None:
        self.service = service
        self.transport = transport
        self.chat_id = chat_id
        self.reviewer_user_id = reviewer_user_id
        self.principal = principal
        self.publication_service = publication_service
        self.publication_account_identifier = publication_account_identifier
        if publication_service is not None and (
            publication_account_identifier is None
            or not principal.has_any(ReviewCapability.PUBLISH)
        ):
            raise ValueError("Telegram automatic publication configuration is incomplete")

    async def handle(self, update: TelegramUpdate) -> None:
        if update.callback_query is not None:
            await self._callback(update.callback_query)
            return
        if update.message is not None:
            await self._message(update.message)

    async def _message(self, message: TelegramMessage) -> None:
        if not self._authorized(message.from_user, message.chat):
            return
        command = (message.text or "").split(maxsplit=1)[0].casefold()
        if command.split("@", maxsplit=1)[0] != "/review":
            return
        if not self.principal.has_any(ReviewCapability.VIEW, ReviewCapability.REVIEW):
            await self.transport.send_message(
                chat_id=self.chat_id, text="Review access is unavailable."
            )
            return
        page = await run_in_threadpool(
            self.service.queue,
            limit=1,
            review_state=ReviewState.READY_FOR_REVIEW,
        )
        if not page.items:
            await self.transport.send_message(
                chat_id=self.chat_id, text="No content is ready for review."
            )
            return
        item = page.items[0]
        if not await self.send_review(item.artifact_id, item.artifact_version):
            await self.transport.send_message(
                chat_id=self.chat_id, text="This content is no longer ready for review."
            )

    async def send_review(self, artifact_id: UUID, artifact_version: int) -> bool:
        """Send one exact review artifact; callers retain durable delivery ownership."""

        detail = await run_in_threadpool(
            self.service.detail, artifact_type="content_variant", artifact_id=artifact_id
        )
        detail_payload = detail.model_dump(mode="json")
        if (
            detail_payload.get("artifact_id") != str(artifact_id)
            or detail_payload.get("artifact_version") != artifact_version
        ):
            raise ValueError("Telegram review packet does not match the queued artifact")
        if detail_payload.get("current_reviewable") is not True:
            return False
        callback_data = f"approve:{artifact_id.hex}:{artifact_version}"
        packet = _build_review_packet(detail_payload)
        # The approval action is deliberately sent last. A partial packet or a
        # failed media preview can therefore never expose an approval button.
        for text in packet.messages:
            await self.transport.send_message(chat_id=self.chat_id, text=text)
        for media in packet.media:
            await self.transport.send_photo(
                chat_id=self.chat_id,
                photo=media.url,
                caption=media.caption,
            )
        await self.transport.send_message(
            chat_id=self.chat_id,
            text=(
                "Approval action\n"
                "Approve only after reviewing the complete publication preview "
                "and every media item."
            ),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Approve exact version", "callback_data": callback_data}]
                ]
            },
        )
        return True

    async def _callback(self, callback: TelegramCallbackQuery) -> None:
        message = callback.message
        if message is None or not self._authorized(callback.from_user, message.chat):
            await self.transport.answer_callback_query(
                callback_query_id=callback.id, text="This reviewer is not authorized."
            )
            return
        match = _CALLBACK_PATTERN.fullmatch(callback.data or "")
        if match is None or not self.principal.has_any(ReviewCapability.APPROVE):
            await self.transport.answer_callback_query(
                callback_query_id=callback.id, text="This approval action is not authorized."
            )
            return
        artifact_id = UUID(hex=match.group(1))
        artifact_version = int(match.group(2))
        operation_key = f"telegram:approve:{artifact_id.hex}:{artifact_version}"
        request_identity = uuid5(NAMESPACE_URL, operation_key)
        try:
            await run_in_threadpool(
                self.service.decide,
                artifact_type="content_variant",
                artifact_id=artifact_id,
                request=ReviewActionRequest(artifact_version=artifact_version),
                decision=ReviewState.APPROVED,
                principal=self.principal,
                idempotency_key=operation_key,
                request_id=request_identity,
                correlation_id=request_identity,
            )
        except ReviewError:
            await self.transport.answer_callback_query(
                callback_query_id=callback.id,
                text="Approval was not applied; refresh the review item.",
            )
            return
        if self.publication_service is not None:
            try:
                await run_in_threadpool(
                    self._schedule_approved_publication,
                    artifact_id,
                    artifact_version,
                    request_identity,
                )
            except PublicationError:
                await self.transport.answer_callback_query(
                    callback_query_id=callback.id,
                    text="Approved; publication scheduling is temporarily unavailable.",
                )
                return
        await self.transport.clear_reply_markup(
            chat_id=message.chat.id, message_id=message.message_id
        )
        await self.transport.answer_callback_query(
            callback_query_id=callback.id,
            text=(
                "Approved and scheduled for publication."
                if self.publication_service is not None
                else "Exact content version approved."
            ),
        )

    def _schedule_approved_publication(
        self, artifact_id: UUID, artifact_version: int, request_identity: UUID
    ) -> None:
        assert self.publication_service is not None
        with self.publication_service.session_factory() as session:
            account_id = session.scalar(
                select(SocialAccount.id).where(
                    SocialAccount.platform == "INSTAGRAM",
                    SocialAccount.account_identifier == self.publication_account_identifier,
                )
            )
        if account_id is None:
            raise PublicationError("ACCOUNT_NOT_FOUND")
        key = f"telegram-publish:{artifact_id.hex}:{artifact_version}"
        publication = self.publication_service.create(
            CreatePublicationRequest(
                content_variant_id=artifact_id,
                social_account_id=account_id,
                platform="INSTAGRAM",
                scheduled_at=None,
                idempotency_key=key,
            ),
            self.principal,
            correlation_id=request_identity,
            request_id=request_identity,
        )
        self.publication_service.publish_now(
            publication.id,
            self.principal,
            idempotency_key=f"telegram-now:{artifact_id.hex}:{artifact_version}",
            request_id=request_identity,
        )

    def _authorized(self, user: TelegramUser | None, chat: TelegramChat) -> bool:
        return (
            user is not None
            and user.id == self.reviewer_user_id
            and chat.id == self.chat_id
            and chat.type in {"private", "group", "supergroup"}
        )


def create_telegram_review_router(
    controller: TelegramReviewController,
    webhook_secret: str,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/integrations/telegram", tags=["review"])
    expected = webhook_secret.encode("utf-8")

    @router.post("/review/webhook", status_code=200)
    async def webhook(
        update: TelegramUpdate,
        supplied_secret: Annotated[
            str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
        ] = None,
    ) -> dict[str, bool]:
        if supplied_secret is None or not hmac.compare_digest(
            supplied_secret.encode("utf-8"), expected
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHENTICATED", "message": "Authentication required"},
            )
        await controller.handle(update)
        return {"ok": True}

    return router


def build_telegram_review_controller(
    settings: AppSettings,
    service: ReviewService,
    *,
    transport: TelegramTransport | None = None,
    publication_service: PublicationService | None = None,
    publication_account_identifier: str | None = None,
) -> TelegramReviewController:
    if (
        settings.telegram_bot_token is None
        or settings.telegram_review_chat_id is None
        or settings.telegram_reviewer_user_id is None
        or settings.reviewer_id is None
    ):
        raise ValueError("Telegram review configuration is incomplete")
    requested = {item.strip() for item in settings.review_capabilities.split(",") if item.strip()}
    capabilities = frozenset(ReviewCapability(item) for item in requested)
    return TelegramReviewController(
        service,
        transport or TelegramBotTransport(settings.telegram_bot_token.get_secret_value()),
        chat_id=settings.telegram_review_chat_id,
        reviewer_user_id=settings.telegram_reviewer_user_id,
        principal=ReviewerPrincipal(
            reviewer_id=settings.reviewer_id,
            capabilities=capabilities,
        ),
        publication_service=publication_service,
        publication_account_identifier=publication_account_identifier,
    )


class TelegramReviewNotificationWorker:
    """Consume quality completion and push exact review packets with durable ACKs."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        controller: TelegramReviewController,
    ) -> None:
        if (
            consumer.stream != stream_for_event(EventType.CONTENT_QUALITY_CHECKED)
            or consumer.group != TELEGRAM_REVIEW_CONSUMER_GROUP
        ):
            raise ValueError("Telegram notifier must use the canonical content stream/group")
        self.consumer = consumer
        self.session_factory = session_factory
        self.controller = controller
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=TELEGRAM_REVIEW_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.CONTENT_QUALITY_CHECKED}),
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> WorkerBatchResult:
        return await self.reliability.process(await self.consumer.read(), self._handle_event)

    async def recover_once(
        self, *, min_idle_ms: int, start_id: str = "0-0"
    ) -> tuple[str, WorkerBatchResult]:
        return await self.reliability.recover(
            self._handle_event, min_idle_ms=min_idle_ms, start_id=start_id
        )

    async def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        with self.session_factory() as session:
            if was_processed(
                session, event_id=event.event_id, consumer_group=TELEGRAM_REVIEW_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            draft_id = UUID(str(event.payload["content_draft_id"]))
            variants = tuple(
                session.scalars(
                    select(ContentVariant)
                    .where(
                        ContentVariant.content_draft_id == draft_id,
                        ContentVariant.review_state == ReviewState.READY_FOR_REVIEW,
                    )
                    .order_by(ContentVariant.created_at, ContentVariant.id)
                )
            )
        notified = 0
        for variant in variants:
            if await self.controller.send_review(variant.id, variant.version):
                notified += 1
        with self.session_factory() as session, session.begin():
            if was_processed(
                session, event_id=event.event_id, consumer_group=TELEGRAM_REVIEW_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=TELEGRAM_REVIEW_CONSUMER_GROUP,
                result={"notified": notified},
            )
        return ProcessingOutcome.PROCESSED


@dataclass(frozen=True, slots=True)
class _TelegramMediaPreview:
    url: str
    caption: str


@dataclass(frozen=True, slots=True)
class _TelegramReviewPacket:
    messages: tuple[str, ...]
    media: tuple[_TelegramMediaPreview, ...]


def _build_review_packet(detail: dict[str, Any]) -> _TelegramReviewPacket:
    """Render publication-facing content while validating its durable review graph."""

    variant = _mapping(detail.get("content_variant"), "content_variant")
    sheet = _mapping(detail.get("fact_sheet"), "fact_sheet")
    quality = detail.get("quality_check")
    if quality is not None:
        _mapping(quality, "quality_check")

    artifact_id = str(detail.get("artifact_id") or "")
    artifact_version = detail.get("artifact_version")
    if not artifact_id or not isinstance(artifact_version, int) or artifact_version < 1:
        raise ValueError("Telegram review packet has invalid artifact identity")
    if detail.get("current_reviewable") is not True:
        raise ValueError("Telegram review artifact is no longer reviewable")

    media_ids = tuple(str(value) for value in variant.get("media_asset_ids") or ())
    raw_media = variant.get("media_provenance") or ()
    if not isinstance(raw_media, (list, tuple)):
        raise ValueError("Telegram review packet has invalid media provenance")
    media: list[_TelegramMediaPreview] = []
    provenance_ids: list[str] = []
    for position, raw_item in enumerate(raw_media, start=1):
        item = _mapping(raw_item, "media_provenance item")
        media_id = str(item.get("id") or "")
        url = str(item.get("public_url") or "")
        if not media_id or not url.startswith("https://"):
            raise ValueError("Telegram review packet has invalid media provenance")
        provenance_ids.append(media_id)
        caption = f"Media preview {position}/{len(raw_media)}"
        if len(caption) > _TELEGRAM_MEDIA_CAPTION_LIMIT:
            raise ValueError("Telegram review media provenance exceeds caption limit")
        media.append(_TelegramMediaPreview(url=url, caption=caption))
    if tuple(provenance_ids) != media_ids:
        raise ValueError("Telegram review media provenance does not match the artifact")

    # Complete provenance and policy data remain durable and validated. Telegram
    # is a focused approval surface, not a serialization of the internal graph.
    preview = _publication_text(variant)
    sources = _source_links(sheet, variant)
    messages = _chunk_text(f"Publication preview\n\n{preview}\n\nSources\n{sources}")
    return _TelegramReviewPacket(messages=messages, media=tuple(media))


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Telegram review packet has invalid {name}")
    return value


def _publication_text(variant: dict[str, Any]) -> str:
    title = str(variant.get("title") or "").strip()
    caption = str(variant.get("caption") or "").strip()
    payload = _mapping(variant.get("structured_payload"), "structured_payload")
    raw_slides = payload.get("slides") or ()
    if not isinstance(raw_slides, (list, tuple)):
        raise ValueError("Telegram review packet has invalid slides")

    sections: list[str] = []
    if title:
        sections.append(title)
    for position, raw_slide in enumerate(raw_slides, start=1):
        slide = _mapping(raw_slide, "slide")
        heading = str(slide.get("heading") or "").strip()
        body = str(slide.get("body") or "").strip()
        if not heading and not body:
            raise ValueError("Telegram review packet has an empty slide")
        text = "\n".join(part for part in (heading, body) if part)
        sections.append(f"Slide {position}\n{text}")
    if not raw_slides:
        body = str(variant.get("body") or "").strip()
        if body:
            sections.append(body)
    if caption:
        sections.append(f"Caption\n{caption}")

    hashtags = payload.get("hashtags") or ()
    if not isinstance(hashtags, (list, tuple)) or any(
        not isinstance(item, str) for item in hashtags
    ):
        raise ValueError("Telegram review packet has invalid hashtags")
    if hashtags:
        sections.append(" ".join(item.strip() for item in hashtags if item.strip()))
    if not sections:
        raise ValueError("Telegram review packet has no publication text")
    return "\n\n".join(sections)


def _source_links(sheet: dict[str, Any], variant: dict[str, Any]) -> str:
    raw_sources = sheet.get("sources") or ()
    raw_used_ids = variant.get("source_ids_used") or ()
    if not isinstance(raw_sources, (list, tuple)) or not isinstance(raw_used_ids, (list, tuple)):
        raise ValueError("Telegram review packet has invalid sources")
    used_ids = {str(value) for value in raw_used_ids}
    links: list[str] = []
    seen: set[str] = set()
    for raw_source in raw_sources:
        source = _mapping(raw_source, "source")
        if used_ids and str(source.get("source_id") or "") not in used_ids:
            continue
        url = str(source.get("url") or "").strip()
        if not url or url in seen:
            continue
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character in url for character in "\r\n\t")
        ):
            raise ValueError("Telegram review packet has an invalid source URL")
        seen.add(url)
        name = str(source.get("name") or "Source").strip() or "Source"
        if any(character in name for character in "\r\n\t"):
            raise ValueError("Telegram review packet has an invalid source name")
        links.append(f"• {name}: {url}")
    return "\n".join(links) if links else "No source link available"


def _chunk_text(text: str) -> tuple[str, ...]:
    """Split without dropping characters; Telegram sendMessage caps text at 4096."""

    if not text:
        return ("(empty)",)
    chunks: list[str] = []
    remaining = text
    while len(remaining) > _TELEGRAM_MESSAGE_LIMIT:
        split_at = remaining.rfind("\n", 0, _TELEGRAM_MESSAGE_LIMIT + 1)
        if split_at <= 0:
            split_at = _TELEGRAM_MESSAGE_LIMIT
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)
