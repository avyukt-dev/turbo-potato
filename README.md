# News AI Social Media Manager

Implementation repository for an evidence-first AI newsroom and social publishing system.

The canonical design is in [`docs/`](docs/README.md). Development happens on the `development` branch; `main` remains the stable documentation/integration branch until an implementation batch is approved.

## Initial implementation scope

```text
repository skeleton
  ↓
typed configuration
  ↓
runtime capability detection
  ↓
platform-independent service control
  ↓
FastAPI health/readiness
  ↓
PostgreSQL + migrations
  ↓
Redis Streams + transactional outbox
```

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

Run the API during development:

```bash
uvicorn news_ai_api.main:app --reload
```

Inspect runtime capabilities:

```bash
newsctl runtime
```

Application/domain code must not call host-specific service managers directly. Native control is isolated behind `news_ai_common.runtime`.
