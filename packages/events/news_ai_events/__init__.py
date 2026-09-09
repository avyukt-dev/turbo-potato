"""Typed event contracts and outbox helpers."""

from .envelope import EventEnvelope
from .types import EventType

__all__ = ["EventEnvelope", "EventType"]
