# News AI Social Media Manager — Data Model

## 1. Purpose

This document defines the canonical PostgreSQL data model for the News AI Social Media Manager.

It is the implementation reference for database schema, entity relationships, provenance, claims and evidence, story clustering, editorial scoring, AI execution, fact checking, content generation, human review, social publishing, jobs and retries, analytics, and auditing.

The database is the **system of record**. Redis Streams is the event transport layer, not the source of truth.

Shared enums and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 2. Database Principles

## 2.1 PostgreSQL is authoritative

Persistent business state MUST live in PostgreSQL.

Redis MUST NOT be treated as the canonical store for stories, claims, evidence, fact checks, generated content, approvals, publications, credentials, or audit history.

Redis may contain queues, transient processing state, stream messages, locks, and short-lived caches.

## 2.2 Provenance is mandatory

Every important factual object should be traceable to its origin, supporting and contradicting evidence, producing AI run, and human approval where applicable.

## 2.3 IDs

Use UUIDs for externally meaningful database records.

## 2.4 Timestamps

All canonical timestamps MUST use PostgreSQL `TIMESTAMPTZ`. Render local time only at the application boundary.

## 2.5 Soft deletion

Records participating in provenance or audit history should generally not be physically deleted. Historical evidence, publications, AI runs, and audit records should remain immutable where practical.

---

# 3. High-Level Entity Model

```text
SOURCE
  │
  ├── SOURCE_FEED
  │
  └── ARTICLE
        │
        ├── ARTICLE_VERSION
        │
        └── STORY_SOURCE
                │
                ▼
              STORY
                │
        ┌───────┼────────┬──────────┐
        ▼       ▼        ▼          ▼
      CLAIM   ENTITY    EVENT    EDITORIAL_SCORE
        │
        ▼
   CLAIM_EVIDENCE
        │
        ▼
    EVIDENCE_ITEM
        │
        ▼
   FACT_CHECK
        │
        ▼
     FACT_SHEET
        │
        ▼
   CONTENT_DRAFT
        │
        ▼
 CONTENT_VARIANT
        │
        ▼
    PUBLICATION
        │
        ▼
PUBLICATION_ATTEMPT
        │
        ▼
    ANALYTICS
```

AI execution and jobs connect across the pipeline. All important mutations may be represented in `AUDIT_LOG`.

---

# 4. PostgreSQL Extensions

Recommended initially:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

`pgvector` is optional and not an MVP requirement.

---

# 5. Enumerations

Prefer explicit application enums backed by PostgreSQL enums only where the state is sufficiently stable.

Shared enums MUST use the meanings in `CANONICAL_CONTRACTS.md`.

## 5.1 ClaimVerificationStatus

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

This enum describes the evidence/verification state of a claim.

It must not contain fact-check verdict labels such as `FALSE`, `FABRICATED`, `OUT_OF_CONTEXT`, or `SATIRE`.

## 5.2 FactCheckLabel

```text
TRUE
MOSTLY_TRUE
PARTIALLY_TRUE
MISLEADING
OUT_OF_CONTEXT
UNVERIFIED
FALSE
FABRICATED
SATIRE
```

This enum describes a fact-check verdict and is separate from claim verification state.

Critical invariant:

```text
UNVERIFIED != FALSE
```

## 5.3 RiskLevel

```text
LOW
MEDIUM
HIGH
CRITICAL
```

Sensitivity is stored separately from risk level.

## 5.4 ReviewState

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

## 5.5 PublicationStatus

```text
DRAFT
READY_FOR_REVIEW
APPROVED
SCHEDULED
PUBLISHING
RETRYING
BLOCKED
PUBLISHED
FAILED
CANCELLED
```

Post-publication descriptors such as `UPDATED`, `CORRECTED`, and `ARCHIVED` should not erase the original publication history.

---

# 6. Core Tables

The canonical table set is:

```text
sources
source_feeds
articles
article_versions
stories
story_sources
claims
claim_evidence
evidence_items
entities
entity_mentions
events
event_locations
event_entities
historical_events
historical_sources
editorial_rules
editorial_scores
ai_models
ai_runs
ai_prompts
fact_checks
fact_sheets
content_drafts
content_variants
media_assets
social_accounts
publications
publication_attempts
analytics_snapshots
jobs
job_attempts
audit_log
```

---

# 7. Sources

`sources` represents an external information source.

Recommended fields:

