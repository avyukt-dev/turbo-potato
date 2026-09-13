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
