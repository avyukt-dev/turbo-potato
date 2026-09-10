from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from news_ai_processor import ArticleNormalizationInput, ArticleNormalizer, canonicalize_url
from pydantic import ValidationError


def _article(**overrides: object) -> ArticleNormalizationInput:
    values: dict[str, object] = {
        "source_id": uuid4(),
        "source_feed_id": uuid4(),
        "url": "HTTPS://Example.COM:443/news/../news/item/?utm_source=x&b=2&a=1#section",
        "title": "  Example   headline  ",
        "author": "  Jane   Doe ",
        "published_at": datetime(
            2026,
            9,
            10,
            8,
            0,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
        "language": "EN_us",
        "summary": "First line.\n\n\nSecond   line.",
        "body": "Paragraph  one.\r\n\r\n\r\nParagraph\t two.",
        "external_id": "  item-123  ",
        "retrieved_at": datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return ArticleNormalizationInput(**values)


def test_canonicalize_url_removes_fragment_tracking_and_default_port() -> None:
    assert (
        canonicalize_url(
            "HTTPS://Example.COM:443/news/../news/item/?utm_source=x&b=2&a=1#section"
        )
        == "https://example.com/news/item/?a=1&b=2"
    )


def test_canonicalize_url_preserves_semantic_query_parameters() -> None:
    assert canonicalize_url("https://example.com/search?q=india&page=2&gclid=tracking") == (
        "https://example.com/search?page=2&q=india"
    )


def test_canonicalize_url_handles_idna_hostnames() -> None:
    assert canonicalize_url("https://münich.example/path") == "https://xn--mnich-kva.example/path"


def test_canonicalize_url_preserves_existing_percent_encoding() -> None:
    assert canonicalize_url("https://example.com/a%20b?q=x%20y") == (
        "https://example.com/a%20b?q=x+y"
    )


def test_canonicalize_url_preserves_duplicate_path_slashes() -> None:
    assert canonicalize_url("https://example.com/a//b/./c") == "https://example.com/a//b/c"


def test_normalizer_cleans_text_language_and_timestamps() -> None:
    result = ArticleNormalizer().normalize(_article())

    assert result.title == "Example headline"
    assert result.author == "Jane Doe"
    assert result.language == "en-us"
    assert result.summary == "First line.\n\nSecond line."
    assert result.body == "Paragraph one.\n\nParagraph two."
    assert result.external_id == "item-123"
    assert result.published_at == datetime(2026, 9, 10, 2, 30, tzinfo=UTC)
    assert result.retrieved_at == datetime(2026, 9, 10, 3, 0, tzinfo=UTC)


def test_content_hash_is_stable_for_equivalent_normalized_content() -> None:
    normalizer = ArticleNormalizer()
    first = normalizer.normalize(_article(title="Example   headline", body="A   body"))
    second = normalizer.normalize(_article(title=" Example headline ", body="A body"))

    assert first.content_hash == second.content_hash


def test_content_hash_changes_when_material_content_changes() -> None:
    normalizer = ArticleNormalizer()
    first = normalizer.normalize(_article(body="Version one"))
    second = normalizer.normalize(_article(body="Version two"))

    assert first.content_hash != second.content_hash


def test_normalization_decodes_html_entities_without_interpreting_markup() -> None:
    result = ArticleNormalizer().normalize(_article(title="India &amp; World <b>News</b>"))

    assert result.title == "India & World <b>News</b>"


def test_invalid_language_is_rejected() -> None:
    with pytest.raises(ValueError, match="language"):
        ArticleNormalizer().normalize(_article(language="english (India)"))


def test_naive_timestamps_are_rejected_at_input_boundary() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _article(published_at=datetime(2026, 9, 10, 8, 0))


def test_embedded_url_credentials_are_rejected() -> None:
    with pytest.raises(ValidationError, match="embedded credentials"):
        _article(url="https://user:password@example.com/story")


def test_empty_title_after_normalization_is_rejected() -> None:
    with pytest.raises(ValueError, match="title is empty"):
        ArticleNormalizer().normalize(_article(title="\x00\t   "))
