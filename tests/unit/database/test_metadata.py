from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from news_ai_database import Base


EXPECTED_FOUNDATION_TABLES = {
    "sources",
    "source_feeds",
    "articles",
    "article_versions",
    "stories",
    "story_sources",
    "claims",
    "evidence_items",
    "claim_evidence",
    "fact_checks",
    "fact_sheets",
    "jobs",
    "job_attempts",
    "event_outbox",
}


def test_foundation_tables_are_registered() -> None:
    assert EXPECTED_FOUNDATION_TABLES <= set(Base.metadata.tables)


def test_outbox_compiles_for_postgresql() -> None:
    table = Base.metadata.tables["event_outbox"]
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))

    assert "CREATE TABLE event_outbox" in ddl
    assert "JSONB" in ddl
    assert "event_id" in ddl
