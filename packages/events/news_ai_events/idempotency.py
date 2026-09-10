"""Durable event-completion markers for at-least-once Redis delivery."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from news_ai_database.models import ProcessedEvent


def was_processed(session: Session, *, event_id: UUID, consumer_group: str) -> bool:
    return session.get(ProcessedEvent, (event_id, consumer_group)) is not None


def mark_processed(
    session: Session,
    *,
    event_id: UUID,
    consumer_group: str,
    result: dict[str, Any] | None = None,
) -> ProcessedEvent:
    if not consumer_group.strip():
        raise ValueError("consumer_group must not be empty")
    record = ProcessedEvent(event_id=event_id, consumer_group=consumer_group, result=result)
    session.add(record)
    return record
