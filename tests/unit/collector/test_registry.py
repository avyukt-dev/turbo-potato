from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from news_ai_collector import (
    CollectionConfig,
    CollectionDefaults,
    FeedConfig,
    FeedRegistryConfig,
    SourceConfig,
    SourceConfigSnapshot,
    SourceRegistryConfig,
    SourceRegistryLoader,
    SourceRegistryService,
)
from news_ai_common.config import ConfigLoader
from news_ai_database import (
    Base,
    Source,
    SourceFeed,
    SourceFeedRegistryEntry,
    SourceRegistryEntry,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def _snapshot(
    *,
    source_enabled: bool = True,
    feed_enabled: bool = True,
    source_name: str = "Example News",
    feed_name: str = "Example RSS",
    source_key: str = "example",
    feed_key: str = "example.main",
) -> SourceConfigSnapshot:
    return SourceConfigSnapshot(
        registry=SourceRegistryConfig(
            schema_version=1,
            sources=[
                SourceConfig(
                    key=source_key,
                    name=source_name,
                    source_type="news",
                    domain="Example.COM.",
                    base_url="https://example.com",
                    country="IN",
                    language="EN_us",
                    description="Example publisher",
                    enabled=source_enabled,
                    metadata={"collection_tag": "primary-feed"},
                )
            ],
        ),
        feeds=FeedRegistryConfig(
            schema_version=1,
            feeds=[
                FeedConfig(
                    key=feed_key,
                    source_key=source_key,
                    name=feed_name,
                    url="https://example.com/rss.xml",
                    feed_type="rss",
                    poll_interval_seconds=600,
                    enabled=feed_enabled,
                    request_timeout_seconds=12,
                    max_response_bytes=500_000,
                    settings={"parser": "default"},
                )
            ],
        ),
        collection=CollectionConfig(
            schema_version=1,
            defaults=CollectionDefaults(
                request_timeout_seconds=20,
                max_concurrency=4,
                user_agent="news-ai-test/1",
                respect_retry_after=True,
                max_response_bytes=2_000_000,
            ),
        ),
    )


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_loader_reads_all_three_source_config_files(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "registry.yaml").write_text(
        """
schema_version: 1
sources:
  - key: example
    name: Example News
    source_type: news
    domain: example.com
    base_url: https://example.com
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (sources / "feeds.yaml").write_text(
        """
schema_version: 1
feeds:
  - key: example.main
    source_key: example
    name: Example RSS
    url: https://example.com/rss.xml
    feed_type: rss
    poll_interval_seconds: 600
""".strip(),
        encoding="utf-8",
    )
    (sources / "collection.yaml").write_text(
        """
schema_version: 1
defaults:
  request_timeout_seconds: 20
  max_concurrency: 4
  user_agent: news-ai-test/1
  respect_retry_after: true
""".strip(),
        encoding="utf-8",
    )

    snapshot = SourceRegistryLoader(ConfigLoader(tmp_path)).load()

    assert snapshot.registry.sources[0].key == "example"
    assert snapshot.registry.sources[0].source_type == "NEWS"
    assert snapshot.feeds.feeds[0].feed_type == "RSS"
    assert snapshot.collection.defaults.max_response_bytes == 2_000_000


def test_snapshot_rejects_feed_reference_to_unknown_source() -> None:
    snapshot = _snapshot()
    broken = SourceConfigSnapshot(
        registry=snapshot.registry,
        feeds=FeedRegistryConfig(
            schema_version=1,
            feeds=[
                FeedConfig(
                    key="missing.main",
                    source_key="missing",
                    name="Missing",
                    url="https://missing.example/rss.xml",
                )
            ],
        ),
        collection=snapshot.collection,
    )

    with pytest.raises(ValueError, match="unknown source keys"):
        broken.validate_references()


def test_config_rejects_duplicate_keys_and_unsafe_urls() -> None:
    with pytest.raises(ValidationError, match="source keys must be unique"):
        SourceRegistryConfig(
            schema_version=1,
            sources=[
                SourceConfig(key="same", name="A", source_type="NEWS"),
                SourceConfig(key="same", name="B", source_type="NEWS"),
            ],
        )

    with pytest.raises(ValidationError, match="embedded credentials"):
        FeedConfig(
            key="example.main",
            source_key="example",
            name="Unsafe",
            url="https://user:secret@example.com/rss.xml",
        )

    with pytest.raises(ValidationError, match="unsupported feed type"):
        FeedConfig(
            key="example.main",
            source_key="example",
            name="JSON",
            url="https://example.com/feed.json",
            feed_type="json",
        )


def test_sync_creates_stable_source_feed_and_registry_mappings(session: Session) -> None:
    result = SourceRegistryService(session).sync(_snapshot())

    assert result.sources_created == 1
    assert result.sources_updated == 0
    assert result.feeds_created == 1
    assert _count(session, Source) == 1
    assert _count(session, SourceFeed) == 1
    assert _count(session, SourceRegistryEntry) == 1
    assert _count(session, SourceFeedRegistryEntry) == 1

    source = session.scalar(select(Source))
    feed = session.scalar(select(SourceFeed))
    source_entry = session.get(SourceRegistryEntry, "example")
    feed_entry = session.get(SourceFeedRegistryEntry, "example.main")
    assert source is not None
    assert feed is not None
    assert source_entry is not None
    assert feed_entry is not None
    assert source_entry.source_id == source.id
    assert feed_entry.source_feed_id == feed.id
    assert source.domain == "example.com"
    assert source.source_type == "NEWS"
    assert source.language == "en-us"
    assert source.authority_level is None
    assert source.source_metadata["collection_config"] == {"collection_tag": "primary-feed"}
    assert feed.source_id == source.id
    assert feed.feed_type == "RSS"
    assert feed.poll_interval_seconds == 600
    assert feed.is_active is True


def test_resync_updates_owned_fields_preserves_identity_research_metadata_and_poll_state(
    session: Session,
) -> None:
    service = SourceRegistryService(session)
    service.sync(_snapshot())
    source = session.scalar(select(Source))
    feed = session.scalar(select(SourceFeed))
    assert source is not None
    assert feed is not None
    source_id = source.id
    feed_id = feed.id
    source.authority_level = 1
    source.source_metadata = {**source.source_metadata, "research_note": "preserve-me"}
    poll_time = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
    service.update_poll_state(
        feed.id,
        polled_at=poll_time,
        etag='"v1"',
        last_modified="Wed, 10 Sep 2026 04:00:00 GMT",
    )
    session.flush()

    result = service.sync(_snapshot(source_name="Renamed News", feed_name="Renamed RSS"))

    assert result.sources_created == 0
    assert result.sources_updated == 1
    assert result.feeds_created == 0
    assert result.feeds_updated == 1
    source = session.get(Source, source_id)
    feed = session.get(SourceFeed, feed_id)
    assert source is not None
    assert feed is not None
    assert source.name == "Renamed News"
    assert source.authority_level == 1
    assert source.source_metadata["research_note"] == "preserve-me"
    assert feed.name == "Renamed RSS"
    assert feed.etag == '"v1"'
    assert feed.last_modified == "Wed, 10 Sep 2026 04:00:00 GMT"
    assert feed.last_polled_at == poll_time


def test_sync_deactivates_only_missing_config_managed_records(session: Session) -> None:
    service = SourceRegistryService(session)
    service.sync(_snapshot())
    unmanaged = Source(name="Research Source", source_type="ACADEMIC", is_active=True)
    session.add(unmanaged)
    session.flush()

    empty = SourceConfigSnapshot(
        registry=SourceRegistryConfig(schema_version=1, sources=[]),
        feeds=FeedRegistryConfig(schema_version=1, feeds=[]),
        collection=CollectionConfig(schema_version=1),
    )
    result = service.sync(empty)

    managed = session.scalar(
        select(Source)
        .join(SourceRegistryEntry, SourceRegistryEntry.source_id == Source.id)
        .where(SourceRegistryEntry.source_key == "example")
    )
    feed = session.scalar(select(SourceFeed))
    assert managed is not None
    assert feed is not None
    assert managed.is_active is False
    assert feed.is_active is False
    assert unmanaged.is_active is True
    assert result.sources_deactivated == 1
    assert result.feeds_deactivated == 1


def test_disabled_source_forces_feed_inactive(session: Session) -> None:
    SourceRegistryService(session).sync(_snapshot(source_enabled=False, feed_enabled=True))

    source = session.scalar(select(Source))
    feed = session.scalar(select(SourceFeed))
    assert source is not None
    assert feed is not None
    assert source.is_active is False
    assert feed.is_active is False


def test_stable_feed_key_cannot_be_reparented(session: Session) -> None:
    service = SourceRegistryService(session)
    service.sync(_snapshot())
    second_source = SourceConfig(
        key="other",
        name="Other News",
        source_type="NEWS",
        domain="other.example",
    )
    snapshot = SourceConfigSnapshot(
        registry=SourceRegistryConfig(
            schema_version=1,
            sources=[_snapshot().registry.sources[0], second_source],
        ),
        feeds=FeedRegistryConfig(
            schema_version=1,
            feeds=[
                FeedConfig(
                    key="example.main",
                    source_key="other",
                    name="Moved",
                    url="https://other.example/rss.xml",
                )
            ],
        ),
        collection=CollectionConfig(schema_version=1),
    )

    with pytest.raises(ValueError, match="cannot be reparented"):
        service.sync(snapshot)


def test_active_feed_definitions_use_persisted_http_state_and_config_overrides(
    session: Session,
) -> None:
    snapshot = _snapshot()
    service = SourceRegistryService(session)
    service.sync(snapshot)
    feed = session.scalar(select(SourceFeed))
    assert feed is not None
    service.update_poll_state(
        feed.id,
        polled_at=datetime(2026, 9, 10, 4, 0, tzinfo=UTC),
        etag='"etag-1"',
        last_modified="Wed, 10 Sep 2026 04:00:00 GMT",
    )
    session.flush()

    definitions = service.active_feed_definitions(snapshot)

    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.source_feed_id == feed.id
    assert definition.timeout_seconds == 12
    assert definition.max_response_bytes == 500_000
    assert definition.etag == '"etag-1"'
    assert definition.last_modified == "Wed, 10 Sep 2026 04:00:00 GMT"


def test_active_feed_definitions_use_collection_defaults_when_no_override(
    session: Session,
) -> None:
    snapshot = _snapshot()
    feed_config = snapshot.feeds.feeds[0].model_copy(
        update={"request_timeout_seconds": None, "max_response_bytes": None}
    )
    snapshot = SourceConfigSnapshot(
        registry=snapshot.registry,
        feeds=FeedRegistryConfig(schema_version=1, feeds=[feed_config]),
        collection=snapshot.collection,
    )
    service = SourceRegistryService(session)
    service.sync(snapshot)

    definition = service.active_feed_definitions(snapshot)[0]

    assert definition.timeout_seconds == 20
    assert definition.max_response_bytes == 2_000_000


def test_sync_does_not_commit_caller_transaction(session: Session) -> None:
    with pytest.raises(RuntimeError, match="rollback"), session.begin():
        SourceRegistryService(session).sync(_snapshot())
        raise RuntimeError("rollback")

    assert _count(session, Source) == 0
    assert _count(session, SourceFeed) == 0
    assert _count(session, SourceRegistryEntry) == 0
    assert _count(session, SourceFeedRegistryEntry) == 0


def test_registry_mapping_ids_are_uuid_values(session: Session) -> None:
    SourceRegistryService(session).sync(_snapshot())
    source_entry = session.get(SourceRegistryEntry, "example")
    feed_entry = session.get(SourceFeedRegistryEntry, "example.main")
    assert source_entry is not None
    assert feed_entry is not None
    assert isinstance(source_entry.source_id, UUID)
    assert isinstance(feed_entry.source_feed_id, UUID)
