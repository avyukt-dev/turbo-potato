"""Small, dependency-light RSS/Atom parser for the initial collector.

The parser uses defusedxml and accepts only already-bounded response bytes. It is intentionally
conservative: malformed entries are skipped by the caller instead of inventing missing fields.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree.ElementTree import Element

from defusedxml import ElementTree


class FeedParseError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        "summary": _text(_first_child(item, "description", "summary", "encoded", "content")),
        "external_id": _text(_first_child(item, "guid", "id")),
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
        "summary": _text(_first_child(entry, "summary", "content")),
        "external_id": _text(_first_child(entry, "id")),
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
