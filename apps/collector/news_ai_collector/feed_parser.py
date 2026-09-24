"""Small, dependency-light RSS/Atom parser for the initial collector.

The parser uses defusedxml and accepts only already-bounded response bytes. It is intentionally
conservative: malformed entries are skipped by the caller instead of inventing missing fields.
"""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from news_ai_common import (
    MAX_FEED_MEDIA_CANDIDATES,
    CollectedMediaCandidate,
    FeedMediaOrigin,
    FeedMediaType,
)
from pydantic import ValidationError

_MEDIA_RSS_NAMESPACE = "http://search.yahoo.com/mrss/"


class FeedParseError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _namespace(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _children(element: Element, name: str) -> list[Element]:
    wanted = name.lower()
    return [child for child in list(element) if _local_name(child.tag) == wanted]


def _first_child(element: Element, *names: str) -> Element | None:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return child
    return None


def _text(element: Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _body(element: Element | None) -> str | None:
    if element is None or element.attrib.get("src"):
        return None
    value = (element.text or "") + "".join(
        ElementTree.tostring(child, encoding="unicode") for child in element
    )
    return value.strip() or None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _atom_link(entry: Element, base_url: str) -> str | None:
    fallback: str | None = None
    for link in _children(entry, "link"):
        href = (link.attrib.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(base_url, href)
        rel = (link.attrib.get("rel") or "alternate").lower()
        if rel == "alternate":
            return absolute
        fallback = fallback or absolute
    return fallback


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _media_type(*, mime_type: str | None, medium: str | None) -> FeedMediaType | None:
    normalized_mime = (mime_type or "").strip().lower()
    normalized_medium = (medium or "").strip().lower()
    if normalized_medium == "image" or normalized_mime.startswith("image/"):
        return FeedMediaType.IMAGE
    if normalized_medium == "video" or normalized_mime.startswith("video/"):
        return FeedMediaType.VIDEO
    return None


def _media_child(element: Element, name: str) -> Element | None:
    for child in list(element):
        if _namespace(child.tag) == _MEDIA_RSS_NAMESPACE and _local_name(child.tag) == name:
            return child
    return None


def _media_context(parent: Element, node: Element | None = None) -> dict[str, str | None]:
    def value(name: str) -> tuple[str | None, str | None]:
        child = _media_child(node, name) if node is not None else None
        if child is None:
            child = _media_child(parent, name)
        href = child.attrib.get("href") if child is not None else None
        return _text(child), href

    credit, _ = value("credit")
    copyright_notice, _ = value("copyright")
    license_text, license_url = value("license")
    return {
        "credit": credit,
        "copyright_notice": copyright_notice,
        "license_text": license_text,
        "license_url": license_url,
    }


def _media_candidate(
    node: Element,
    *,
    base_url: str,
    origin: FeedMediaOrigin,
    context: dict[str, str | None],
) -> dict[str, object] | None:
    raw_url = (node.attrib.get("url") or node.attrib.get("href") or "").strip()
    mime_type = (node.attrib.get("type") or "").strip() or None
    media_type = _media_type(mime_type=mime_type, medium=node.attrib.get("medium"))
    if origin is FeedMediaOrigin.MEDIA_THUMBNAIL and media_type is None:
        media_type = FeedMediaType.IMAGE
    if not raw_url or media_type is None:
        return None
    license_url = context.get("license_url")
    raw_candidate = {
        "url": urljoin(base_url, raw_url),
        "media_type": media_type,
        "origin": origin,
        "mime_type": mime_type,
        "width": _optional_int(node.attrib.get("width")),
        "height": _optional_int(node.attrib.get("height")),
        "duration_seconds": _optional_int(node.attrib.get("duration")),
        "title": _text(_media_child(node, "title")),
        "description": _text(_media_child(node, "description")),
        "credit": context.get("credit"),
        "copyright_notice": context.get("copyright_notice"),
        "license_url": urljoin(base_url, license_url) if license_url else None,
        "license_text": context.get("license_text"),
        "reuse_status": "UNASSESSED",
    }
    try:
        candidate = CollectedMediaCandidate.model_validate(raw_candidate)
    except ValidationError:
        return None
    return candidate.model_dump(mode="json")


def _feed_media(entry: Element, *, base_url: str, atom: bool) -> list[dict[str, object]]:
    nodes: list[tuple[Element, FeedMediaOrigin, dict[str, str | None]]] = []

    def collect(parent: Element, *, allow_enclosures: bool) -> None:
        for node in list(parent):
            namespace = _namespace(node.tag)
            name = _local_name(node.tag)
            origin: FeedMediaOrigin | None = None
            if namespace == _MEDIA_RSS_NAMESPACE and name == "content":
                origin = FeedMediaOrigin.MEDIA_CONTENT
            elif namespace == _MEDIA_RSS_NAMESPACE and name == "thumbnail":
                origin = FeedMediaOrigin.MEDIA_THUMBNAIL
            elif allow_enclosures and name == "enclosure" and not atom:
                origin = FeedMediaOrigin.RSS_ENCLOSURE
            elif (
                allow_enclosures
                and atom
                and name == "link"
                and (node.attrib.get("rel") or "").lower() == "enclosure"
            ):
                origin = FeedMediaOrigin.ATOM_ENCLOSURE
            if origin is not None:
                nodes.append((node, origin, _media_context(parent, node)))

    collect(entry, allow_enclosures=True)
    for group in list(entry):
        if _namespace(group.tag) == _MEDIA_RSS_NAMESPACE and _local_name(group.tag) == "group":
            collect(group, allow_enclosures=False)

    priority = {
        FeedMediaOrigin.MEDIA_CONTENT: 0,
        FeedMediaOrigin.RSS_ENCLOSURE: 1,
        FeedMediaOrigin.ATOM_ENCLOSURE: 1,
        FeedMediaOrigin.MEDIA_THUMBNAIL: 2,
    }
    ordered = sorted(enumerate(nodes), key=lambda item: (priority[item[1][1]], item[0]))
    candidates: list[dict[str, object]] = []
    seen: set[tuple[str, FeedMediaType]] = set()
    for _, (node, origin, context) in ordered:
        candidate = _media_candidate(node, base_url=base_url, origin=origin, context=context)
        if candidate is None:
            continue
        identity = (str(candidate["url"]), candidate["media_type"])
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(candidate)
        if len(candidates) == MAX_FEED_MEDIA_CANDIDATES:
            break
    return candidates


def _rss_body_node(item: Element) -> Element | None:
    for child in list(item):
        if _local_name(child.tag) == "encoded":
            return child
    for child in list(item):
        if _local_name(child.tag) == "content" and _namespace(child.tag) != _MEDIA_RSS_NAMESPACE:
            return child
    return None


def _rss_item(item: Element, base_url: str) -> dict[str, object] | None:
    title = _text(_first_child(item, "title"))
    link = _text(_first_child(item, "link"))
    if not title or not link:
        return None
    return {
        "title": title,
        "url": urljoin(base_url, link),
        "author": _text(_first_child(item, "author", "creator")),
        "published_at": _parse_datetime(
            _text(_first_child(item, "pubdate", "published", "updated", "date"))
        ),
        "summary": _text(_first_child(item, "description", "summary")),
        "body": _body(_rss_body_node(item)),
        "external_id": _text(_first_child(item, "guid", "id")),
        "media_candidates": _feed_media(item, base_url=base_url, atom=False),
    }


def _atom_entry(entry: Element, base_url: str) -> dict[str, object] | None:
    title = _text(_first_child(entry, "title"))
    link = _atom_link(entry, base_url)
    if not title or not link:
        return None
    author_node = _first_child(entry, "author")
    author = _text(_first_child(author_node, "name")) if author_node is not None else None
    return {
        "title": title,
        "url": link,
        "author": author,
        "published_at": _parse_datetime(
            _text(_first_child(entry, "published", "updated", "issued", "date"))
        ),
        "summary": _text(_first_child(entry, "summary")),
        "body": _body(_first_child(entry, "content")),
        "external_id": _text(_first_child(entry, "id")),
        "media_candidates": _feed_media(entry, base_url=base_url, atom=True),
    }


def parse_feed(content: bytes, *, base_url: str) -> tuple[list[dict[str, object]], list[str]]:
    try:
        root = ElementTree.fromstring(
            content,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise FeedParseError(f"invalid or unsafe feed XML: {type(exc).__name__}") from exc

    root_name = _local_name(root.tag)
    warnings: list[str] = []
    entries: list[dict[str, object]] = []

    if root_name == "rss":
        channel = _first_child(root, "channel")
        items = _children(channel, "item") if channel is not None else []
    elif root_name == "rdf":
        items = _children(root, "item")
    else:
        items = []

    if root_name in {"rss", "rdf"}:
        for item in items:
            parsed = _rss_item(item, base_url)
            if parsed is None:
                warnings.append("skipped RSS item missing title or link")
            else:
                entries.append(parsed)
        return entries, warnings

    if root_name == "feed":
        for entry in _children(root, "entry"):
            parsed = _atom_entry(entry, base_url)
            if parsed is None:
                warnings.append("skipped Atom entry missing title or link")
            else:
                entries.append(parsed)
        return entries, warnings

    raise FeedParseError(f"unsupported feed root element: {root_name}")
