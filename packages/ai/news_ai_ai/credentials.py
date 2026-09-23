"""Secret-safe credential discovery and durable per-key operational state."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from news_ai_database import AICredential, AICredentialReason, AICredentialState
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session


class CredentialStateUnavailableError(RuntimeError):
    """Durable credential state could not be read or updated safely."""


class CredentialPoolEmptyError(ValueError):
    """A configured pool currently has no environment-backed credentials."""


class CredentialPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pool_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=32)
    env_prefix: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=96)
    max_credentials: int = Field(default=32, ge=1, le=128)
    cooldown_seconds: float = Field(default=60.0, gt=0, le=3600)
    max_retry_after_seconds: float = Field(default=60.0, ge=0, le=300)

    @field_validator("env_prefix")
    @classmethod
    def forbid_numbered_prefix(cls, value: str) -> str:
        if re.search(r"_[1-9][0-9]*$", value):
            raise ValueError("credential pool prefix must not include a slot number")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    pool_id: str
    provider_type: str
    slot: int
    fingerprint: str
    secret: str

    def __repr__(self) -> str:
        return (
            "ResolvedCredential("
            f"pool_id={self.pool_id!r}, provider_type={self.provider_type!r}, "
            f"slot={self.slot}, fingerprint='<redacted>', secret='<redacted>')"
        )


def resolve_credential_pool(
    config: CredentialPoolConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[ResolvedCredential, ...]:
    """Resolve numbered secrets without ever returning their environment names as state."""

    values = os.environ if environ is None else environ
    legacy = values.get(config.env_prefix, "").strip()
    numbered_one = values.get(f"{config.env_prefix}_1", "").strip()
    if legacy and numbered_one:
        raise ValueError(
            f"credential pool {config.pool_id!r} cannot configure both legacy and slot 1"
        )
    slots: dict[int, str] = {}
    if legacy or numbered_one:
        slots[1] = legacy or numbered_one
    pattern = re.compile(rf"^{re.escape(config.env_prefix)}_([1-9][0-9]*)$")
    for name, raw in values.items():
        match = pattern.fullmatch(name)
        if match is None:
            continue
        slot = int(match.group(1))
        if slot == 1 or slot > config.max_credentials:
            continue
        secret = raw.strip()
        if secret:
            slots[slot] = secret
    if not slots:
        raise CredentialPoolEmptyError(
            f"credential pool {config.pool_id!r} has no configured credentials"
        )
    fingerprints: set[str] = set()
    result: list[ResolvedCredential] = []
    for slot, secret in sorted(slots.items()):
        fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if fingerprint in fingerprints:
            raise ValueError(f"credential pool {config.pool_id!r} contains duplicate credentials")
        fingerprints.add(fingerprint)
        result.append(
            ResolvedCredential(
                pool_id=config.pool_id,
                provider_type=config.provider,
                slot=slot,
                fingerprint=fingerprint,
                secret=secret,
            )
        )
    return tuple(result)


class DatabaseCredentialPool:
    """Select and update credentials using short PostgreSQL transactions."""

    def __init__(
        self,
        config: CredentialPoolConfig,
        credentials: tuple[ResolvedCredential, ...],
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._credentials = {item.slot: item for item in credentials}
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._synchronized = False
        self._sync_lock = asyncio.Lock()

    async def acquire(self, attempted: set[int]) -> ResolvedCredential | None:
        await self._ensure_synchronized()
        return await asyncio.to_thread(self._acquire_sync, attempted)

    async def synchronize(self) -> None:
        await self._ensure_synchronized()

    async def record_success(self, credential: ResolvedCredential) -> None:
        await asyncio.to_thread(self._record_sync, credential, "SUCCESS", None)

    async def record_rate_limit(
        self, credential: ResolvedCredential, retry_after_seconds: float | None
    ) -> None:
        bounded = (
            min(retry_after_seconds, self.config.max_retry_after_seconds)
            if retry_after_seconds is not None
            else self.config.cooldown_seconds
        )
        await asyncio.to_thread(self._record_sync, credential, "RATE_LIMIT", bounded)

    async def record_auth_failure(self, credential: ResolvedCredential) -> None:
        await asyncio.to_thread(self._record_sync, credential, "AUTHENTICATION", None)

    async def record_unknown(self, credential: ResolvedCredential) -> None:
        await asyncio.to_thread(self._record_sync, credential, "UNKNOWN", None)

    async def minimum_cooldown_remaining(self) -> float | None:
        await self._ensure_synchronized()
        return await asyncio.to_thread(self._minimum_cooldown_sync)

    async def _ensure_synchronized(self) -> None:
        if self._synchronized:
            return
        async with self._sync_lock:
            if self._synchronized:
                return
            await asyncio.to_thread(self._synchronize_sync)
            self._synchronized = True

    def _synchronize_sync(self) -> None:
        for attempt in range(2):
            try:
                self._synchronize_once()
                return
            except IntegrityError:
                if attempt:
                    raise CredentialStateUnavailableError(
                        "AI credential state is unavailable"
                    ) from None
        raise CredentialStateUnavailableError("AI credential state is unavailable")

    def _synchronize_once(self) -> None:
        try:
            with self._session_factory() as session, session.begin():
                rows = {
                    row.slot: row
                    for row in session.scalars(
                        select(AICredential)
                        .where(AICredential.pool_id == self.config.pool_id)
                        .with_for_update()
                    )
                }
                for slot, credential in self._credentials.items():
                    row = rows.get(slot)
                    if row is None:
                        session.add(
                            AICredential(
                                pool_id=self.config.pool_id,
                                provider_type=self.config.provider,
                                slot=slot,
                                secret_fingerprint=credential.fingerprint,
                                state=AICredentialState.HEALTHY,
                                consecutive_failures=0,
                                revision=1,
                                is_active=True,
                            )
                        )
                    elif (
                        row.secret_fingerprint != credential.fingerprint
                        or row.provider_type != self.config.provider
                    ):
                        row.provider_type = self.config.provider
                        row.secret_fingerprint = credential.fingerprint
                        row.state = AICredentialState.HEALTHY
                        row.reason_code = AICredentialReason.SECRET_REPLACED
                        row.cooldown_until = None
                        row.consecutive_failures = 0
                        row.last_used_at = None
                        row.last_success_at = None
                        row.last_failure_at = None
                        row.is_active = True
                        row.revision += 1
                    else:
                        row.is_active = True
                for slot, row in rows.items():
                    if slot not in self._credentials and row.is_active:
                        row.is_active = False
                        row.revision += 1
        except IntegrityError:
            raise
        except (SQLAlchemyError, OSError):
            raise CredentialStateUnavailableError("AI credential state is unavailable") from None

    def _acquire_sync(self, attempted: set[int]) -> ResolvedCredential | None:
        for _ in range(5):
            credential, retry_locked = self._acquire_once(attempted)
            if credential is not None or not retry_locked:
                return credential
            time.sleep(0.01)
        return None

    def _acquire_once(self, attempted: set[int]) -> tuple[ResolvedCredential | None, bool]:
        now = self._clock()
        try:
            with self._session_factory() as session, session.begin():
                session.execute(
                    update(AICredential)
                    .where(
                        AICredential.pool_id == self.config.pool_id,
                        AICredential.is_active.is_(True),
                        AICredential.state == AICredentialState.COOLDOWN,
                        AICredential.cooldown_until <= now,
                    )
                    .values(
                        state=AICredentialState.HEALTHY,
                        reason_code=None,
                        cooldown_until=None,
                        revision=AICredential.revision + 1,
                    )
                )
                conditions = [
                    AICredential.pool_id == self.config.pool_id,
                    AICredential.is_active.is_(True),
                    AICredential.state == AICredentialState.HEALTHY,
                ]
                if attempted:
                    conditions.append(AICredential.slot.not_in(attempted))
                row = session.scalar(
                    select(AICredential)
                    .where(*conditions)
                    .order_by(
                        AICredential.last_used_at.asc().nullsfirst(),
                        AICredential.slot.asc(),
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    visible = session.scalar(select(AICredential.id).where(*conditions).limit(1))
                    return None, visible is not None
                credential = self._credentials.get(row.slot)
                if credential is None or row.secret_fingerprint != credential.fingerprint:
                    return None, False
                row.last_used_at = now
                row.revision += 1
                return credential, False
        except (SQLAlchemyError, OSError, TypeError):
            raise CredentialStateUnavailableError("AI credential state is unavailable") from None

    def _record_sync(
        self,
        credential: ResolvedCredential,
        outcome: str,
        cooldown_seconds: float | None,
    ) -> None:
        now = self._clock()
        try:
            with self._session_factory() as session, session.begin():
                row = session.scalar(
                    select(AICredential)
                    .where(
                        AICredential.pool_id == credential.pool_id,
                        AICredential.slot == credential.slot,
                    )
                    .with_for_update()
                )
                if row is None or row.secret_fingerprint != credential.fingerprint:
                    raise CredentialStateUnavailableError("AI credential state changed")
                if outcome == "SUCCESS":
                    row.state = AICredentialState.HEALTHY
                    row.reason_code = None
                    row.cooldown_until = None
                    row.consecutive_failures = 0
                    row.last_success_at = now
                elif outcome == "RATE_LIMIT":
                    row.state = AICredentialState.COOLDOWN
                    row.reason_code = AICredentialReason.RATE_LIMIT
                    row.cooldown_until = now + timedelta(seconds=cooldown_seconds or 0)
                    row.consecutive_failures += 1
                    row.last_failure_at = now
                elif outcome == "AUTHENTICATION":
                    row.state = AICredentialState.AUTH_FAILED
                    row.reason_code = AICredentialReason.AUTHENTICATION
                    row.cooldown_until = None
                    row.consecutive_failures += 1
                    row.last_failure_at = now
                else:
                    row.state = AICredentialState.UNKNOWN
                    row.reason_code = AICredentialReason.UNKNOWN_CREDENTIAL_FAILURE
                    row.cooldown_until = None
                    row.consecutive_failures += 1
                    row.last_failure_at = now
                row.revision += 1
        except CredentialStateUnavailableError:
            raise
        except (SQLAlchemyError, OSError):
            raise CredentialStateUnavailableError("AI credential state is unavailable") from None

    def _minimum_cooldown_sync(self) -> float | None:
        now = self._clock()
        try:
            with self._session_factory() as session:
                values = session.scalars(
                    select(AICredential.cooldown_until).where(
                        AICredential.pool_id == self.config.pool_id,
                        AICredential.is_active.is_(True),
                        AICredential.state == AICredentialState.COOLDOWN,
                        AICredential.cooldown_until > now,
                    )
                ).all()
            remaining = [
                (aware - now).total_seconds()
                for value in values
                if (aware := self._aware(value)) is not None
            ]
            return min(remaining) if remaining else None
        except (SQLAlchemyError, OSError, TypeError):
            raise CredentialStateUnavailableError("AI credential state is unavailable") from None

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


class MemoryCredentialPool:
    """Process-local test/development pool; production composition uses PostgreSQL."""

    def __init__(
        self,
        config: CredentialPoolConfig,
        credentials: tuple[ResolvedCredential, ...],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._credentials = credentials
        self._clock = clock or (lambda: datetime.now(UTC))
        self._states: dict[int, tuple[AICredentialState, datetime | None]] = {
            item.slot: (AICredentialState.HEALTHY, None) for item in credentials
        }
        self._last_used: dict[int, int] = {item.slot: 0 for item in credentials}
        self._counter = 0
        self._lock = asyncio.Lock()

    async def acquire(self, attempted: set[int]) -> ResolvedCredential | None:
        async with self._lock:
            now = self._clock()
            eligible: list[ResolvedCredential] = []
            for credential in self._credentials:
                state, until = self._states[credential.slot]
                if state is AICredentialState.COOLDOWN and until is not None and until <= now:
                    self._states[credential.slot] = (AICredentialState.HEALTHY, None)
                    state = AICredentialState.HEALTHY
                if credential.slot not in attempted and state is AICredentialState.HEALTHY:
                    eligible.append(credential)
            if not eligible:
                return None
            selected = min(eligible, key=lambda item: (self._last_used[item.slot], item.slot))
            self._counter += 1
            self._last_used[selected.slot] = self._counter
            return selected

    async def record_success(self, credential: ResolvedCredential) -> None:
        async with self._lock:
            self._states[credential.slot] = (AICredentialState.HEALTHY, None)

    async def record_rate_limit(
        self, credential: ResolvedCredential, retry_after_seconds: float | None
    ) -> None:
        delay = (
            min(retry_after_seconds, self.config.max_retry_after_seconds)
            if retry_after_seconds is not None
            else self.config.cooldown_seconds
        )
        async with self._lock:
            self._states[credential.slot] = (
                AICredentialState.COOLDOWN,
                self._clock() + timedelta(seconds=delay),
            )

    async def record_auth_failure(self, credential: ResolvedCredential) -> None:
        async with self._lock:
            self._states[credential.slot] = (AICredentialState.AUTH_FAILED, None)

    async def record_unknown(self, credential: ResolvedCredential) -> None:
        async with self._lock:
            self._states[credential.slot] = (AICredentialState.UNKNOWN, None)

    async def minimum_cooldown_remaining(self) -> float | None:
        async with self._lock:
            now = self._clock()
            values = [
                (until - now).total_seconds()
                for state, until in self._states.values()
                if state is AICredentialState.COOLDOWN and until is not None and until > now
            ]
            return min(values) if values else None
