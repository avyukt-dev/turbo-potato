import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from news_ai_database import AuditLog, RuntimeControl
from news_ai_runtime.publishing import DatabasePublishingControl
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def schema(engine):
    inspector = inspect(engine)
    return {
        table: {
            "columns": [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ],
            "checks": inspector.get_check_constraints(table),
            "unique": inspector.get_unique_constraints(table),
            "foreign_keys": inspector.get_foreign_keys(table),
            "indexes": inspector.get_indexes(table),
        }
        for table in inspector.get_table_names()
    }


def test_0012_exact_0011_schema_audit_parity_and_reupgrade():
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_runtime_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    os.environ["NEWS_AI_DATABASE_URL"] = target.render_as_string(hide_password=False)
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0011_publication_execution")
        prior = schema(engine)
        command.upgrade(config, "head")
        command.check(config)
        factory = sessionmaker(engine)
        with factory() as session:
            assert session.scalar(select(RuntimeControl)).boolean_value
        control = DatabasePublishingControl(factory)
        control.set_paused(False, reason="deployment accepted")
        control.set_paused(True, reason="maintenance")
        with factory() as session:
            assert len(list(session.scalars(select(AuditLog)))) == 2
        command.downgrade(config, "0011_publication_execution")
        assert schema(engine) == prior
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM audit_log")) == 0
        command.upgrade(config, "head")
        command.check(config)
    finally:
        os.environ["NEWS_AI_DATABASE_URL"] = url
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
