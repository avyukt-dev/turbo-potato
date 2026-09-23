from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_database import AICredential, AICredentialReason, AICredentialState, AuditLog, Base
from news_ai_runtime.ai_credentials import AICredentialOperator
from news_ai_runtime.cli import main
from news_ai_runtime.controller import RuntimeOperationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _config(root: Path) -> ConfigLoader:
    path = root / "models"
    path.mkdir()
    (path / "providers.yaml").write_text(
        """schema_version: 2
credential_pools:
  - pool_id: groq-production
    provider: groq
    env_prefix: GROQ_API_KEY
  - pool_id: openai-production
    provider: openai
    env_prefix: OPENAI_API_KEY
providers:
  - provider_id: groq
    adapter_type: groq
    credential_pool_id: groq-production
    models:
      - model_id: model-1
        task_types: [CLAIM_EXTRACTION]
""",
        encoding="utf-8",
    )
    return ConfigLoader(root)


def test_operator_status_and_reset_are_secret_safe_and_audited(tmp_path: Path, monkeypatch) -> None:
    secret = "SUPER_SECRET_CREDENTIAL_SENTINEL"
    monkeypatch.setenv("GROQ_API_KEY_2", secret)
    factory = _factory()
    operator = AICredentialOperator(_config(tmp_path), factory)
    statuses = asyncio.run(operator.status())
    assert [(item.pool_id, item.slot, item.state) for item in statuses] == [
        ("groq-production", 2, AICredentialState.HEALTHY)
    ]
    assert secret not in repr(statuses)

    with factory() as session, session.begin():
        row = session.scalar(select(AICredential))
        row.state = AICredentialState.AUTH_FAILED
        row.reason_code = AICredentialReason.AUTHENTICATION
        row.consecutive_failures = 1
        row.last_failure_at = datetime.now(UTC)
    reset = asyncio.run(operator.reset("groq-production", 2, f"replace {secret}"))
    assert reset.state is AICredentialState.HEALTHY
    with factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.action == "AI_CREDENTIAL_RESET"))
    assert audit is not None
    assert audit.reason == "replace [redacted]"
    assert secret not in repr(audit.audit_metadata)


def test_reset_redacts_long_secret_before_bounding_audit_reason(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "SUPER_SECRET_" + "x" * 600
    monkeypatch.setenv("GROQ_API_KEY_1", secret)
    factory = _factory()
    operator = AICredentialOperator(_config(tmp_path), factory)

    asyncio.run(operator.reset("groq-production", 1, f"replace {secret} now"))

    with factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.action == "AI_CREDENTIAL_RESET"))
    assert audit is not None
    assert audit.reason == "replace [redacted] now"
    assert secret[:512] not in audit.reason


def test_non_generative_probe_restores_credential_without_exposing_secret(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY_1", "probe-secret")
    factory = _factory()
    operator = AICredentialOperator(_config(tmp_path), factory)
    asyncio.run(operator.status())
    with factory() as session, session.begin():
        row = session.scalar(select(AICredential))
        row.state = AICredentialState.UNKNOWN
        row.reason_code = AICredentialReason.UNKNOWN_CREDENTIAL_FAILURE

    calls: list[str] = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "probe-secret"
            self.models = self

        async def list(self):
            calls.append("models.list")

        async def close(self):
            calls.append("close")

    monkeypatch.setattr("news_ai_runtime.ai_credentials.groq.AsyncGroq", Client)
    asyncio.run(operator.probe("groq-production", 1))
    assert calls == ["models.list", "close"]
    assert asyncio.run(operator.status())[0].state is AICredentialState.HEALTHY


def test_transient_probe_failure_does_not_disable_credential(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY_1", "probe-secret")
    factory = _factory()
    operator = AICredentialOperator(_config(tmp_path), factory)

    class Client:
        def __init__(self, **_kwargs):
            self.models = self

        async def list(self):
            raise OSError("SUPER_SECRET_NETWORK_DETAIL")

        async def close(self):
            return None

    monkeypatch.setattr("news_ai_runtime.ai_credentials.groq.AsyncGroq", Client)
    with pytest.raises(RuntimeOperationError, match="CREDENTIAL_PROBE_UNAVAILABLE"):
        asyncio.run(operator.probe("groq-production", 1))

    status = asyncio.run(operator.status())[0]
    assert status.state is AICredentialState.HEALTHY
    assert status.reason_code is None


def test_newsctl_credential_commands_render_only_normalized_data(capsys) -> None:
    status = SimpleNamespace(
        model_dump=lambda **_: {
            "pool_id": "groq-production",
            "provider": "groq",
            "slot": 1,
            "state": "HEALTHY",
        }
    )

    class Operator:
        async def status(self):
            return (status,)

        async def probe(self, pool, slot):
            raise AssertionError("not called")

        async def reset(self, pool, slot, reason):
            return status

    settings = AppSettings(database_url=None)
    assert (
        main(["ai", "credentials", "status"], settings=settings, credential_operator=Operator())
        == 0
    )
    assert json.loads(capsys.readouterr().out)[0]["state"] == "HEALTHY"
    assert (
        main(
            [
                "ai",
                "credentials",
                "reset",
                "--pool",
                "groq-production",
                "--slot",
                "1",
                "--reason",
                "operator reset",
            ],
            settings=settings,
            credential_operator=Operator(),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["slot"] == 1
