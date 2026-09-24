from datetime import UTC

import pytest
from news_ai_collector.feed_parser import FeedParseError, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>Headline</title>
      <link>/story/1</link>
      <guid>story-1</guid>
      <pubDate>Thu, 10 Sep 2026 01:00:00 GMT</pubDate>
      <description>Summary text</description>
      <author>Reporter</author>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Atom headline</title>
    <id>tag:example.com,2026:1</id>
    <link rel="alternate" href="/atom/1" />
    <published>2026-09-10T01:30:00Z</published>
    <author><name>Atom Reporter</name></author>
    <summary>Atom summary</summary>
  </entry>
</feed>
"""


def test_parses_rss_and_resolves_relative_links() -> None:
    entries, warnings = parse_feed(RSS, base_url="https://example.com/feed.xml")

    assert warnings == []
    assert len(entries) == 1
    assert entries[0]["url"] == "https://example.com/story/1"
    assert entries[0]["title"] == "Headline"
    assert entries[0]["external_id"] == "story-1"
    assert entries[0]["published_at"].tzinfo == UTC


def test_parses_atom() -> None:
    entries, warnings = parse_feed(ATOM, base_url="https://example.com/feed")

    assert warnings == []
    assert entries[0]["url"] == "https://example.com/atom/1"
    assert entries[0]["author"] == "Atom Reporter"
    assert entries[0]["summary"] == "Atom summary"


@pytest.mark.parametrize("atom", [False, True])
@pytest.mark.parametrize("summary,body", [(True, False), (False, True), (True, True)])
def test_explicit_content_is_distinct_from_discovery_summary(atom, summary, body):
    summary_xml = "<summary>Discovery context only</summary>" if summary else ""
    if atom:
        body_xml = (
            "<content type='html'>&lt;p&gt;Full publisher body&lt;/p&gt;</content>" if body else ""
        )
        xml = (
            f"<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Title</title>"
            f"<link href='/story'/>{summary_xml}{body_xml}</entry></feed>"
        )
    else:
        summary_xml = summary_xml.replace("summary", "description")
        body_xml = (
            "<content:encoded><![CDATA[<p>Full publisher body</p>]]></content:encoded>"
            if body
            else ""
        )
        xml = (
            "<rss xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><item>"
            f"<title>Title</title><link>/story</link>{summary_xml}{body_xml}</item></channel></rss>"
        )
    entries, _ = parse_feed(xml.encode(), base_url="https://example.com/feed")
    assert entries[0]["summary"] == ("Discovery context only" if summary else None)
    assert entries[0]["body"] == ("<p>Full publisher body</p>" if body else None)


def test_skips_entry_without_title_or_link() -> None:
    content = b"<rss><channel><item><title>Missing link</title></item></channel></rss>"

    entries, warnings = parse_feed(content, base_url="https://example.com/feed")

    assert entries == []
    assert warnings == ["skipped RSS item missing title or link"]


def test_rejects_dtd_and_entities() -> None:
    malicious = b"""<!DOCTYPE rss [<!ENTITY x "boom">]>
<rss><channel><item><title>&x;</title><link>https://example.com/x</link></item></channel></rss>
"""

    with pytest.raises(FeedParseError, match="invalid or unsafe feed XML"):
        parse_feed(malicious, base_url="https://example.com/feed")


MEDIA_RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Incident report</title><link>/story/incident</link>
    <content:encoded><![CDATA[<p>Full article text</p>]]></content:encoded>
    <media:group>
      <media:credit>District administration</media:credit>
      <media:copyright>Government work; verify reuse terms</media:copyright>
      <media:license href="/licenses/terms">Declared terms</media:license>
      <media:content url="/media/incident.jpg" type="image/jpeg" width="1600" height="900">
        <media:title>Incident location</media:title>
      </media:content>
      <media:content url="/media/briefing.mp4" type="video/mp4" duration="42" />
      <media:thumbnail url="/media/incident.jpg" width="320" height="180" />
    </media:group>
    <enclosure url="/media/briefing.mp4" type="video/mp4" />
    <enclosure url="/reports/study.pdf" type="application/pdf" />
  </item></channel>
</rss>
"""


