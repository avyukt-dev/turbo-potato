# News AI Social Media Manager

An evidence-first, human-reviewed AI newsroom and social publishing system.

The application is designed to collect news, turn developing stories into explicit claims, research those claims against traceable evidence, preserve contradictions and uncertainty, build an auditable Fact Sheet, generate social content from that factual boundary, run deterministic and AI-assisted quality checks, require human approval, and publish through controlled social-platform adapters.

It is especially suited to political, governance, geopolitical, security, legal, historical, demographic, religious-rights, civilizational, and fact-checking coverage, while remaining usable for general and global news.

---

## What the application does

The normal workflow is:

```text
NEWS / FEEDS / PUBLIC SOURCES
        ↓
DISCOVERY & COLLECTION
        ↓
NORMALIZATION
        ↓
DEDUPLICATION / STORY CLUSTERING
        ↓
CLAIM EXTRACTION
        ↓
RESEARCH PLAN
        ↓
SOURCE SEARCH / EVIDENCE ACQUISITION
        ↓
CORROBORATION + CONTRADICTION DISCOVERY
        ↓
CLAIM VERIFICATION / FACT CHECK
        ↓
FACT SHEET
        ↓
EDITORIAL INTERPRETATION
        ↓
CONTENT GENERATION
        ↓
QUALITY GATE
        ↓
HUMAN REVIEW / APPROVAL
        ↓
SCHEDULING / PUBLISHING
        ↓
OPERATIONS / METRICS / HISTORY
```

The system is intentionally **not** "give an AI model a pile of articles and let it decide what happened."

The core design separates facts, evidence, interpretation, editorial framing, content generation, and publication.

### Operator/product goals

The operator experience is designed around a human staying in control of consequential steps. The product direction includes the ability to:

- manually trigger news collection;
- inspect discovered stories and filter/research candidate items;
- inspect claims, evidence, contradictions, provenance, and Fact Sheets;
- see the current pipeline stage and application/service health;
- review generated content before publication;
- approve, reject, or request correction/regeneration;
- inspect approval, rejection, scheduling, publication, and failure history;
- control or pause publication;
- trigger/schedule approved publication manually;
- review media-generation handoffs where media is part of a content workflow;
- use operational/reconciliation controls when transport or external execution becomes ambiguous.

The repository contains the backend/runtime contracts that support these workflows. A graphical operator UI can consume the same APIs and durable state; the README does not assume that every operator action is already exposed by a specific frontend in this repository checkout.

---

## Core guarantees

### Evidence before conclusion

Search results and AI outputs are not automatically evidence.

A source can be useful for discovery without being sufficient proof of a claim. The research layer separately tracks source roles, provenance, corroboration, contradiction, and independence.

### Editorial preference does not decide truth

Editorial policy may influence:

- which stories receive attention;
- context and audience relevance;
- tone and format;
- risk and review routing.

It must not change evidence, claim verification status, or a fact-check verdict.

### Human approval before external publication

The current external-publishing safety model requires explicit human approval before content is published.

### PostgreSQL is durable truth

PostgreSQL stores durable business state such as stories, claims, evidence, Fact Sheets, content, review decisions, jobs, publications, attempts, and audit history.

Redis Streams is transport/work infrastructure. Losing Redis must not erase durable business state.

### AI is constrained by typed contracts

AI output is treated as untrusted until it passes structural and semantic validation.

The application preserves model/provider, prompt/version, input identity, routing attempts, and other provenance needed to audit AI-assisted work.

---

## Current capabilities in this repository

### Collection and article acquisition

The collection/processor stack supports feed-based discovery and bounded public article acquisition.

Collection and acquisition are deliberately separate from truth assessment. Acquiring an article does not prove that the article is correct or independent.

### Story normalization and clustering

Incoming material is normalized, deduplicated, and grouped into story-level durable state.

### Claim extraction and typed claim semantics

Stories are decomposed into claims rather than evaluated only as article summaries.

The claim pipeline includes typed semantic state and deterministic safeguards around important factual values such as:

- numbers;
- percentages and percentage points;
- currency;
- dates and times;
- durations;
- measurements;
- ranges and bounds;
- approximations.

### Research and evidence engine

The research layer is claim-driven and supports:

