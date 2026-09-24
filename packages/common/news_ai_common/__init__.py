"""Shared infrastructure-neutral utilities for the News AI system."""

from .feed_media import (
    MAX_FEED_MEDIA_CANDIDATES,
    CollectedMediaCandidate,
    FeedMediaOrigin,
    FeedMediaType,
    MediaReuseStatus,
)

__all__ = [
    "CollectedMediaCandidate",
    "FeedMediaOrigin",
    "FeedMediaType",
    "MAX_FEED_MEDIA_CANDIDATES",
    "MediaReuseStatus",
]