def test_parses_bounded_media_rss_candidates_without_granting_reuse() -> None:
    entries, warnings = parse_feed(MEDIA_RSS, base_url="https://example.com/feed.xml")

    assert warnings == []
    assert entries[0]["body"] == "<p>Full article text</p>"
    candidates = entries[0]["media_candidates"]
    assert len(candidates) == 2
    image, video = candidates
    assert image == {
        "url": "https://example.com/media/incident.jpg",
        "media_type": "IMAGE",
        "origin": "MEDIA_CONTENT",
        "mime_type": "image/jpeg",
        "width": 1600,
        "height": 900,
        "duration_seconds": None,
        "title": "Incident location",
        "description": None,
        "credit": "District administration",
        "copyright_notice": "Government work; verify reuse terms",
        "license_url": "https://example.com/licenses/terms",
        "license_text": "Declared terms",
        "reuse_status": "UNASSESSED",
    }
    assert video["media_type"] == "VIDEO"
    assert video["duration_seconds"] == 42
    assert video["reuse_status"] == "UNASSESSED"


def test_parses_atom_enclosure_and_resolves_relative_url() -> None:
    content = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Report</title><link rel="alternate" href="/story" />
      <link rel="enclosure" href="/photo.jpg" type="image/jpeg" /></entry></feed>"""

    entries, _ = parse_feed(content, base_url="https://example.com/feed")

    assert entries[0]["media_candidates"] == [
        {
            "url": "https://example.com/photo.jpg",
            "media_type": "IMAGE",
            "origin": "ATOM_ENCLOSURE",
            "mime_type": "image/jpeg",
            "width": None,
            "height": None,
            "duration_seconds": None,
            "title": None,
            "description": None,
            "credit": None,
            "copyright_notice": None,
            "license_url": None,
            "license_text": None,
            "reuse_status": "UNASSESSED",
        }
    ]


def test_media_candidates_are_bounded_and_unknown_enclosures_are_ignored() -> None:
    enclosures = "".join(
        f'<enclosure url="/image-{index}.jpg" type="image/jpeg" />' for index in range(25)
    )
    content = (
        "<rss><channel><item><title>Report</title><link>/story</link>"
        + enclosures
        + '<enclosure url="/document.pdf" type="application/pdf" />'
        + "</item></channel></rss>"
    ).encode()

    entries, _ = parse_feed(content, base_url="https://example.com/feed")

    assert len(entries[0]["media_candidates"]) == 20
    assert all(item["media_type"] == "IMAGE" for item in entries[0]["media_candidates"])


def test_media_group_provenance_is_not_applied_to_unrelated_candidate() -> None:
    content = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
      <title>Report</title><link>/story</link>
      <media:group><media:credit>Photographer A</media:credit>
        <media:content url="/a.jpg" type="image/jpeg" /></media:group>
      <media:group><media:credit>Photographer B</media:credit>
        <media:content url="/b.jpg" type="image/jpeg" /></media:group>
    </item></channel></rss>"""

    entries, _ = parse_feed(content, base_url="https://example.com/feed")

    assert [item["credit"] for item in entries[0]["media_candidates"]] == [
        "Photographer A",
        "Photographer B",
    ]


def test_full_media_candidate_wins_over_same_url_thumbnail() -> None:
    content = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
      <title>Report</title><link>/story</link>
      <media:thumbnail url="/image.jpg" width="320" height="180" />
      <media:content url="/image.jpg" type="image/jpeg" width="1600" height="900" />
    </item></channel></rss>"""

    entries, _ = parse_feed(content, base_url="https://example.com/feed")

    assert entries[0]["media_candidates"] == [
        {
            "url": "https://example.com/image.jpg",
            "media_type": "IMAGE",
            "origin": "MEDIA_CONTENT",
            "mime_type": "image/jpeg",
            "width": 1600,
            "height": 900,
            "duration_seconds": None,
            "title": None,
            "description": None,
            "credit": None,
            "copyright_notice": None,
            "license_url": None,
            "license_text": None,
            "reuse_status": "UNASSESSED",
        }
    ]


def test_invalid_optional_media_does_not_discard_valid_article() -> None:
    content = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
      <title>Report</title><link>/story</link>
      <media:content url="https://user:secret@example.com/image.jpg" type="image/jpeg" />
      <media:content url="/valid.jpg" type="image/jpeg" width="999999" />
    </item></channel></rss>"""

    entries, warnings = parse_feed(content, base_url="https://example.com/feed")

    assert warnings == []
    assert entries[0]["title"] == "Report"
    assert entries[0]["media_candidates"] == []
