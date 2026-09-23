from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from news_ai_ai.credentials import (
    CredentialPoolConfig,
    DatabaseCredentialPool,
    resolve_credential_pool,
)
from news_ai_database import AICredential, create_database_engine, create_session_factory
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    not os.getenv("NEWS_AI_DATABASE_URL"), reason="PostgreSQL integration URL is required"
)


def test_postgres_concurrent_selection_spreads_across_eligible_keys() -> None:
    engine = create_database_engine(os.environ["NEWS_AI_DATABASE_URL"])
    factory = create_session_factory(engine)
    config = CredentialPoolConfig(
        pool_id="integration-concurrent",
        provider="groq",
        env_prefix="INTEGRATION_GROQ_KEY",
    )
    credentials = resolve_credential_pool(
        config,
        environ={"INTEGRATION_GROQ_KEY_1": "first", "INTEGRATION_GROQ_KEY_2": "second"},
    )
    try:
        with factory() as session, session.begin():
            session.execute(delete(AICredential).where(AICredential.pool_id == config.pool_id))
        pools = [DatabaseCredentialPool(config, credentials, factory) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            selected = list(executor.map(lambda pool: asyncio.run(pool.acquire(set())).slot, pools))
        assert set(selected) == {1, 2}
    finally:
        with factory() as session, session.begin():
            session.execute(delete(AICredential).where(AICredential.pool_id == config.pool_id))
        engine.dispose()
