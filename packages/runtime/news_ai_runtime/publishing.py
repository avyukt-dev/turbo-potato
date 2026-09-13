"""Shared fail-closed control for running schedulers, publishers, and local operators."""

import os
from datetime import UTC, datetime

from news_ai_common.diagnostics import safe_text
from news_ai_database import AuditLog, RuntimeControl
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text


class PublishingControlSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    environment_pause: bool
    database_pause: bool | None
    effective_pause: bool
    available: bool
    revision: int | None = None


class DatabasePublishingControl:
    def __init__(self, factory, *, clock=None, timeout_seconds=5):
        value = os.getenv("NEWS_AI_PUBLISHING_PAUSED")
        if value is not None and value.casefold() not in {"true", "false"}:
            raise ValueError("invalid publishing pause configuration")
        self.factory = factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timeout_seconds = timeout_seconds

    def _bound(self, session):
        if session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{int(self.timeout_seconds * 1000)}ms"},
            )
            session.execute(
                text("SELECT set_config('lock_timeout', :timeout, true)"),
                {"timeout": f"{int(self.timeout_seconds * 1000)}ms"},
            )

    def snapshot(self):
        value = os.getenv("NEWS_AI_PUBLISHING_PAUSED")
        environment_pause = value is not None and value.casefold() != "false"
        try:
            with self.factory() as session:
                self._bound(session)
                row = session.scalar(
                    select(RuntimeControl).where(RuntimeControl.control_key == "PUBLISHING_PAUSED")
                )
                if row is None:
                    raise RuntimeError("missing control")
                return PublishingControlSnapshot(
                    environment_pause=environment_pause,
                    database_pause=row.boolean_value,
                    effective_pause=environment_pause or row.boolean_value,
                    available=True,
                    revision=row.revision,
                )
        except Exception:
            return PublishingControlSnapshot(
                environment_pause=environment_pause,
                database_pause=None,
                effective_pause=True,
                available=False,
            )

    def paused(self):
        return self.snapshot().effective_pause

    def set_paused(self, paused: bool, *, reason: str):
        reason = safe_text(reason)
        if not reason:
            raise ValueError("control reason is required")
        try:
            with self.factory() as session, session.begin():
                self._bound(session)
                # Serialize first creation too; normal production has a migration-seeded row.
                if session.bind.dialect.name == "postgresql":
                    session.execute(text("SELECT pg_advisory_xact_lock(27001)"))
                row = session.scalar(
                    select(RuntimeControl)
                    .where(RuntimeControl.control_key == "PUBLISHING_PAUSED")
                    .with_for_update()
                )
                created = row is None
                if row is None:
                    row = RuntimeControl(
                        control_key="PUBLISHING_PAUSED",
                        boolean_value=paused,
                        revision=1,
                        reason=reason,
                    )
                    session.add(row)
                    session.flush()
                changed = created or row.boolean_value != paused
                if not created and changed:
                    row.boolean_value = paused
                    row.revision += 1
                    row.reason = reason
                    row.updated_at = self.clock()
                if changed:
                    session.add(
                        AuditLog(
                            actor_id=None,
                            action="RUNTIME_PUBLISHING_PAUSED"
                            if paused
                            else "RUNTIME_PUBLISHING_RESUMED",
                            artifact_type="runtime_control",
                            artifact_id=row.id,
                            artifact_version=row.revision,
                            result="SUCCESS",
                            reason=reason,
                            audit_metadata={"source": "newsctl"},
                            created_at=self.clock(),
                        )
                    )
        except Exception:
            raise RuntimeError("runtime control update unavailable") from None
        return self.snapshot()
