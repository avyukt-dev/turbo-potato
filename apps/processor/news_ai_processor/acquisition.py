"""Bounded public source-material acquisition, never factual assessment or persistence."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
from news_ai_events import PermanentEventError, TransientEventError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArticleContentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    article_request_timeout_seconds: float = Field(default=20, gt=0, le=60, strict=True)
    article_max_response_bytes: int = Field(default=5_000_000, ge=1024, le=10_000_000, strict=True)
    article_max_redirects: int = Field(default=5, ge=0, le=10, strict=True)
    article_min_body_chars: int = Field(default=150, ge=1, le=10000, strict=True)
    article_max_body_chars: int = Field(default=200_000, ge=2, le=200_000, strict=True)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.article_max_body_chars <= self.article_min_body_chars:
            raise ValueError("article maximum body length must exceed minimum")
        return self


class ContentOrigin(StrEnum):
    FEED_CONTENT = "FEED_CONTENT"
    ARTICLE_PAGE = "ARTICLE_PAGE"


class ContentAcquisitionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    origin: ContentOrigin
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: datetime
    content_type: str = Field(pattern=r"^(text/html|application/xhtml\+xml|text/plain)$")
    response_bytes: int = Field(ge=0, le=10_000_000)
    extractor_version: str = "article-text-v1"
    redirect_count: int = Field(ge=0, le=10)
    final_host: str | None = Field(default=None, max_length=253, pattern=r"^[a-z0-9.-]+$")
    truncated: bool = False

    @model_validator(mode="after")
    def valid_provenance(self):
        if self.retrieved_at.tzinfo is None or self.truncated:
            raise ValueError("acquisition requires aware timestamps and complete text")
        if self.extractor_version != "article-text-v1":
            raise ValueError("unsupported article extractor")
        return self


class ArticleContentAcquisitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    body: str = Field(min_length=1, max_length=200_000)
    provenance: ContentAcquisitionProvenance

    @model_validator(mode="after")
    def exact_hash(self):
        if not self.body.strip() or hashlib.sha256(self.body.encode()).hexdigest() != (
            self.provenance.body_hash
        ):
            raise ValueError("acquired article body/hash is invalid")
        return self


class ArticleContentAcquisitionTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: UUID
    article_id: UUID
    discovery_id: UUID
    source_id: UUID
    source_feed_id: UUID | None
    canonical_url: str = Field(repr=False)
    source_domain: str | None
    raw_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_json: str = Field(repr=False)
    feed_body: str | None = Field(default=None, repr=False)


class ArticleContentAcquirer(Protocol):
    async def acquire(
        self, task: ArticleContentAcquisitionTask
    ) -> ArticleContentAcquisitionResult: ...


class _ArticleTextParser(HTMLParser):
    ignored = {
        "script",
        "style",
        "noscript",
        "svg",
        "form",
        "nav",
        "header",
        "footer",
        "aside",
        "head",
    }
    blocks = {"p", "div", "section", "article", "main", "li", "br", "h1", "h2", "h3"}
    voids = {"br", "hr", "img", "input", "meta", "link", "area", "source", "wbr", "embed"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = {"article": [], "main": [], "body": []}

    def _add(self, value):
        if not any(tag in self.ignored for tag in self.stack):
            self.parts["body"].append(value)
            for target in ("article", "main"):
                if target in self.stack:
                    self.parts[target].append(value)

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if len(self.stack) >= 512:
            raise PermanentEventError("article content markup is too deeply nested")
        if tag not in self.voids:
            self.stack.append(tag)
        if tag in self.blocks:
            self._add("\n\n")

    def handle_endtag(self, tag):
        tag = tag.rsplit(":", 1)[-1]
        if tag in self.blocks:
            self._add("\n\n")
        if tag in self.stack:
            position = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[position:]

    def handle_startendtag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in self.blocks:
            self._add("\n\n")

    def handle_data(self, data):
        self._add(data)

    def text(self):
        from .normalizer import normalize_body_text

        for target in ("article", "main", "body"):
            text = normalize_body_text("".join(self.parts[target]))
            if text:
                return text
        return None


def extract_article_text(content: str, *, html: bool, policy: ArticleContentPolicy) -> str:
    from .normalizer import normalize_body_text

    if html:
        parser = _ArticleTextParser()
        parser.feed(content)
        parser.close()
        text = parser.text()
    else:
        text = normalize_body_text(content)
    if not text or len(text) < policy.article_min_body_chars:
        raise PermanentEventError("article content could not be extracted")
    if len(text) > policy.article_max_body_chars:
        raise PermanentEventError("article content exceeds configured text limit")
    return text


async def resolve_public_host(host: str) -> tuple[str, ...]:
    records = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(sorted({record[4][0] for record in records}))


def _global_destination(address):
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.teredo or address in ipaddress.ip_network("64:ff9b::/96"):
            return False
        nested = address.ipv4_mapped or address.sixtofour
        if nested is not None:
            return _global_destination(nested)
    return True


class HttpArticleContentAcquirer:
    """One bounded, noncredentialed transport; vetted DNS is pinned through TCP/TLS.

    Transport injection is caller-owned. Default transport bypasses environment
    proxies, cookies, authentication, automatic redirects and HTTPX URL logging.
    Keepalive is disabled to avoid cross-host TLS reuse when public hosts share an IP.
    """

    def __init__(
        self,
        policy: ArticleContentPolicy,
        *,
        user_agent="news-ai-social-manager/0.1",
        max_concurrency=4,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] = resolve_public_host,
    ):
        self.policy = policy
        self.user_agent = user_agent
        self.resolver = resolver
        self._owns_transport = transport is None
        self._transport = (
            transport
            if transport is not None
            else httpx.AsyncHTTPTransport(
                trust_env=False,
                retries=0,
                limits=httpx.Limits(max_connections=max_concurrency, max_keepalive_connections=0),
            )
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._closed = False

    async def close(self):
        if not self._closed:
            self._closed = True
            if self._owns_transport:
                await self._transport.aclose()

    def _host(self, url, domain):
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
            allowed = (domain or "").encode("idna").decode("ascii").lower().rstrip(".")
            if (
                parts.scheme not in {"http", "https"}
                or not host
                or parts.username is not None
                or parts.password is not None
                or parts.port not in {None, 80, 443}
                or host == "localhost"
                or host.endswith((".localhost", ".local", ".internal"))
                or not allowed
                or not (host == allowed or host.endswith("." + allowed))
            ):
                raise ValueError
            # Non-global numeric hosts must fail before DNS/transport too.
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not _global_destination(address):
                raise ValueError
            return host
        except (ValueError, UnicodeError):
            raise PermanentEventError("article content host is not permitted") from None

    async def acquire(self, task):
        if self._closed:
            raise TransientEventError("article content transport is unavailable")
        async with self._semaphore:
            try:
                async with asyncio.timeout(self.policy.article_request_timeout_seconds):
                    return await self._acquire(task)
            except (TimeoutError, httpx.TimeoutException):
                raise TransientEventError("article content request timed out") from None
            except (httpx.TransportError, OSError):
                raise TransientEventError("article content network is unavailable") from None

    def _result(self, body, *, origin, content_type, byte_count, redirects=0, host=None):
        return ArticleContentAcquisitionResult(
            body=body,
            provenance=ContentAcquisitionProvenance(
                origin=origin,
                body_hash=hashlib.sha256(body.encode()).hexdigest(),
                retrieved_at=datetime.now(UTC),
                content_type=content_type,
                response_bytes=byte_count,
                redirect_count=redirects,
                final_host=host,
            ),
        )

    async def _acquire(self, task):
        if task.feed_body:
            byte_count = len(task.feed_body.encode())
            if byte_count <= self.policy.article_max_response_bytes:
                is_html = bool(re.search(r"<[a-zA-Z][^>]*>", task.feed_body))
                try:
                    body = extract_article_text(task.feed_body, html=is_html, policy=self.policy)
                except PermanentEventError:
                    pass  # Explicit but unusable feed content does not become summary fallback.
                else:
                    return self._result(
                        body,
                        origin=ContentOrigin.FEED_CONTENT,
                        content_type="text/html" if is_html else "text/plain",
                        byte_count=byte_count,
                    )
        current, visited = task.canonical_url, set()
        for hop in range(self.policy.article_max_redirects + 1):
            host = self._host(current, task.source_domain)
            if current in visited:
                raise PermanentEventError("article content redirect loop")
            visited.add(current)
            addresses = await self.resolver(host)
            try:
                if not addresses or any(
                    not _global_destination(ipaddress.ip_address(ip)) for ip in addresses
                ):
                    raise ValueError
            except ValueError:
                raise PermanentEventError(
                    "article content network destination is not permitted"
                ) from None
            # Numeric destination prevents a second DNS lookup/rebinding at connection time.
            original = httpx.URL(current)
            destination = original.copy_with(host=sorted(addresses)[0])
            request = httpx.Request(
                "GET",
                destination,
                headers={
                    "Host": original.netloc.decode("ascii"),
                    "User-Agent": self.user_agent,
                    "Accept": "text/html, application/xhtml+xml, text/plain",
                    "Accept-Encoding": "identity",
                },
                extensions={
                    "sni_hostname": host,
                    "timeout": {
                        key: self.policy.article_request_timeout_seconds
                        for key in ("connect", "read", "write", "pool")
                    },
                },
            )
            response = await self._transport.handle_async_request(request)
            try:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or hop == self.policy.article_max_redirects:
                        raise PermanentEventError("article content redirect limit exceeded")
                    current = urljoin(current, location)
                    continue
                if status in {408, 425, 429} or status >= 500:
                    raise TransientEventError("article content provider is temporarily unavailable")
                if status != 200:
                    raise PermanentEventError("article content document is unavailable")
                media_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise PermanentEventError("article content media type is unsupported")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise PermanentEventError("article content encoding is unsupported")
                length = response.headers.get("content-length")
                if length and (
                    not length.isdecimal() or int(length) > self.policy.article_max_response_bytes
                ):
                    raise PermanentEventError("article content response exceeds configured limit")
                data = bytearray()
                iterator = (
                    response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                )
                async for chunk in iterator:
                    if len(data) + len(chunk) > self.policy.article_max_response_bytes:
                        raise PermanentEventError(
                            "article content response exceeds configured limit"
                        )
                    data.extend(chunk)
                encoding = response.charset_encoding or "utf-8"
                try:
                    text = bytes(data).decode(encoding, errors="strict")
                except (UnicodeError, LookupError):
                    raise PermanentEventError(
                        "article content character encoding is invalid"
                    ) from None
                body = extract_article_text(
                    text, html=media_type != "text/plain", policy=self.policy
                )
                return self._result(
                    body,
                    origin=ContentOrigin.ARTICLE_PAGE,
                    content_type=media_type,
                    byte_count=len(data),
                    redirects=hop,
                    host=host,
                )
            finally:
                await response.aclose()
        raise PermanentEventError("article content redirect limit exceeded")
