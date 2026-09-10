"""Typed configuration and persistence for config-managed collection sources.

`config/sources/` owns collection identity and mechanics only. Research authority or evidentiary
policy is deliberately not represented here. Existing research-owned source metadata is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_database import (
    Source,
    SourceFeed,
    SourceFeedRegistryEntry,
    SourceRegistryEntry,
)
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FeedDefinition

_KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
_SUPPORTED_FEED_TYPES = frozenset({"RSS", "ATOM"})


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=64)
    domain: str | None = Field(default=None, max_length=255)
    base_url: AnyHttpUrl | None = None
    country: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=32)
    description: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "source_type", "domain", "country", "language", "description")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("source_type")
    @classmethod
    def normalize_source_type(cls, value: str) -> str:
        return value.upper()

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        domain = value.casefold().rstrip(".")
        if "://" in domain or "/" in domain or "@" in domain:
            raise ValueError("domain must be a hostname, not a URL or credential-bearing value")
        return domain

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return value.casefold().replace("_", "-") if value else None

    @field_validator("base_url")
    @classmethod
    def reject_base_url_credentials(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and (value.username or value.password):
            raise ValueError("base_url must not contain embedded credentials")
        return value


class SourceRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_source_keys(self) -> SourceRegistryConfig:
        keys = [source.key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("source keys must be unique")
        return self


class FeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=_KEY_PATTERN)
    source_key: str = Field(min_length=1, max_length=128, pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    url: AnyHttpUrl
    feed_type: str = Field(default="RSS", min_length=1, max_length=32)
    poll_interval_seconds: int = Field(default=900, ge=60, le=86_400)
    enabled: bool = True
    request_timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    max_response_bytes: int | None = Field(default=None, ge=1_024, le=20_000_000)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("feed_type")
    @classmethod
    def normalize_feed_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _SUPPORTED_FEED_TYPES:
            raise ValueError(f"unsupported feed type: {normalized}")
        return normalized

    @field_validator("url")
    @classmethod
    def reject_feed_url_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password:
            raise ValueError("feed URL must not contain embedded credentials")
        return value


class FeedRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    feeds: list[FeedConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_feed_keys(self) -> FeedRegistryConfig:
        keys = [feed.key for feed in self.feeds]
        if len(keys) != len(set(keys)):
            raise ValueError("feed keys must be unique")
        return self


class CollectionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_timeout_seconds: float = Field(default=20, gt=0, le=60)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    user_agent: str = Field(default="news-ai-social-manager/0.1", min_length=1, max_length=255)
    respect_retry_after: bool = True
    max_response_bytes: int = Field(default=2_000_000, ge=1_024, le=20_000_000)


class CollectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    defaults: CollectionDefaults = Field(default_factory=CollectionDefaults)


@dataclass(frozen=True, slots=True)
class SourceConfigSnapshot:
    registry: SourceRegistryConfig
    feeds: FeedRegistryConfig
    collection: CollectionConfig

    def validate_references(self) -> None:
        source_keys = {source.key for source in self.registry.sources}
        missing = sorted({feed.source_key for feed in self.feeds.feeds} - source_keys)
        if missing:
            raise ValueError(f"feeds reference unknown source keys: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class RegistrySyncResult:
    sources_created: int = 0
    sources_updated: int = 0
    sources_deactivated: int = 0
    feeds_created: int = 0
    feeds_updated: int = 0
    feeds_deactivated: int = 0


class SourceRegistryLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> SourceConfigSnapshot:
        snapshot = SourceConfigSnapshot(
            registry=self.loader.load_domain_file(
                ConfigDomain.SOURCES,
                "registry.yaml",
                SourceRegistryConfig,
            ),
            feeds=self.loader.load_domain_file(
                ConfigDomain.SOURCES,
                "feeds.yaml",
                FeedRegistryConfig,
            ),
            collection=self.loader.load_domain_file(
                ConfigDomain.SOURCES,
                "collection.yaml",
                CollectionConfig,
            ),
        )
        snapshot.validate_references()
        return snapshot


class SourceRegistryService:
    """Reconcile a complete source-config snapshot into caller-owned DB transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def sync(self, snapshot: SourceConfigSnapshot) -> RegistrySyncResult:
        snapshot.validate_references()
        source_map, source_counts = self._sync_sources(snapshot.registry)
        feed_counts = self._sync_feeds(snapshot.feeds, source_map)
        return RegistrySyncResult(
            sources_created=source_counts[0],
            sources_updated=source_counts[1],
            sources_deactivated=source_counts[2],
            feeds_created=feed_counts[0],
            feeds_updated=feed_counts[1],
            feeds_deactivated=feed_counts[2],
        )

    def active_feed_definitions(self, snapshot: SourceConfigSnapshot) -> list[FeedDefinition]:
        """Build collector definitions from persisted identities plus current config/state."""

        snapshot.validate_references()
        config_by_key = {feed.key: feed for feed in snapshot.feeds.feeds}
        defaults = snapshot.collection.defaults
        rows = self.session.execute(
            select(SourceFeedRegistryEntry, SourceFeed, Source)
            .join(SourceFeed, SourceFeed.id == SourceFeedRegistryEntry.source_feed_id)
            .join(Source, Source.id == SourceFeed.source_id)
            .where(SourceFeed.is_active.is_(True), Source.is_active.is_(True))
            .order_by(SourceFeedRegistryEntry.feed_key.asc())
        )
        definitions: list[FeedDefinition] = []
        for registry_entry, feed, source in rows:
            config = config_by_key.get(registry_entry.feed_key)
            if config is None or not config.enabled or feed.feed_url is None:
                continue
            definitions.append(
                FeedDefinition(
                    source_id=source.id,
                    source_feed_id=feed.id,
                    name=feed.name,
                    url=feed.feed_url,
                    timeout_seconds=(
                        config.request_timeout_seconds or defaults.request_timeout_seconds
                    ),
                    max_response_bytes=(config.max_response_bytes or defaults.max_response_bytes),
                    etag=feed.etag,
                    last_modified=feed.last_modified,
                )
            )
        return definitions

    def update_poll_state(
        self,
        source_feed_id: Any,
        *,
        polled_at: datetime,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        feed = self.session.get(SourceFeed, source_feed_id)
        if feed is None:
            raise ValueError(f"source feed {source_feed_id} does not exist")
        feed.last_polled_at = polled_at
        feed.etag = etag
        feed.last_modified = last_modified

    def _sync_sources(
        self,
        config: SourceRegistryConfig,
    ) -> tuple[dict[str, Source], tuple[int, int, int]]:
        entries = {
            entry.source_key: entry
            for entry in self.session.scalars(select(SourceRegistryEntry).with_for_update())
        }
        configured_keys = {source.key for source in config.sources}
        source_map: dict[str, Source] = {}
        created = 0
        updated = 0
        deactivated = 0

        for source_config in config.sources:
            entry = entries.get(source_config.key)
            if entry is None:
                source = Source(name=source_config.name, source_type=source_config.source_type)
                self.session.add(source)
                self.session.flush()
                entry = SourceRegistryEntry(source_key=source_config.key, source_id=source.id)
                self.session.add(entry)
                entries[source_config.key] = entry
                created += 1
            else:
                source = self.session.get(Source, entry.source_id)
                if source is None:
                    raise RuntimeError(f"dangling source registry entry: {entry.source_key}")
                updated += 1
            self._apply_source_config(source, source_config)
            source_map[source_config.key] = source

        for key, entry in entries.items():
            if key in configured_keys:
                continue
            source = self.session.get(Source, entry.source_id)
            if source is None:
                raise RuntimeError(f"dangling source registry entry: {key}")
            if source.is_active:
                source.is_active = False
                deactivated += 1

        self.session.flush()
        return source_map, (created, updated, deactivated)

    def _sync_feeds(
        self,
        config: FeedRegistryConfig,
        source_map: dict[str, Source],
    ) -> tuple[int, int, int]:
        entries = {
            entry.feed_key: entry
            for entry in self.session.scalars(select(SourceFeedRegistryEntry).with_for_update())
        }
        configured_keys = {feed.key for feed in config.feeds}
        created = 0
        updated = 0
        deactivated = 0

        for feed_config in config.feeds:
            source = source_map[feed_config.source_key]
            entry = entries.get(feed_config.key)
            if entry is None:
                feed = SourceFeed(
                    source_id=source.id,
                    name=feed_config.name,
                    feed_type=feed_config.feed_type,
                )
                self.session.add(feed)
                self.session.flush()
                entry = SourceFeedRegistryEntry(feed_key=feed_config.key, source_feed_id=feed.id)
                self.session.add(entry)
                entries[feed_config.key] = entry
                created += 1
            else:
                feed = self.session.get(SourceFeed, entry.source_feed_id)
                if feed is None:
                    raise RuntimeError(f"dangling source-feed registry entry: {entry.feed_key}")
                if feed.source_id != source.id:
                    raise ValueError(
                        f"feed {feed_config.key!r} cannot be reparented to a different source key"
                    )
                updated += 1
            self._apply_feed_config(feed, feed_config, source_active=source.is_active)

        for key, entry in entries.items():
            if key in configured_keys:
                continue
            feed = self.session.get(SourceFeed, entry.source_feed_id)
            if feed is None:
                raise RuntimeError(f"dangling source-feed registry entry: {key}")
            if feed.is_active:
                feed.is_active = False
                deactivated += 1

        self.session.flush()
        return created, updated, deactivated

    @staticmethod
    def _apply_source_config(source: Source, config: SourceConfig) -> None:
        source.name = config.name
        source.domain = config.domain
        source.base_url = str(config.base_url) if config.base_url is not None else None
        source.source_type = config.source_type
        source.country = config.country
        source.language = config.language
        source.description = config.description
        source.is_active = config.enabled
        metadata = dict(source.source_metadata or {})
        metadata["collection_config"] = dict(config.metadata)
        metadata["managed_by_collection_config"] = True
        source.source_metadata = metadata

    @staticmethod
    def _apply_feed_config(
        feed: SourceFeed,
        config: FeedConfig,
        *,
        source_active: bool,
    ) -> None:
        feed.name = config.name
        feed.feed_url = str(config.url)
        feed.feed_type = config.feed_type
        feed.poll_interval_seconds = config.poll_interval_seconds
        feed.is_active = config.enabled and source_active
        feed.configuration = {
            "request_timeout_seconds": config.request_timeout_seconds,
            "max_response_bytes": config.max_response_bytes,
            "settings": dict(config.settings),
        }
