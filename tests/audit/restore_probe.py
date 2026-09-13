"""Logical restore and representative-data migration probe on a disposable DB.

Only explicit local test infrastructure is accepted. Source is never migrated;
all downgrade/upgrade operations target the uniquely named restored database.
"""

import json
import os
import re
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def snapshot(url):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            result = {}
            for table in inspect(connection).get_table_names():
                if table == "alembic_version":
                    continue
                rows = connection.execute(
                    text(f'SELECT to_jsonb(t)::text FROM "{table}" t')
                ).scalars()
                values = sorted(rows)
                result[table] = {
                    "rows": len(values),
                    "hash": sha256("\n".join(values).encode()).hexdigest(),
                }
            return result
    finally:
        engine.dispose()


def run_tool(arguments, env, *, input_data=None):
    result = subprocess.run(
        arguments, env=env, input=input_data, capture_output=True, timeout=120, check=False
    )
    if result.returncode:
        raise RuntimeError("logical backup/restore tool failed; raw diagnostics suppressed")
    return result.stdout


def main():
    original = os.environ["NEWS_AI_DATABASE_URL"]
    source = make_url(original)
    if (
        os.environ.get("NEWS_AI_ENVIRONMENT") != "test"
        or source.host not in {"localhost", "127.0.0.1"}
        or source.get_backend_name() != "postgresql"
    ):
        raise RuntimeError("explicit local PostgreSQL test service required")
    name = f"acceptance_restore_{uuid4().hex}"
    target = source.set(database=name)
    admin_url = source.set(database="postgres", drivername="postgresql")
    env = dict(os.environ, PGPASSWORD=source.password or "")
    args = ["-h", source.host, "-p", str(source.port), "-U", source.username]
    container = os.getenv("NEWS_AI_AUDIT_POSTGRES_CONTAINER")
    if container and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container) is None:
        raise RuntimeError("invalid isolated test container name")
    before = snapshot(source)
    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with tempfile.TemporaryDirectory(prefix="acceptance-backup-") as directory:
                archive = Path(directory) / "synthetic.dump"
                if container:
                    # Matching server tools avoid newer dump SET statements on
                    # older PostgreSQL; Docker is optional test infrastructure.
                    archive.write_bytes(
                        run_tool(
                            [
                                "docker",
                                "exec",
                                container,
                                "pg_dump",
                                "-U",
                                source.username,
                                "-Fc",
                                "-d",
                                source.database,
                            ],
                            env,
                        )
                    )
                    run_tool(
                        [
                            "docker",
                            "exec",
                            "-i",
                            container,
                            "pg_restore",
                            "-U",
                            source.username,
                            "--no-owner",
                            "-d",
                            name,
                        ],
                        env,
                        input_data=archive.read_bytes(),
                    )
                else:
                    run_tool(["pg_dump", *args, "-Fc", "-f", str(archive), source.database], env)
                    run_tool(["pg_restore", *args, "--no-owner", "-d", name, str(archive)], env)
                restored = snapshot(target)
                assert restored == before
                size = archive.stat().st_size
            os.environ["NEWS_AI_DATABASE_URL"] = target.render_as_string(hide_password=False)
            config = Config("alembic.ini")
            stable_tables = {
                "articles",
                "article_versions",
                "stories",
                "claims",
                "evidence_items",
                "claim_evidence",
                "fact_checks",
                "fact_sheets",
                "ai_runs",
                "ai_models",
                "content_drafts",
                "content_variants",
                "content_quality_checks",
                "review_decisions",
            }
            rounds = []
            for revision in (
                "0011_publication_execution",
                "0010_publication_scheduler",
                "0009_human_review",
            ):
                command.downgrade(config, revision)
                lowered = snapshot(target)
                command.upgrade(config, "head")
                upgraded = snapshot(target)
                retained = all(
                    lowered.get(table) == before.get(table) == upgraded.get(table)
                    for table in stable_tables
                    if table in before
                )
                assert retained
                command.check(config)
                rounds.append({"from_revision": revision, "core_graph_preserved": retained})
            print(
                json.dumps(
                    {
                        "all_table_rows_restore_identically": restored == before,
                        "nonempty_tables": sum(value["rows"] > 0 for value in before.values()),
                        "total_rows": sum(value["rows"] for value in before.values()),
                        "archive_bytes": size,
                        "existing_data_round_trips": rounds,
                        "source_database_unchanged": snapshot(source) == before,
                    },
                    indent=2,
                )
            )
        finally:
            os.environ["NEWS_AI_DATABASE_URL"] = original
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