```text
id                  UUID PK
name                TEXT NOT NULL
domain              TEXT
base_url             TEXT
source_type          TEXT NOT NULL
authority_level      SMALLINT
country              TEXT
language             TEXT
description          TEXT
is_active            BOOLEAN NOT NULL DEFAULT TRUE
metadata             JSONB
created_at           TIMESTAMPTZ NOT NULL
updated_at           TIMESTAMPTZ NOT NULL
```

Authority levels are source roles, not truth guarantees:

```text
1 = primary
2 = established
3 = specialist/research
4 = discovery/social
```

---

# 8. Source Feeds

`source_feeds` represents collection mechanisms such as RSS, Atom, APIs, sitemaps, webpages, or social APIs.

```text
id                  UUID PK
source_id            UUID FK → sources.id
name                 TEXT NOT NULL
feed_url             TEXT
feed_type            TEXT NOT NULL
poll_interval_seconds INTEGER
last_polled_at       TIMESTAMPTZ
etag                  TEXT
last_modified         TEXT
is_active            BOOLEAN NOT NULL DEFAULT TRUE
configuration        JSONB
created_at           TIMESTAMPTZ NOT NULL
updated_at           TIMESTAMPTZ NOT NULL
```

---

# 9. Articles and Versions

`articles` stores normalized discovered article identity and metadata. `article_versions` stores immutable retrieved versions so source edits are not silently overwritten.

Use uniqueness on `(source_id, canonical_url)` and `(article_id, version_number)`.

---

# 10. Stories

A story is the normalized real-world event/topic being tracked. Multiple articles may belong to one story.

Recommended fields include:

```text
id
canonical_headline
summary
status
language
first_seen_at
last_updated_at
importance_score
evidence_strength
controversy_score
risk_score
confidence_score
cluster_key
metadata
created_at
updated_at
```

`story.verified` semantics are defined by `CANONICAL_CONTRACTS.md`: it means the verification stage completed, not that every claim is true.

---

# 11. Story Sources

`story_sources` is the many-to-many relation between stories and articles.

Possible relationship types:

```text
PRIMARY_REPORT
FOLLOWUP
CORROBORATION
CONTEXT
CONTRADICTION
BACKGROUND
DISCOVERY
```

---

# 12. Claims

A claim is a discrete factual proposition.

```text
claims
------
id                    UUID PK
story_id              UUID FK
claim_text             TEXT NOT NULL
normalized_claim       TEXT
claim_type             TEXT
status                 TEXT  -- ClaimVerificationStatus
confidence_score       NUMERIC
importance_score       NUMERIC
risk_level             TEXT  -- RiskLevel
temporal_start         TIMESTAMPTZ
temporal_end           TIMESTAMPTZ
location_id            UUID NULL
created_by_ai_run_id   UUID NULL
metadata               JSONB
created_at             TIMESTAMPTZ NOT NULL
updated_at             TIMESTAMPTZ NOT NULL
```

## 12.1 Claim status

Only these canonical values are used:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

`UNVERIFIED` means insufficient evidence. `REFUTED` means sufficient evidence establishes that the proposition, as stated, is not supported.

---

# 13. Evidence Items

`evidence_items` stores individual evidence records such as court judgments, government releases, police statements, research papers, statistics, articles, interviews, photographs, videos, social posts, and archived webpages.

Evidence metadata must preserve provenance and must never contain credentials or authorization headers.

---

# 14. Claim Evidence

`claim_evidence` links evidence to claims.

Recommended relation vocabulary:

```text
DIRECT_SUPPORT
INDIRECT_SUPPORT
CONTRADICTS
QUALIFIES
CONTEXT
PRIMARY_EVIDENCE
SECONDARY_EVIDENCE
```

The relation should be explicit rather than inferred solely from a boolean.

---

# 15. Entities and Mentions

Canonical entity types include:

```text
PERSON
ORGANIZATION
GOVERNMENT
POLITICAL_PARTY
COUNTRY
CITY
REGION
RELIGIOUS_TRADITION
HISTORICAL_FIGURE
MILITARY_UNIT
COURT
LAW
EVENT
```

Entity mentions connect canonical entities to source material, stories, and claims.

---

# 16. Events and Locations

`events`, `event_locations`, and `event_entities` represent concrete events and participants. Temporal precision may be:

```text
EXACT
DAY
MONTH
YEAR
APPROXIMATE
UNKNOWN
```

---

# 17. Historical Events and Sources

Historical research supports uncertain chronology and multiple evidence domains.

Possible source types include:

```text
PRIMARY_TEXT
INSCRIPTION
ARCHAEOLOGY
LITERARY_SOURCE
LINGUISTIC_EVIDENCE
GENETIC_EVIDENCE
NUMISMATIC
EPIGRAPHIC
SECONDARY_SCHOLARSHIP
MODERN_RESEARCH
```

