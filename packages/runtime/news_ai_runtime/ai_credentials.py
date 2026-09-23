"""Secret-safe operator access to durable AI credential health."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import groq
import openai
from google import genai
from google.genai import errors as google_errors
from news_ai_ai import AIProvidersConfigLoader
from news_ai_ai.credentials import (
    CredentialPoolEmptyError,
    CredentialStateUnavailableError,
    DatabaseCredentialPool,
    resolve_credential_pool,
)
from news_ai_common.config import ConfigLoader
from news_ai_database import AICredential, AICredentialReason, AICredentialState, AuditLog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .controller import RuntimeOperationError


class CredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pool_id: str
    provider: str
    slot: int
    state: AICredentialState
    reason_code: AICredentialReason | None
    cooldown_remaining_seconds: float | None
    consecutive_failures: int
    last_used_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    revision: int
    active: bool


class AICredentialOperator:
    def __init__(
        self,
        loader: ConfigLoader,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = AIProvidersConfigLoader(loader).load()
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    async def status(self) -> tuple[CredentialStatus, ...]:
        await self._synchronize()
        return await asyncio.to_thread(self._status_sync)

    async def reset(self, pool_id: str, slot: int, reason: str) -> CredentialStatus:
        pool = self._pool(pool_id)
        try:
            configured = resolve_credential_pool(pool)
        except CredentialPoolEmptyError:
            raise RuntimeOperationError("CREDENTIAL_NOT_CONFIGURED") from None
        safe_reason = self._safe_reason(reason, secrets=tuple(item.secret for item in configured))
        if not safe_reason:
            raise RuntimeOperationError("REASON_REQUIRED")
        current = {item.slot: item for item in configured}.get(slot)
        if current is None:
            raise RuntimeOperationError("CREDENTIAL_NOT_CONFIGURED")
        await self._synchronize()
        try:
            with self.session_factory() as session, session.begin():
                row = session.scalar(
                    select(AICredential)
                    .where(AICredential.pool_id == pool_id, AICredential.slot == slot)
                    .with_for_update()
                )
                if row is None or row.secret_fingerprint != current.fingerprint:
                    raise RuntimeOperationError("CREDENTIAL_STATE_CHANGED")
                row.state = AICredentialState.HEALTHY
                row.reason_code = AICredentialReason.OPERATOR_RESET
                row.cooldown_until = None
                row.consecutive_failures = 0
                row.revision += 1
                session.add(
                    AuditLog(
                        actor_id=None,
                        action="AI_CREDENTIAL_RESET",
                        artifact_type="ai_credential",
                        artifact_id=row.id,
                        artifact_version=row.revision,
                        result="SUCCESS",
                        reason=safe_reason,
                        audit_metadata={"pool_id": pool_id, "slot": slot},
                        created_at=self.clock(),
                    )
                )
        except RuntimeOperationError:
            raise
        except Exception:
            raise RuntimeOperationError("CREDENTIAL_STATE_UNAVAILABLE") from None
        statuses = await self.status()
        return next(item for item in statuses if item.pool_id == pool_id and item.slot == slot)

    async def probe(self, pool_id: str, slot: int) -> None:
        pool = self._pool(pool_id)
        if slot < 1:
            raise RuntimeOperationError("INVALID_CREDENTIAL_SLOT")
        try:
            configured = resolve_credential_pool(pool)
        except CredentialPoolEmptyError:
            raise RuntimeOperationError("CREDENTIAL_NOT_CONFIGURED") from None
        credential = {item.slot: item for item in configured}.get(slot)
        if credential is None:
            raise RuntimeOperationError("CREDENTIAL_NOT_CONFIGURED")
        durable = DatabaseCredentialPool(pool, configured, self.session_factory)
        try:
            await durable.synchronize()
            if pool.provider == "groq":
                client = groq.AsyncGroq(api_key=credential.secret, max_retries=0)
                try:
                    await client.models.list()
                finally:
                    await client.close()
            elif pool.provider == "openai":
                client = openai.AsyncOpenAI(api_key=credential.secret, max_retries=0)
                try:
                    await client.models.list()
                finally:
                    await client.close()
            elif pool.provider == "gemini":
                client = genai.Client(api_key=credential.secret)
                try:
                    await client.aio.models.list()
                finally:
                    await client.aio.aclose()
            else:
                raise RuntimeOperationError("PROBE_UNSUPPORTED")
        except (groq.AuthenticationError, openai.AuthenticationError):
            await durable.record_auth_failure(credential)
            raise RuntimeOperationError("AUTHENTICATION") from None
        except (groq.RateLimitError, openai.RateLimitError):
            await durable.record_rate_limit(credential, None)
            raise RuntimeOperationError("RATE_LIMIT") from None
        except (groq.PermissionDeniedError, openai.PermissionDeniedError):
            raise RuntimeOperationError("CREDENTIAL_PROBE_DENIED") from None
        except (
            groq.APITimeoutError,
            groq.APIConnectionError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            TimeoutError,
            OSError,
        ):
            raise RuntimeOperationError("CREDENTIAL_PROBE_UNAVAILABLE") from None
        except (groq.APIStatusError, openai.APIStatusError) as exc:
            code = getattr(exc, "status_code", None)
            error = (
                "CREDENTIAL_PROBE_UNAVAILABLE"
                if code and code >= 500
                else "CREDENTIAL_PROBE_REJECTED"
            )
            raise RuntimeOperationError(error) from None
        except google_errors.APIError as exc:
            if exc.code == 401:
                await durable.record_auth_failure(credential)
                raise RuntimeOperationError("AUTHENTICATION") from None
            if exc.code == 429:
                await durable.record_rate_limit(credential, None)
                raise RuntimeOperationError("RATE_LIMIT") from None
            if exc.code == 403:
                raise RuntimeOperationError("CREDENTIAL_PROBE_DENIED") from None
            if exc.code in {408, 504} or exc.code >= 500:
                raise RuntimeOperationError("CREDENTIAL_PROBE_UNAVAILABLE") from None
            raise RuntimeOperationError("CREDENTIAL_PROBE_REJECTED") from None
        except RuntimeOperationError:
            raise
        except CredentialStateUnavailableError:
            raise RuntimeOperationError("CREDENTIAL_STATE_UNAVAILABLE") from None
        except Exception:
            await durable.record_unknown(credential)
            raise RuntimeOperationError("CREDENTIAL_PROBE_UNKNOWN") from None
        await durable.record_success(credential)

    async def _synchronize(self) -> None:
        for pool in self.config.credential_pools:
            try:
                resolved = resolve_credential_pool(pool)
            except CredentialPoolEmptyError:
                resolved = ()
            await DatabaseCredentialPool(pool, resolved, self.session_factory).synchronize()

    def _status_sync(self) -> tuple[CredentialStatus, ...]:
        now = self._aware(self.clock())
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(AICredential).order_by(AICredential.pool_id, AICredential.slot)
                ).all()
            return tuple(
                CredentialStatus(
                    pool_id=row.pool_id,
                    provider=row.provider_type,
                    slot=row.slot,
                    state=row.state,
                    reason_code=row.reason_code,
                    cooldown_remaining_seconds=(
                        max(0.0, (self._aware(row.cooldown_until) - now).total_seconds())
                        if row.cooldown_until is not None
                        else None
                    ),
                    consecutive_failures=row.consecutive_failures,
                    last_used_at=row.last_used_at,
                    last_success_at=row.last_success_at,
                    last_failure_at=row.last_failure_at,
                    revision=row.revision,
                    active=row.is_active,
                )
                for row in rows
            )
        except Exception:
            raise RuntimeOperationError("CREDENTIAL_STATE_UNAVAILABLE") from None

    def _pool(self, pool_id: str):
        for pool in self.config.credential_pools:
            if pool.pool_id == pool_id:
                return pool
        raise RuntimeOperationError("CREDENTIAL_POOL_UNKNOWN")

    @staticmethod
    def _safe_reason(reason: str, *, secrets: tuple[str, ...]) -> str:
        value = reason
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        return " ".join(value.split())[:512]

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
