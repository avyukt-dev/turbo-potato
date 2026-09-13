# Migration revisions

Migration files in this directory are immutable snapshots of approved schema changes.

The initial revision must be generated from the approved foundation metadata and reviewed before any deployment. A migration must never call `Base.metadata.create_all()` or import mutable model definitions to create/drop the schema at runtime.

Typical development command after configuring a disposable PostgreSQL database:

```bash
NEWS_AI_DATABASE_URL='postgresql+psycopg://...' alembic revision --autogenerate -m 'foundation'
```

Review generated constraints, indexes, enum handling, JSONB types, and downgrade operations before commit.