Historical interpretations must support competing hypotheses without collapsing distinct evidence domains.

---

# 18. Editorial Rules and Scores

Editorial rules determine attention, ranking, framing preferences, and review requirements. They MUST NOT determine factual truth.

Editorial scoring keeps importance and evidence strength separate.

---

# 19. AI Models, Runs, and Prompts

`ai_models`, `ai_runs`, and `ai_prompts` provide provider/model registry, execution provenance, and prompt versioning.

Do not store provider API keys in these tables.

---

# 20. Fact Checks

`fact_checks.status` stores `FactCheckLabel`, not `ClaimVerificationStatus`.

```text
fact_checks
-----------
id                    UUID PK
story_id               UUID FK
claim_id               UUID FK NULL
status                 TEXT NOT NULL  -- FactCheckLabel
confidence_score       NUMERIC
summary                TEXT
reasoning_summary      TEXT
primary_evidence_count INTEGER
supporting_count       INTEGER
contradicting_count    INTEGER
review_required        BOOLEAN
review_status          TEXT           -- ReviewState
ai_run_id              UUID FK NULL
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

Allowed verdicts:

```text
TRUE
MOSTLY_TRUE
PARTIALLY_TRUE
MISLEADING
OUT_OF_CONTEXT
UNVERIFIED
FALSE
FABRICATED
SATIRE
```

The system must distinguish automated assessment from human-approved assessment.

---

# 21. Fact Sheets

The Fact Sheet is the canonical intermediate representation between research and content generation.

```text
fact_sheets
-----------
id
story_id
version
headline
summary
verified_claims
disputed_claims
unverified_claims
evidence
timeline
entities
locations
context
counterclaims
confidence_score
risk_level
source_snapshot
ai_run_id
created_at
```

A Fact Sheet used for publication should be immutable. Corrections create a new version.

---

# 22. Content Drafts and Variants

`content_drafts` represents a generated package tied to a story and Fact Sheet. `content_variants` represents platform/format/language-specific outputs.

Normal content generation must consume a Fact Sheet rather than bypassing directly from raw articles.

---

# 23. Media Assets

`media_assets` tracks storage and delivery metadata separately.

Recommended fields include:

```text
storage_provider
storage_key
public_url
mime_type
width
height
duration_ms
file_hash
generation_ai_run_id
source_metadata
visual_check_status
```

Local storage does not imply public accessibility. Public delivery is governed by infrastructure and social publishing policy.

---

# 24. Social Accounts

`social_accounts` stores connected publishing accounts and a `credential_reference`, never raw tokens.

---

# 25. Publications

`publications.status` uses `PublicationStatus`.

```text
publications
------------
id
content_variant_id
social_account_id
status
scheduled_at
published_at
external_post_id
external_url
failure_reason
created_at
updated_at
```

For the MVP, external publication requires explicit human approval before reaching `SCHEDULED`.

---

# 26. Publication Attempts

Each external attempt is recorded independently so retry history is never overwritten.

---

# 27. Analytics Snapshots

Analytics are stored as timestamped snapshots rather than mutable counters.

---

# 28. Jobs and Job Attempts

`jobs` represents durable asynchronous work state. `job_attempts` records individual processing attempts.

Redis delivery never replaces these durable records.

---

# 29. Audit Log

Audit meaningful human/system actions, including approvals, rejections, corrections, publication decisions, source changes, and editorial-rule changes.

Approval records should include who, what, when, which version, decision, and reason.

---

# 30. Evidence Packet

Every publication candidate must be reconstructable as an evidence packet containing story, claims, sources, primary/supporting/contradicting evidence, timeline, entities, locations, counterclaims, fact-check result, confidence, risk, editorial angle, AI runs, and human review.

The packet may be reconstructed from normalized tables rather than stored as one giant JSON object.

---

# 31. Sensitive-Topic Metadata

Sensitivity is separate from `RiskLevel`.

Example:

```json
{
  "sensitive_topics": [
    "COMMUNAL_VIOLENCE",
    "RELIGIOUS_ALLEGATION",
    "SC_ST_ALLEGATION"
  ],
  "risk_level": "HIGH"
}
```

Sensitive topics trigger stricter evidence and review gates.

---

# 32. Demographic Data

Demographic information must distinguish:

```text
OBSERVED_DATA
STATISTICAL_INTERPRETATION
POSSIBLE_CAUSES
CAUSAL_EVIDENCE
EDITORIAL_INTERPRETATION
```

Never automatically convert demographic change into communal blame.

---

# 33. Civilizational Taxonomy

Use:

```text
INDIC_CIVILIZATIONAL_CONTEXT
├── HINDU_TRADITIONS
├── BUDDHIST_TRADITIONS
├── JAIN_TRADITIONS
├── SIKH_TRADITIONS
├── INDIGENOUS_REGIONAL_TRADITIONS
└── ANCIENT_INDIAN_CULTURAL_TRADITIONS
```

This permits shared historical context without collapsing distinct religious identities.

---

# 34. Indexing and JSONB

Index fields based on measured query patterns. Use JSONB for evolving/provider-specific metadata, not as a substitute for relational modeling of frequently queried or integrity-critical fields.

---

# 35. Foreign-Key and Uniqueness Rules

Prefer restrictive deletion for provenance-critical relationships. Preserve uniqueness for canonical URLs, article versions, story/article links, Fact Sheet versions, content variants, and attempt numbers.

---

# 36. Idempotency

All external side effects must be idempotent where possible. Publication idempotency should be based on the canonical publication operation and verified external state.

---

# 37. Transaction Boundaries

Important state transitions should be transactional. Do not perform external API calls inside long PostgreSQL transactions.

Use:

```text
database state
→ job/outbox
→ external API
→ database result
```

---

# 38. Optimistic Concurrency

Mutable editorial objects should use versioning or equivalent optimistic concurrency to prevent silent reviewer overwrites.

---

# 39. Retention

Claims, evidence, fact checks, Fact Sheets, publications, and audit history should be retained long-term according to policy. AI runs and raw provider responses may have configurable retention.

---

# 40. Database Migrations

Every schema change must be represented by a versioned migration. Alembic is the recommended Python migration tool.

---

# 41. SQLAlchemy Organization

Keep ORM/persistence code under `packages/database/`, with domain logic in services rather than raw ORM models.

---

# 42. Repository and Service Layers

Use:

```text
API / Worker
    ↓
