"""Instagram carousel adapter implementing the official container/publish flow."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qsl

from news_ai_content import ContentFormat, ContentPlatform
from pydantic import AnyHttpUrl, TypeAdapter

from .config import InstagramPlatformConfig
from .contracts import (
    InstagramCarouselRequest,
    InstagramContainerStatus,
    InstagramContainerStatusResult,
    InstagramPreparedPublication,
    InstagramPublishResult,
    PublicationVerificationResult,
    PublicationVerificationStatus,
    SocialCapabilities,
    SocialMediaFormat,
    SocialMediaMimeType,
    SocialMediaType,
    SocialPublishStatus,
)
from .errors import SocialAdapterError, SocialErrorClass
from .transport import GraphResponse, GraphTransportError, InstagramGraphTransport

_GRAPH_ID_RE = re.compile(r"^[0-9]{1,255}$")
_SENSITIVE_QUERY_KEYS = {"access_token", "api_key", "apikey", "token", "password", "secret"}
_READY_CONTAINER_STATES = {"FINISHED", "PUBLISHED"}
_FAILED_CONTAINER_STATES = {"ERROR", "EXPIRED"}
_THROTTLE_CODES = {4, 17, 32, 341, 613, *range(80000, 80015)}
_HTTP_URL = TypeAdapter(AnyHttpUrl)


def canonical_public_media_url(value: str | AnyHttpUrl) -> str:
    """Validate and canonicalize an exact public delivery reference, without I/O."""

    try:
        url = _HTTP_URL.validate_python(value)
    except (TypeError, ValueError) as exc:
        raise SocialAdapterError(
            "media URL is invalid", classification=SocialErrorClass.MEDIA
        ) from exc
    _validate_public_https_url(url)
    return str(url)


def validate_instagram_request(
    request: InstagramCarouselRequest, config: InstagramPlatformConfig
) -> None:
    if request.platform is not ContentPlatform.INSTAGRAM:
        raise _validation_error("Instagram adapter requires platform INSTAGRAM")
    if request.format is not ContentFormat.CAROUSEL:
        raise _validation_error("Instagram adapter requires format CAROUSEL")
    if request.format not in config.supported_formats or not config.capabilities.carousel:
        raise _validation_error("Instagram carousel capability is disabled")
    count = len(request.media_items)
    constraints = config.constraints
    if not constraints.min_carousel_items <= count <= constraints.max_carousel_items:
        raise _validation_error("Instagram carousel media count is outside configured limits")
    if len(request.graph_caption) > constraints.caption_max_characters:
        raise _validation_error("Instagram caption exceeds configured limit")
    for item in request.media_items:
        if item.media_type not in constraints.allowed_media_types:
            raise SocialAdapterError(
                "Instagram media type is not supported",
                classification=SocialErrorClass.MEDIA,
            )
        if item.media_type is SocialMediaType.IMAGE and not config.capabilities.image:
            raise SocialAdapterError(
                "Instagram image capability is disabled",
                classification=SocialErrorClass.MEDIA,
            )
        if item.media_type is SocialMediaType.IMAGE and (
            item.media_format not in constraints.allowed_image_formats
            or item.mime_type not in constraints.allowed_image_mime_types
            or item.media_format is not SocialMediaFormat.JPEG
            or item.mime_type is not SocialMediaMimeType.JPEG
        ):
            raise SocialAdapterError(
                "Instagram image media must be ordinary JPEG with image/jpeg MIME type",
                classification=SocialErrorClass.MEDIA,
            )
        _validate_public_https_url(item.public_url)


def _validate_public_https_url(url: Any) -> None:
    if url.scheme != "https":
        raise SocialAdapterError("media URL must use HTTPS", classification=SocialErrorClass.MEDIA)
    if url.username is not None or url.password is not None:
        raise SocialAdapterError(
            "media URL must not contain credentials", classification=SocialErrorClass.MEDIA
        )
    host = (url.host or "").rstrip(".").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SocialAdapterError(
            "media URL host is not a public delivery host",
            classification=SocialErrorClass.MEDIA,
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise SocialAdapterError(
                "media URL host is not a public delivery host",
                classification=SocialErrorClass.MEDIA,
            ) from None
    else:
        if not address.is_global:
            raise SocialAdapterError(
                "media URL IP is not globally routable",
                classification=SocialErrorClass.MEDIA,
            )
    query_keys = {key.lower().replace("-", "_") for key, _ in parse_qsl(url.query or "")}
    if query_keys & _SENSITIVE_QUERY_KEYS:
        raise SocialAdapterError(
            "media URL must not contain credential query parameters",
            classification=SocialErrorClass.MEDIA,
        )


class InstagramAdapter:
    def __init__(
        self,
        config: InstagramPlatformConfig,
        *,
        account_id: str,
        transport: InstagramGraphTransport,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not _GRAPH_ID_RE.fullmatch(account_id):
            raise ValueError("Instagram account ID is invalid")
        self.config = config
        self.account_id = account_id
        self.transport = transport
        self._sleep = sleeper

    @property
    def capabilities(self) -> SocialCapabilities:
        return self.config.capabilities

    def validate_content(self, request: InstagramCarouselRequest) -> None:
        validate_instagram_request(request, self.config)

    async def publish(self, request: InstagramCarouselRequest) -> InstagramPublishResult:
        prepared = await self.prepare_publication(request)
        return await self.publish_prepared(prepared)

    async def prepare_publication(
        self, request: InstagramCarouselRequest
    ) -> InstagramPreparedPublication:
        self.validate_content(request)
        children: list[str] = []
        for item in request.media_items:
            child = await self._post(
                f"{self.account_id}/media",
                {
                    "image_url": str(item.public_url),
                    "is_carousel_item": "true",
                },
            )
            child_id = self._required_id(child, operation="child container creation")
            await self._wait_until_ready(child_id)
            children.append(child_id)

        parent = await self._post(
            f"{self.account_id}/media",
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": request.graph_caption,
            },
        )
        parent_id = self._required_id(parent, operation="carousel container creation")
        await self._wait_until_ready(parent_id)
        return InstagramPreparedPublication(
            container_id=parent_id,
            child_container_ids=tuple(children),
            request_hash=instagram_request_hash(request),
        )

    async def publish_prepared(
        self, prepared: InstagramPreparedPublication, *, verify: bool = True
    ) -> InstagramPublishResult:
        if prepared.mock or not _GRAPH_ID_RE.fullmatch(prepared.container_id):
            raise _validation_error("Instagram prepared container is invalid")
        published = await self._post(
            f"{self.account_id}/media_publish", {"creation_id": prepared.container_id}
        )
        external_id = self._required_id(published, operation="carousel publication")

        if not verify:
            return InstagramPublishResult(
                status=SocialPublishStatus.CREATED,
                external_post_id=external_id,
                provider_metadata={"container_id": prepared.container_id},
            )

        verification: PublicationVerificationResult | None = None
        with suppress(SocialAdapterError):
            verification = await self.verify_publication(external_id)
        verification_status = (
            verification.status
            if verification is not None
            else PublicationVerificationStatus.UNKNOWN
        )
        confirmed = verification_status is PublicationVerificationStatus.PUBLISHED
        return InstagramPublishResult(
            status=(SocialPublishStatus.PUBLISHED if confirmed else SocialPublishStatus.CREATED),
            external_post_id=external_id,
            external_url=verification.external_url if confirmed and verification else None,
            provider_metadata={
                "container_id": prepared.container_id,
                "verification_status": verification_status.value,
            },
        )

    async def verify_publication(self, external_post_id: str) -> PublicationVerificationResult:
        if not _GRAPH_ID_RE.fullmatch(external_post_id):
            raise _validation_error("external Instagram identifier is invalid")
        response = await self._get(
            external_post_id,
            {"fields": "id,permalink,media_type,timestamp"},
            allow_not_found=True,
        )
        if response.status_code == 404:
            return PublicationVerificationResult(
                external_post_id=external_post_id,
                status=PublicationVerificationStatus.NOT_FOUND,
            )
        payload = response.payload
        status = (
            PublicationVerificationStatus.PUBLISHED
            if payload.get("id") == external_post_id
            else PublicationVerificationStatus.UNKNOWN
        )
        permalink = _safe_instagram_permalink(payload.get("permalink"))
        metadata: dict[str, str | int | bool] = {}
        media_type = payload.get("media_type")
        if media_type in {"IMAGE", "VIDEO", "CAROUSEL_ALBUM"}:
            metadata["media_type"] = media_type
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str) and re.fullmatch(r"[0-9T:+.Z-]{10,40}", timestamp):
            metadata["timestamp"] = timestamp
        return PublicationVerificationResult(
            external_post_id=external_post_id,
            status=status,
            external_url=permalink,
            provider_metadata=metadata,
        )

    async def get_container_status(self, container_id: str) -> InstagramContainerStatusResult:
        """Read IG Container fields; published IG Media uses a separate read contract."""

        if not _GRAPH_ID_RE.fullmatch(container_id):
            raise _validation_error("Instagram container identifier is invalid")
        response = await self._get(
            container_id,
            {"fields": "status_code,status"},
            allow_not_found=False,
        )
        raw_status = str(response.payload.get("status_code", "")).upper()
        try:
            status = InstagramContainerStatus(raw_status)
        except ValueError:
            status = InstagramContainerStatus.UNKNOWN
        return InstagramContainerStatusResult(container_id=container_id, status=status)

    async def _wait_until_ready(self, container_id: str) -> None:
        for attempt in range(self.config.polling.max_attempts):
            result = await self.get_container_status(container_id)
            if result.status.value in _READY_CONTAINER_STATES:
                return
            if result.status.value in _FAILED_CONTAINER_STATES:
                raise SocialAdapterError(
                    "Instagram media container processing failed",
                    classification=SocialErrorClass.MEDIA,
                )
            if attempt + 1 < self.config.polling.max_attempts:
                await self._sleep(self.config.polling.interval_seconds)
        raise SocialAdapterError(
            "Instagram media container processing timed out",
            classification=SocialErrorClass.TRANSIENT,
        )

    async def _post(self, path: str, data: dict[str, str]) -> GraphResponse:
        try:
            response = await self.transport.post(path, data=data)
        except GraphTransportError as exc:
            if exc.outcome_may_be_ambiguous:
                raise SocialAdapterError(
                    "Instagram operation outcome is unknown; do not retry blindly",
                    classification=SocialErrorClass.AMBIGUOUS,
                    outcome_may_be_ambiguous=True,
                ) from exc
            raise SocialAdapterError(
                "Instagram endpoint is temporarily unavailable",
                classification=SocialErrorClass.TRANSIENT,
            ) from exc
        self._raise_graph_error(response)
        return response

    async def _get(
        self, path: str, params: dict[str, str], *, allow_not_found: bool
    ) -> GraphResponse:
        try:
            response = await self.transport.get(path, params=params)
        except GraphTransportError as exc:
            raise SocialAdapterError(
                "Instagram verification endpoint is temporarily unavailable",
                classification=SocialErrorClass.TRANSIENT,
            ) from exc
        if allow_not_found and response.status_code == 404:
            return response
        self._raise_graph_error(response)
        return response

    @staticmethod
    def _required_id(response: GraphResponse, *, operation: str) -> str:
        value = response.payload.get("id")
        if not isinstance(value, str) or not _GRAPH_ID_RE.fullmatch(value):
            raise SocialAdapterError(
                f"Instagram {operation} returned no valid identifier",
                classification=SocialErrorClass.AMBIGUOUS,
                http_status=response.status_code,
                outcome_may_be_ambiguous=True,
            )
        return value

    @staticmethod
    def _raise_graph_error(response: GraphResponse) -> None:
        if response.status_code < 400:
            return
        error = response.payload.get("error")
        error_map = error if isinstance(error, dict) else {}
        code_value = error_map.get("code")
        provider_code = (
            code_value if isinstance(code_value, int) and not isinstance(code_value, bool) else None
        )
        error_type = error_map.get("type") if isinstance(error_map.get("type"), str) else ""
        classification = _classify_graph_error(response.status_code, provider_code, error_type)
        raise SocialAdapterError(
            "Instagram Graph API rejected the operation",
            classification=classification,
            http_status=response.status_code,
            provider_code=provider_code,
            retry_after_seconds=response.retry_after_seconds,
        )


class MockInstagramAdapter:
    """Deterministic test/development adapter with no transport dependency."""

    def __init__(self, config: InstagramPlatformConfig) -> None:
        self.config = config
        self._published: set[str] = set()

    @property
    def capabilities(self) -> SocialCapabilities:
        return self.config.capabilities

    def validate_content(self, request: InstagramCarouselRequest) -> None:
        validate_instagram_request(request, self.config)

    async def publish(self, request: InstagramCarouselRequest) -> InstagramPublishResult:
        return await self.publish_prepared(await self.prepare_publication(request))

    async def prepare_publication(
        self, request: InstagramCarouselRequest
    ) -> InstagramPreparedPublication:
        self.validate_content(request)
        request_hash = instagram_request_hash(request)
        return InstagramPreparedPublication(
            container_id=f"mock_container_{request_hash[:24]}",
            child_container_ids=tuple(
                f"mock_child_{request_hash[:24]}_{item.position}" for item in request.media_items
            ),
            request_hash=request_hash,
            mock=True,
        )

    async def publish_prepared(
        self, prepared: InstagramPreparedPublication, *, verify: bool = True
    ) -> InstagramPublishResult:
        if not prepared.mock:
            raise _validation_error("MOCK adapter requires a MOCK container")
        external_id = f"mock_ig_{prepared.request_hash[:24]}"
        self._published.add(external_id)
        return InstagramPublishResult(
            status=SocialPublishStatus.PUBLISHED,
            external_post_id=external_id,
            external_url=f"https://instagram-publish.invalid/{external_id}",
            mock=True,
            provider_metadata={"mode": "MOCK"},
        )

    async def verify_publication(self, external_post_id: str) -> PublicationVerificationResult:
        # MOCK identities are deterministic and recoverable across process restarts.
        found = bool(re.fullmatch(r"mock_ig_[0-9a-f]{24}", external_post_id))
        return PublicationVerificationResult(
            external_post_id=external_post_id,
            status=(
                PublicationVerificationStatus.PUBLISHED
                if found
                else PublicationVerificationStatus.NOT_FOUND
            ),
            external_url=(
                f"https://instagram-publish.invalid/{external_post_id}" if found else None
            ),
            mock=True,
            provider_metadata={"mode": "MOCK"},
        )

    async def get_container_status(self, container_id: str) -> InstagramContainerStatusResult:
        return InstagramContainerStatusResult(
            container_id=container_id,
            status=(
                InstagramContainerStatus.FINISHED
                if re.fullmatch(r"mock_container_[0-9a-f]{24}", container_id)
                else InstagramContainerStatus.UNKNOWN
            ),
        )


def instagram_request_hash(request: InstagramCarouselRequest) -> str:
    """Identity of the exact validated command, including ordered delivery references."""

    canonical = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classify_graph_error(
    status: int, provider_code: str | int | None, error_type: str
) -> SocialErrorClass:
    if status == 429 or provider_code in _THROTTLE_CODES:
        return SocialErrorClass.RATE_LIMIT
    # Meta may return permission code 200 with the broad OAuthException type.
    # The numeric cause is more specific than the envelope type.
    if status == 403 or provider_code in {10, 200}:
        return SocialErrorClass.PERMISSION
    if status == 401 or provider_code == 190 or "oauth" in error_type.lower():
        return SocialErrorClass.AUTHENTICATION
    if status == 404:
        return SocialErrorClass.NOT_FOUND
    if status >= 500:
        return SocialErrorClass.TRANSIENT
    if status == 400:
        return SocialErrorClass.VALIDATION
    return SocialErrorClass.PLATFORM


def _validation_error(message: str) -> SocialAdapterError:
    return SocialAdapterError(message, classification=SocialErrorClass.VALIDATION)


def _safe_instagram_permalink(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

    try:
        url = TypeAdapter(AnyHttpUrl).validate_python(value)
    except ValidationError:
        return None
    host = (url.host or "").lower()
    if (
        url.scheme != "https"
        or host not in {"instagram.com", "www.instagram.com"}
        or url.username is not None
        or url.password is not None
        or url.query is not None
        or url.fragment is not None
    ):
        return None
    return str(url)