- research planning;
- source discovery;
- source-role evaluation;
- evidence acquisition;
- corroboration;
- source-lineage/independence analysis;
- contradiction and counterclaim discovery;
- evidence-to-claim linkage;
- verification and fact-checking;
- historical research domains;
- provenance preservation.

Important distinction:

```text
source count != independent corroboration
```

### Fact Sheet

The Fact Sheet is the normal factual boundary before content generation.

It carries the researched state that downstream editorial/content systems are allowed to use, including claims, evidence, source information, verification state, context, unresolved questions, confidence, risk, and related provenance.

### AI providers

The current provider configuration includes:

- **Groq** using `openai/gpt-oss-120b`;
- **local llama.cpp** through an OpenAI-compatible local endpoint.

Current AI task families include:

- claim extraction;
- evidence assessment;
- content generation;
- quality checking.

The provider layer is abstracted so routing/fallback behavior is not hard-coded into business logic.

### Content and quality

Content is generated from the factual/research boundary rather than directly from raw news discovery.

Quality checks include deterministic semantic validation plus AI-assisted review. Existing safeguards cover areas such as:

- claim-state/certainty consistency;
- unsupported or altered factual values;
- factual drift;
- scope/reference ownership;
- quotations;
- dates/numbers/units;
- overstatement;
- missing or inconsistent provenance.

An AI "pass" cannot override a deterministic error.

### Human review

Review decisions are persisted and audited.

The review boundary is designed to keep approval separate from research and generation so an operator can inspect the evidence-backed artifact before publication.

### Social publishing

The current social implementation includes an Instagram adapter and durable publication/scheduling state.

Publishing is designed to be:

- approval-gated;
- idempotent;
- retry-aware;
- safe around ambiguous external outcomes;
- auditable through durable publication attempts and external IDs.

The architecture is adapter-based so additional platforms can be added without moving platform-specific behavior into the factual pipeline.

### Operations and monitoring

The application exposes:

- `/health`;
- `/ready`;
- `/metrics`;
- runtime capability detection;
- service inspection/control through `newsctl`;
- publishing pause/resume controls;
- event reconciliation tooling.

The `news-pipeline` process owns the upstream production pipeline through quality readiness. API, scheduler, review/publishing boundaries remain separate owners.

---

## Architecture at a glance

```text
                    ┌──────────────────────┐
                    │   FastAPI / newsctl  │
                    │ operator boundaries  │
                    └──────────┬───────────┘
                               │
     ┌─────────────────────────┼─────────────────────────┐
     │                         │                         │
     ▼                         ▼                         ▼
Collection / Processor   Research / AI / Quality   Review / Publishing
     │                         │                         │
     └───────────────┬─────────┴───────────┬─────────────┘
                     │                     │
                     ▼                     ▼
              PostgreSQL 16            Redis 7
              durable truth        streams / work
```

Main application areas:

```text
apps/
├── api/
├── collector/
├── processor/
├── ai-worker/
├── research-worker/
├── publisher/
├── scheduler/
└── pipeline/

packages/
├── ai/
├── common/
├── content/
├── database/
├── domain/
├── editorial/
├── events/
├── evidence/
├── publishing/
├── quality/
├── review/
├── runtime/
└── social/
```

Configuration is kept outside business logic:

```text
config/
├── sources/      # where/how material is collected
├── research/     # how evidence is researched/evaluated
├── editorial/    # what is prioritized and how verified material is presented
├── models/       # AI provider/model routing
├── prompts/      # versioned prompts
├── platforms/    # social platform configuration
└── runtime/      # monitoring/pipeline/runtime configuration
```

---

# Developer setup

## Requirements

For the automated local setup:

- Linux;
- Python **3.11 or newer**;
- Git;
- Debian/Ubuntu if Docker needs to be installed automatically.

Python 3.14 is within the project's declared `>=3.11` support range and the complete local suite has been validated successfully with Python 3.14.4.

Docker is used only to give integration tests isolated PostgreSQL and Redis services. PostgreSQL and Redis do **not** need to be installed directly on the host.

On macOS/Windows or a non-Debian Linux distribution, install Docker/Desktop using the normal platform method first, then run `setup.sh`.

---

## One-command local setup

From the repository root:

```bash
chmod +x setup.sh
./setup.sh
```

`setup.sh` will:

