from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from news_ai_ai.credentials import (
    CredentialPoolConfig,
    DatabaseCredentialPool,
    resolve_credential_pool,
)
from news_ai_database import AICredential, AICredentialState, Base
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _config() -> CredentialPoolConfig:
    return CredentialPoolConfig(
        pool_id="groq-test",
        provider="groq",
        env_prefix="GROQ_API_KEY",
        max_credentials=8,
        cooldown_seconds=30,
        max_retry_after_seconds=60,
    )


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_numbered_pool_allows_gaps_and_legacy_but_rejects_ambiguity_and_duplicates() -> None:
    config = _config()
    resolved = resolve_credential_pool(
        config,
        environ={"GROQ_API_KEY_2": "second", "GROQ_API_KEY_7": "seventh"},
    )
    assert [item.slot for item in resolved] == [2, 7]
    assert "second" not in repr(resolved[0])

    assert resolve_credential_pool(config, environ={"GROQ_API_KEY": "legacy"})[0].slot == 1
    with pytest.raises(ValueError, match="both legacy and slot 1"):
        resolve_credential_pool(
            config,
            environ={"GROQ_API_KEY": "one", "GROQ_API_KEY_1": "other"},
        )
    with pytest.raises(ValueError, match="duplicate credentials"):
        resolve_credential_pool(
            config,
            environ={"GROQ_API_KEY_1": "same", "GROQ_API_KEY_4": "same"},
        )


def test_durable_pool_tracks_each_key_independently_and_survives_reconstruction() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    clock_value = [now]
    config = _config()
    credentials = resolve_credential_pool(
        config,
        environ={"GROQ_API_KEY_1": "first", "GROQ_API_KEY_2": "second"},
    )
    factory = _factory()
    pool = DatabaseCredentialPool(config, credentials, factory, clock=lambda: clock_value[0])

    first = asyncio.run(pool.acquire(set()))
    assert first is not None and first.slot == 1
    asyncio.run(pool.record_rate_limit(first, 20))
    second = asyncio.run(pool.acquire(set()))
    assert second is not None and second.slot == 2

    reconstructed = DatabaseCredentialPool(
        config, credentials, factory, clock=lambda: clock_value[0]
    )
    assert asyncio.run(reconstructed.acquire(set())).slot == 2
    clock_value[0] += timedelta(seconds=21)
    assert asyncio.run(reconstructed.acquire(set())).slot == 1

    with factory() as session:
        rows = session.scalars(select(AICredential).order_by(AICredential.slot)).all()
    assert rows[0].state is AICredentialState.HEALTHY
    assert rows[1].state is AICredentialState.HEALTHY


def test_auth_and_unknown_states_are_ineligible_until_secret_replacement() -> None:
    config = _config()
    first_set = resolve_credential_pool(
        config,
        environ={"GROQ_API_KEY_1": "invalid", "GROQ_API_KEY_2": "unknown"},
    )
    factory = _factory()
    pool = DatabaseCredentialPool(config, first_set, factory)
    first = asyncio.run(pool.acquire(set()))
    asyncio.run(pool.record_auth_failure(first))
    second = asyncio.run(pool.acquire(set()))
    asyncio.run(pool.record_unknown(second))
    assert asyncio.run(pool.acquire(set())) is None

    replaced = resolve_credential_pool(
        config,
        environ={"GROQ_API_KEY_1": "replacement", "GROQ_API_KEY_2": "unknown"},
    )
    new_pool = DatabaseCredentialPool(config, replaced, factory)
    asyncio.run(new_pool.synchronize())
    with factory() as session:
        replacement = session.scalar(select(AICredential).where(AICredential.slot == 1))
    assert replacement is not None
    assert replacement.last_used_at is None
    assert replacement.last_success_at is None
    assert replacement.last_failure_at is None
    selected = asyncio.run(new_pool.acquire(set()))
    assert selected is not None and selected.slot == 1


def test_removed_environment_slot_becomes_inactive_without_losing_history() -> None:
    config = _config()
    original = resolve_credential_pool(
        config,
        environ={"GROQ_API_KEY_1": "first", "GROQ_API_KEY_2": "second"},
    )
    factory = _factory()
    asyncio.run(DatabaseCredentialPool(config, original, factory).synchronize())
    remaining = resolve_credential_pool(config, environ={"GROQ_API_KEY_2": "second"})
    pool = DatabaseCredentialPool(config, remaining, factory)
    selected = asyncio.run(pool.acquire(set()))
    assert selected is not None and selected.slot == 2
    with factory() as session:
        rows = session.scalars(select(AICredential).order_by(AICredential.slot)).all()
    assert [(row.slot, row.is_active) for row in rows] == [(1, False), (2, True)]
