import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def test_0011_true_0010_downgrade_and_reupgrade():
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_execution_migration_{uuid4().hex}"
    target = base.set(database=name)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(target)
    os.environ["NEWS_AI_DATABASE_URL"] = target.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0010_publication_scheduler")
        before = inspect(engine)
        prior_columns = {column["name"] for column in before.get_columns("publications")}
        prior_checks = {
            check["name"]: check["sqltext"] for check in before.get_check_constraints("audit_log")
        }
        command.upgrade(config, "head")
        command.check(config)
        inspector = inspect(engine)
        assert "publication_attempts" in inspector.get_table_names()
        assert "publication_retry_operations" in inspector.get_table_names()
        legacy_id, execution_id, human_id = uuid4(), uuid4(), uuid4()
        with engine.begin() as connection:
            for identifier, action, actor in (
                (legacy_id, "PUBLICATION_BLOCKED", None),
                (execution_id, "PUBLICATION_EXECUTED", None),
                (human_id, "PUBLICATION_MANUAL_RETRY", uuid4()),
            ):
                connection.execute(
                    text(
                        "INSERT INTO audit_log (id,actor_id,action,artifact_type,artifact_id,"
                        "artifact_version,result,metadata) VALUES "
                        "(:id,:actor,:action,'publication',:artifact,1,'SUCCESS','{}')"
                    ),
                    {"id": identifier, "actor": actor, "action": action, "artifact": uuid4()},
                )
        command.downgrade(config, "0010_publication_scheduler")
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("publications")} == prior_columns
        assert {
            check["name"]: check["sqltext"]
            for check in inspector.get_check_constraints("audit_log")
        } == prior_checks
        assert "publication_attempts" not in inspector.get_table_names()
        with engine.connect() as connection:
            ids = set(connection.scalars(text("SELECT id FROM audit_log")))
            assert execution_id not in ids and legacy_id in ids and human_id in ids
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log (id,actor_id,action,artifact_type,artifact_id,"
                    "artifact_version,result,metadata) VALUES "
                    "(:id,NULL,'PUBLICATION_EXECUTED','publication',:artifact,1,'SUCCESS','{}')"
                ),
                {"id": uuid4(), "artifact": uuid4()},
            )
        command.upgrade(config, "head")
        command.check(config)
    finally:
        os.environ["NEWS_AI_DATABASE_URL"] = url
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
