"""Deterministic article normalization.

The normalizer deliberately performs only loss-minimizing transformations. It does not fetch
remote content, infer language, or make editorial decisions.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .models import ArticleNormalizationInput, NormalizedArticle

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
}
_INLINE_WHITESPACE_RE = re.compile(r"[\t\f\v ]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")


def _remove_tracking_query(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        (key, value)
        for key, value in items
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]


def _remove_last_path_segment(path: str) -> str:
    separator = path.rfind("/")
    return path[:separator] if separator >= 0 else ""


def _remove_dot_segments(path: str) -> str:
    """Apply RFC 3986 dot-segment removal without collapsing duplicate slashes."""

    input_buffer = path
    output = ""

    while input_buffer:
        if input_buffer.startswith("../"):
            input_buffer = input_buffer[3:]
        elif input_buffer.startswith("./"):
            input_buffer = input_buffer[2:]
        elif input_buffer.startswith("/./"):
            input_buffer = input_buffer[2:]
        elif input_buffer == "/.":
            input_buffer = "/"
        elif input_buffer.startswith("/../"):
            input_buffer = input_buffer[3:]
            output = _remove_last_path_segment(output)
        elif input_buffer == "/..":
            input_buffer = "/"
            output = _remove_last_path_segment(output)
        elif input_buffer in {".", ".."}:
            input_buffer = ""
        else:
            search_from = 1 if input_buffer.startswith("/") else 0
            separator = input_buffer.find("/", search_from)
            if separator < 0:
                output += input_buffer
                input_buffer = ""
            else:
                output += input_buffer[:separator]
                input_buffer = input_buffer[separator:]

    return output


def canonicalize_url(url: str) -> str:
    """Return a stable HTTP(S) URL while preserving semantic query parameters."""

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("article URL must use http or https")
    if not parts.hostname:
        raise ValueError("article URL must include a host")
    if parts.username or parts.password:
        raise ValueError("article URL must not contain embedded credentials")

    host = parts.hostname.encode("idna").decode("ascii").lower()
    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not default_port:
        host = f"{host}:{port}"

    normalized_path = _remove_dot_segments(parts.path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    normalized_path = quote(normalized_path, safe="/%:@!$&'()*+,;=-._~")

    query_items = _remove_tracking_query(parse_qsl(parts.query, keep_blank_values=True))
    query_items.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_items, doseq=True)

    return urlunsplit((scheme, host, normalized_path, query, ""))


def _unicode_text(value: str) -> str:
    value = html.unescape(value)
    value = unicodedata.normalize("NFC", value)
    return _CONTROL_RE.sub("", value)


def normalize_inline_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = _unicode_text(value).replace("\r", " ").replace("\n", " ")
    value = _INLINE_WHITESPACE_RE.sub(" ", value).strip()
    return value or None


def normalize_body_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = _unicode_text(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in value.split("\n")]
    normalized = "\n".join(lines).strip()
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized or None


def normalize_language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("_", "-").lower()
    if not normalized:
        return None
    if not _LANGUAGE_RE.fullmatch(normalized):
        raise ValueError("language must be a simple BCP-47 style tag")
    return normalized


def normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("article timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _content_hash(*, title: str, summary: str | None, body: str | None) -> str:
    payload = json.dumps(
        {"title": title, "summary": summary, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ArticleNormalizer:
    """Normalize collected article metadata into a deterministic processing contract."""

    def normalize(self, article: ArticleNormalizationInput) -> NormalizedArticle:
        title = normalize_inline_text(article.title)
        if title is None:
            raise ValueError("title is empty after normalization")

        author = normalize_inline_text(article.author)
        summary = normalize_body_text(article.summary)
        body = normalize_body_text(article.body)
        language = normalize_language(article.language)
        published_at = normalize_timestamp(article.published_at)
        retrieved_at = normalize_timestamp(article.retrieved_at)
        assert retrieved_at is not None

        return NormalizedArticle(
            source_id=article.source_id,
            source_feed_id=article.source_feed_id,
            canonical_url=canonicalize_url(str(article.url)),
            title=title,
            author=author,
            published_at=published_at,
            language=language,
            summary=summary,
            body=body,
            external_id=normalize_inline_text(article.external_id),
            retrieved_at=retrieved_at,
            content_hash=_content_hash(title=title, summary=summary, body=body),
        )