1. locate a Python interpreter >= 3.11;
2. create or reuse `.venv`;
3. install the project with `.[dev]`;
4. check for Docker;
5. install Docker automatically on Debian/Ubuntu when it is missing;
6. pull `postgres:16-alpine` and `redis:7-alpine`;
7. create **isolated disposable** local test containers;
8. bind them only to `127.0.0.1` on automatically selected host ports;
9. optionally ask for `GROQ_API_KEY` using hidden input;
10. create `.env.test.local`;
11. keep social publication disabled/paused in that test environment;
12. apply all Alembic migrations;
13. run `alembic check`.

The setup script only recreates these containers:

```text
news-ai-test-postgres
news-ai-test-redis
```

It does **not** touch Stage 27, development, production, or other PostgreSQL/Redis containers.

---

## Environment file

A normal shell script cannot export variables back into the parent terminal after the script exits.

For that reason, `setup.sh` writes:

```text
.env.test.local
```

The repository already ignores `.env.*`, and the setup script also sets this file to mode `600`.

For every new terminal:

```bash
source .venv/bin/activate
source .env.test.local
```

The generated file contains the local test equivalents of:

```bash
NEWS_AI_ENVIRONMENT=test
NEWS_AI_CONFIG_DIR=/absolute/path/to/config
NEWS_AI_DATABASE_URL=postgresql+psycopg://...
NEWS_AI_REDIS_URL=redis://...
NEWS_AI_READINESS_TIMEOUT_SECONDS=5

NEWS_AI_SOCIAL_MODE=MOCK
NEWS_AI_PUBLISHING_ENABLED=false
NEWS_AI_PUBLISHING_PAUSED=true
```

If you explicitly configure live Groq testing during setup it will also contain:

```bash
GROQ_API_KEY=...
NEWS_AI_RUN_LIVE_GROQ=1
```

The Groq key is not echoed by the setup prompt.

Never commit `.env.test.local` or any real provider/social token.

---

# Testing

## Why some tests need PostgreSQL and Redis

Most unit-level tests can use SQLite, mocks, or in-memory components and therefore run without infrastructure.

The full integration suite uses real PostgreSQL because production correctness depends on behavior SQLite cannot fully reproduce, including:

- PostgreSQL migrations;
- JSONB behavior and PostgreSQL-only constraints;
- transactional behavior;
- uniqueness/foreign-key enforcement under production semantics;
- publication/reconciliation durability;
- database-backed integration flows.

Real Redis is used where Redis Streams and delivery/reconciliation behavior are part of the contract.

This gives the project two useful layers:

```text
fast unit/local tests
    ↓
SQLite / mocks / in-memory components

full integration tests
    ↓
PostgreSQL 16 + Redis 7
```

PostgreSQL remains authoritative for production database behavior.

---

## Run the complete local suite

After `setup.sh`:

```bash
source .venv/bin/activate
source .env.test.local
pytest
```

If Groq live testing was not enabled, the live Groq acceptance test is intentionally skipped.

To show skip reasons:

```bash
pytest -q -rs
```

---

## Run unit tests only

For a fast logic-focused pass:

```bash
pytest tests/unit
```

These should not require the disposable PostgreSQL/Redis services unless a future unit test explicitly changes that contract.

---

## Run integration tests only

```bash
pytest tests/integration
```

For the full integration suite, source `.env.test.local` first so the tests use the disposable PostgreSQL and Redis instances.

---

## Live Groq acceptance test

The live test is intentionally opt-in because it performs a real network/provider call.

It runs only when both are present:

```bash
GROQ_API_KEY=...
NEWS_AI_RUN_LIVE_GROQ=1
```

Run it directly with:

```bash
pytest tests/integration/test_live_groq.py -vv
```

The current live acceptance test exercises the configured Groq `openai/gpt-oss-120b` provider with structured output.

If you want the API key stored locally but do not want every `pytest` run to make the live call:

```bash
export NEWS_AI_RUN_LIVE_GROQ=0
```

or edit the same value in `.env.test.local`.

The normal GitHub CI intentionally leaves live Groq disabled, so provider/network availability does not make ordinary CI flaky.

---

## Full CI-equivalent local gate

Use the disposable test database created by `setup.sh`.

