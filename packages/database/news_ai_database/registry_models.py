"""Stable identities for config-managed collection sources and feeds.

The general ``sources`` table is also used by research/evidence. These mapping tables let collection
configuration reconcile only the records it owns without imposing config-specific identity on all
sources in the system.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SourceRegistryEntry(Base):
    __tablename__ = "source_registry_entries"

    source_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SourceFeedRegistryEntry(Base):
    __tablename__ = "source_feed_registry_entries"

    feed_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_feed_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_feeds.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