Service
    ↓
Repository
    ↓
SQLAlchemy
    ↓
PostgreSQL
```

Repositories handle persistence. Services handle business logic.

---

# 43. Event References and Outbox

Redis events should reference database records rather than duplicate large payloads.

Where reliable event emission is required, use a transactional outbox:

```text
PostgreSQL transaction
    ├── business state update
    └── outbox event
        ↓
Outbox publisher
        ↓
Redis Streams
```

---

# 44. MVP Database Scope

MVP tables:

```text
sources
source_feeds
articles
article_versions
stories
story_sources
claims
claim_evidence
evidence_items
entities
entity_mentions
editorial_scores
ai_models
ai_runs
fact_checks
fact_sheets
content_drafts
content_variants
social_accounts
publications
publication_attempts
jobs
audit_log
```

Historical-specific tables may follow immediately if historical research is included in MVP.

---

# 45. First Working Flow

```text
RSS item
   ↓
sources / source_feeds
   ↓
articles / article_versions
   ↓
stories / story_sources
   ↓
claims
   ↓
evidence_items / claim_evidence
   ↓
fact_checks
   ↓
fact_sheets
   ↓
content_drafts / content_variants
   ↓
human approval
   ↓
publications
```

Every stage must be independently inspectable.

---

# 46. Data Integrity Rules

The application MUST prevent:

* publication without a content variant
* content generation without a story and eligible Fact Sheet
* fact sheet without a story
* claim evidence without a claim
* publication without a social account
* publication retry without an attempt record
* human approval without an audit record
* fact-check verdict without associated evidence assessment
* deletion of evidence required by published content
* scheduling external publication before required human approval

---

# 47. Corrections

Corrections create a new Fact Sheet/content version and preserve the original evidence and publication trail.

---

# 48. Documentation Precedence

Cross-document shared semantics are defined in `CANONICAL_CONTRACTS.md`.

Persistent structures are defined here.

System architecture remains defined in `ARCHITECTURE.md`.

Implementation MUST conform to all three. If implementation reveals a necessary change, update the canonical documentation, migrations/models, and tests together.

---

# 49. Final Canonical Model

```text
SOURCE
  ↓
ARTICLE
  ↓
STORY
  ↓
CLAIM
  ↓
EVIDENCE
  ↓
FACT CHECK
  ↓
FACT SHEET
  ↓
CONTENT
  ↓
HUMAN APPROVAL
  ↓
PUBLICATION
  ↓
ANALYTICS
```

AI is an analysis and generation layer around this chain. It is not the source of truth.