```bash
source .venv/bin/activate
source .env.test.local

python -m pip install -e '.[dev]'
ruff check .
ruff format --check apps packages migrations tests
python -m compileall -q apps packages migrations tests

alembic upgrade head
alembic check

pytest

alembic downgrade base
alembic upgrade head
```

**Do not run the downgrade/upgrade round trip against a development or production database.**

The sequence is destructive by design and belongs only on an isolated test database.

---

## GitHub Actions

Normal CI uses:

- PostgreSQL `16-alpine`;
- Redis `7-alpine`;
- a test-only PostgreSQL database;
- a test-only Redis database;
- migrations before tests;
- migration drift checking;
- migration downgrade/re-upgrade verification.

The live Groq test stays opt-in unless the workflow is deliberately changed to set both `GROQ_API_KEY` and `NEWS_AI_RUN_LIVE_GROQ=1`.

---

# Running the application locally

The test environment created by `setup.sh` is safe for development/testing, but it is not production configuration.

## API

Activate the environment and start FastAPI:

```bash
source .venv/bin/activate
source .env.test.local

uvicorn news_ai_api.main:app --reload
```

Useful endpoints:

```text
GET /health
GET /ready
GET /metrics
```

`/ready` checks the important runtime dependencies rather than only reporting that the HTTP process is alive.

---

## Upstream pipeline

Run the production-composed upstream pipeline with:

```bash
news-pipeline
```

The upstream pipeline owns concurrent collection, outbox dispatch, normalization, processing, research, content, and quality work through quality readiness.

Human approval and external publication remain separate control boundaries.

---

## Runtime/operator CLI

Inspect detected runtime capabilities:

```bash
newsctl runtime
```

Health:

```bash
newsctl health
```

List configured services:

```bash
newsctl service list
```

Inspect a service:

```bash
newsctl service status <service-name>
```

Publishing control:

```bash
newsctl publish status
newsctl publish pause --reason "maintenance"
newsctl publish resume --reason "maintenance complete"
```

Dry-run event reconciliation:

```bash
newsctl events reconcile --dry-run
```

Applying reconciliation requires an explicit reason:

```bash
newsctl events reconcile --apply --reason "recover missing transport event"
```

Application/domain code should not call `systemctl`, `rc-service`, or other host-specific service managers directly. Runtime control is kept behind the runtime abstraction.

---

# Local test-service lifecycle

Show the disposable services:

```bash
docker ps --filter label=news-ai.local-test=true
```

Stop them without deleting:

```bash
docker stop news-ai-test-postgres news-ai-test-redis
```

Start them again:

```bash
docker start news-ai-test-postgres news-ai-test-redis
```

Remove/reset the local test database and Redis:

```bash
docker rm -f news-ai-test-postgres news-ai-test-redis
```

Then rerun:

```bash
./setup.sh
```

The images remain cached locally so later setup runs are faster.

---

# Important safety notes

## Never use a real database for the test suite

Integration tests may truncate data and the CI-equivalent migration gate deliberately downgrades the schema to `base`.

Always verify:

```bash
echo "$NEWS_AI_ENVIRONMENT"
echo "$NEWS_AI_DATABASE_URL"
echo "$NEWS_AI_REDIS_URL"
```

before destructive migration/reconciliation testing.

For the environment created by `setup.sh`:

```text
NEWS_AI_ENVIRONMENT=test
```

and the database/Redis URLs point to loopback-only disposable Docker containers.

## Keep external publishing inert while testing

The generated local environment uses:

```text
NEWS_AI_SOCIAL_MODE=MOCK
NEWS_AI_PUBLISHING_ENABLED=false
NEWS_AI_PUBLISHING_PAUSED=true
```

Do not replace these with live publication settings merely to make tests pass.

## Treat provider keys as secrets

Never put `GROQ_API_KEY`, Instagram tokens, review tokens, or other credentials into:

- committed files;
- test fixtures;
- screenshots;
- issue/PR comments;
- logs;
- shell commands that will be pasted or shared.

If a real key is accidentally exposed, rotate it.

---

# Troubleshooting

## Many integration tests are skipped

Run:

```bash
pytest -q -rs
```

If skip reasons mention PostgreSQL or Redis, the test environment was not sourced.

Fix:

```bash
source .venv/bin/activate
source .env.test.local
pytest
```

If `.env.test.local` does not exist or the containers were deleted:

```bash
./setup.sh
```

## Live Groq test is skipped

Verify without printing the key:

```bash
for v in GROQ_API_KEY NEWS_AI_RUN_LIVE_GROQ; do
  [ -n "${!v:-}" ] && echo "$v = SET" || echo "$v = MISSING"
done
```

The test requires:

```text
GROQ_API_KEY          = set
NEWS_AI_RUN_LIVE_GROQ = 1
```

## Docker exists but setup cannot access it

The script first tries normal Docker access and then `sudo docker`.

It intentionally does **not** add your user to the `docker` group automatically because Docker group membership is effectively root-equivalent.

Check:

```bash
docker info
```

or:

```bash
sudo docker info
```

## A stale/broken virtual environment exists

Remove it and rerun setup:

```bash
rm -rf .venv
./setup.sh
```

This is useful after changing/removing Python installations or environment managers.

## Verify the Python interpreter

```bash
source .venv/bin/activate

python --version
which python
python -c 'import sys; print(sys.executable); print(sys.base_prefix)'
```

The project requires Python >= 3.11.

---

# Configuration and secrets

A non-secret template is provided in:

```text
.env.example
```

Process settings use the `NEWS_AI_` prefix.

Important settings include:

| Variable | Purpose |
| --- | --- |
| `NEWS_AI_ENVIRONMENT` | Runtime environment such as `test`, `development`, or `production` |
| `NEWS_AI_CONFIG_DIR` | Root of application YAML configuration |
| `NEWS_AI_DATABASE_URL` | PostgreSQL SQLAlchemy/psycopg URL |
| `NEWS_AI_REDIS_URL` | Redis URL |
| `NEWS_AI_READINESS_TIMEOUT_SECONDS` | Dependency readiness timeout |
| `NEWS_AI_REVIEW_API_TOKEN` | Review API authentication secret when configured |
| `NEWS_AI_REVIEWER_ID` | Reviewer identity |
| `NEWS_AI_SERVICE_MANAGER` | Optional explicit runtime-manager override |
| `GROQ_API_KEY` | Groq provider secret |
| `NEWS_AI_RUN_LIVE_GROQ` | Explicit opt-in for the live Groq acceptance test |

Social platform secrets must also remain outside version control.

---

# Database migrations

Alembic owns schema history.

Upgrade:

```bash
alembic upgrade head
```

Check ORM/migration drift:

```bash
alembic check
```

Current development policy requires schema changes to include:

- an Alembic migration;
- ORM parity;
- integration coverage;
- migration drift validation;
- downgrade/re-upgrade validation when applicable.

PostgreSQL is the authoritative database for migration correctness.

---

# Development principles

When modifying the system:

- preserve PostgreSQL as the durable source of truth;
- treat Redis as transport, not durable business state;
- keep external/network work outside long database transactions;
- preserve durable IDs, idempotency, correlation/causation, and provenance;
- never make raw AI output authoritative;
- never let AI override deterministic errors;
- never let editorial preference change factual status;
- preserve explicit human approval for external publication;
- keep platform/service-manager details behind runtime abstractions;
- validate changes with the full local gate before calling them ready.

---

# Deeper technical documentation

This README is intended to be sufficient for understanding the product and getting a developer environment working.

The `docs/` directory contains the canonical implementation contracts for contributors who need subsystem-level detail:

- `CANONICAL_CONTRACTS.md` — shared invariants and lifecycle semantics;
- `ARCHITECTURE.md` — complete architecture and boundaries;
- `DATA_MODEL.md` — PostgreSQL schema/persistence;
- `EVENTS.md` — Redis Streams/event contracts;
- `AI_PLATFORM.md` — AI providers, routing, prompts, provenance;
- `SOURCE_AND_RESEARCH.md` — evidence/research methodology;
- `CONTENT_AND_EDITORIAL.md` — editorial/content policy;
- `CONTENT_SCHEMAS.md` — structured application contracts;
- `INFRASTRUCTURE_AND_DEPLOYMENT.md` — runtime/deployment;
- `SOCIAL_PUBLISHING.md` — publication execution;
- `API_SPEC.md` — HTTP/API contracts;
- `TESTING_AND_EVALUATION.md` — testing/release methodology;
- `OPERATIONS_RUNBOOK.md` — operational procedures.

A normal user or new developer should not need to read all of those documents before they can understand or run the application.
