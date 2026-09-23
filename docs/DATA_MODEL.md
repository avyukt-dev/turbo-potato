# News AI Social Media Manager — Data Model

## 1. Purpose

This document defines the canonical PostgreSQL persistence model for the News AI Social Media Manager.

PostgreSQL is the durable source of truth.

Redis Streams is event/work transport and must not become authoritative business state.

Shared enums and lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 2. Database Principles

## 2.1 PostgreSQL is authoritative

Persistent business state lives in PostgreSQL.

Redis may hold:

```text
stream messages
consumer state
short-lived cache
locks/coordination
```

but must not be the only location containing stories, claims, evidence, reviews, publications, jobs, or audit state.

## 2.2 Provenance is mandatory

Important factual/content objects should be traceable to:

```text
source material
claims
evidence
contradictions
AI runs/prompts
Fact Sheet version
review decision
publication attempt
```

## 2.3 IDs

Use UUIDs for externally meaningful durable records.

## 2.4 Time

Use PostgreSQL `TIMESTAMPTZ` for canonical timestamps.

Render local time at application/UI boundaries.

## 2.5 Deletion and immutability

Provenance-critical records should not be physically deleted as the normal workflow.

Historical evidence, Fact Sheets used for publication, AI runs, publication attempts, and audit records should remain reconstructable.

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
        ┌───────┼────────┬──────────────┐
        ▼       ▼        ▼              ▼
      CLAIM   ENTITY   REAL_EVENT   EDITORIAL_SCORE
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
 ANALYTICS_SNAPSHOT
```

AI runs, jobs, and audit records connect across the pipeline.

---

# 4. Extensions

Recommended initially:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

`pgvector` is optional and not an MVP requirement.

---

# 5. Canonical Shared Enums

## 5.1 ClaimVerificationStatus

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

`UNVERIFIED != REFUTED`.

Legacy values such as `partially_confirmed` are invalid.

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

`UNVERIFIED != FALSE`.

## 5.3 RiskLevel

```text
LOW
MEDIUM
HIGH
CRITICAL
```

Sensitivity remains separate metadata.

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

Post-publication descriptors such as `UPDATED`, `CORRECTED`, and `ARCHIVED` may be stored separately without erasing history.

---

# 6. Canonical Table Set

Initial domain tables:

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

Infrastructure support tables such as an event outbox/processed-event ledger may be added according to `EVENTS.md`.

---

# 7. Sources

`sources` represents external source identity/metadata.

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

`authority_level` reflects a source role/hierarchy signal, not a truth guarantee.

Evidence methodology is owned by `SOURCE_AND_RESEARCH.md`; operational collection configuration is owned by `config/sources/`.

---

# 8. Source Feeds

```text
source_feeds
------------
id                    UUID PK
source_id              UUID FK → sources.id
name                   TEXT NOT NULL
feed_url               TEXT
feed_type              TEXT NOT NULL
poll_interval_seconds  INTEGER
last_polled_at         TIMESTAMPTZ
etag                    TEXT
last_modified           TEXT
is_active              BOOLEAN NOT NULL DEFAULT TRUE
configuration          JSONB
created_at             TIMESTAMPTZ NOT NULL
updated_at             TIMESTAMPTZ NOT NULL
```

This table stores collection mechanics, not evidence sufficiency policy.

---

# 9. Articles

`articles` stores normalized article identity/current metadata.

Useful fields:

```text
id
source_id
source_feed_id
canonical_url
title
author
published_at
first_seen_at
language
current_version
content_hash
metadata
created_at
updated_at
```

Recommended uniqueness:

```text
(source_id, canonical_url)
```

---

# 10. Article Versions

`article_versions` preserves retrieved source versions.

```text
id
article_id
version_number
title
content
content_hash
retrieved_at
source_updated_at
metadata
```

Recommended uniqueness:

```text
(article_id, version_number)
```

Source edits must not silently overwrite the material used for earlier evidence/review decisions.

---

# 11. Stories

A story represents the normalized real-world topic/event under processing.

Recommended fields:

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
risk_level
confidence_score
cluster_key
metadata
created_at
updated_at
```

`story.verified` is an event/stage semantic, not a claim that all attached propositions are true.

---

# 12. Story Sources

Many-to-many relationship between stories and articles.

Relationship examples:

```text
PRIMARY_REPORT
FOLLOWUP
CORROBORATION
CONTEXT
CONTRADICTION
BACKGROUND
DISCOVERY
```

These values do not by themselves establish source independence.

---

# 13. Claims

Migration `0015_claim_semantics` adds nullable semantic_type (VARCHAR32),
semantic_state (VARCHAR16), semantic_policy_version (VARCHAR64), semantic_ai_run_id
(indexed FK to ai_runs). Closed CHECK vocabularies enforce non-null type/state, and
an all-null/all-present CHECK prevents partial provenance. Historical legacy
claim_type remains unconstrained/readable; no classification backfill is performed.
New extraction attaches actual AI classification provenance without changing status.
Immutable Fact Sheet claim snapshots copy these semantics; historical versions are
not rewritten.

```text
claims
------
id                    UUID PK
story_id              UUID FK → stories.id
claim_text             TEXT NOT NULL
normalized_claim       TEXT
claim_type             TEXT
status                 TEXT NOT NULL -- ClaimVerificationStatus
confidence_score       NUMERIC
importance_score       NUMERIC
risk_level             TEXT -- RiskLevel
temporal_start         TIMESTAMPTZ
temporal_end           TIMESTAMPTZ
location_id            UUID NULL
created_by_ai_run_id   UUID NULL
metadata               JSONB
created_at             TIMESTAMPTZ NOT NULL
updated_at             TIMESTAMPTZ NOT NULL
```

Only canonical `ClaimVerificationStatus` values are valid.

Fact-check labels must never be stored in `claims.status`.

---

# 14. Evidence Items

Evidence items may represent:

```text
court judgment/order
government/police statement
treaty
official statistic/dataset
research paper
article
interview
photograph/video
social post
archived webpage
historical source
```

Recommended fields:

```text
id
source_id
source_type
source_url/document_id
title
published_at
retrieved_at
content_hash
excerpt/reference_location
language
provenance
metadata
created_at
```

Do not store secrets or authorization headers.

---

# 15. Claim Evidence

```text
claim_evidence
--------------
claim_id
 evidence_id
relation
strength_score
notes
created_at
```

Current `claim_evidence` additionally stores nullable `directness`, `origin_role`,
`provenance_state`, `temporal_role`, `semantics_policy_version`, using the closed
`evidence-graph-policy-v1` vocabulary in `SOURCE_AND_RESEARCH.md`. These fields
are all present for current assessments or all NULL for unevaluated history.
No guessed historical backfill is permitted.

`evidence_graph_relations` stores UUID identity, source evidence FK, exactly one
target evidence FK or bounded external reference, closed relation type, basis,
policy version, research Job FK, positive Claim research generation and timestamps.
Self-edges and duplicate source/type/endpoint/policy edges are rejected. Current
research persistence permits resolved targets only in the same Claim's current
collection; Fact Sheet generation rechecks endpoint currency and run/generation.
No edge mutates factual verification state.

Canonical relation vocabulary may include:

```text
DIRECT_SUPPORT
INDIRECT_SUPPORT
CONTRADICTS
QUALIFIES
CONTEXT
PRIMARY_EVIDENCE
SECONDARY_EVIDENCE
```

The relationship is explicit and claim-specific.

---

# 16. Entities and Mentions

Entity types include:

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

`entity_mentions` links canonical entities to source material/stories/claims.

---

# 17. Events and Locations

`events`, `event_locations`, and `event_entities` model real-world events and their participants/places.

Avoid confusing the `events` domain table with Redis event messages.

Temporal precision may be:

```text
EXACT
DAY
MONTH
YEAR
APPROXIMATE
UNKNOWN
```

---

# 18. Historical Events and Sources

Historical source types may include:

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

Historical interpretations must support competing hypotheses and preserve evidence-domain distinctions.

---

# 19. Editorial Rules and Scores

Editorial rules/scores determine:

```text
importance
coverage priority
audience relevance
framing preferences
risk/review routing
```

They must not determine factual truth.

Editorial scoring keeps importance and evidence strength separate.

---

# 20. AI Models, Runs, and Prompts

`ai_models`, `ai_runs`, and `ai_prompts` provide model registry and provenance.

Do not store provider credentials here.

`ai_runs` should identify model/provider/task/prompt version/input/output references/latency/status.

Operational AI route failures are preserved on the existing event reliability records rather than
inventing a single model for a failed multi-provider invocation. Both
`event_processing_attempts.ai_failure_provenance` and
`event_dead_letters.ai_failure_provenance` are nullable JSONB fields containing the sanitized
task/input identity, complete ordered route attempts, final failure reason, and fallback decision.
They are added by migration `0018_ai_failure_provenance` with no default and no historical backfill;
successful `ai_runs` persistence is unchanged.

---

# 21. Fact Checks

Use `label` consistently across database, application schema, and events.

```text
fact_checks
-----------
id                    UUID PK
story_id               UUID FK → stories.id
claim_id               UUID FK → claims.id NULL
label                  TEXT NOT NULL -- FactCheckLabel
confidence_score       NUMERIC
summary                TEXT
reasoning_summary      TEXT
primary_evidence_count INTEGER
supporting_count       INTEGER
contradicting_count    INTEGER
review_required        BOOLEAN
review_state           TEXT -- ReviewState
ai_run_id              UUID FK NULL
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

Do not call this field `status` when it represents `FactCheckLabel`; `label` prevents confusion with claim/workflow status.

---

# 22. Fact Sheets

The Fact Sheet is the canonical factual intermediate representation.

Recommended persistence:

```text
fact_sheets
-----------
id
story_id
version
headline
summary
claims_snapshot          JSONB
fact_checks_snapshot     JSONB
evidence_snapshot        JSONB
sources_snapshot         JSONB
timeline                 JSONB
entities                 JSONB
locations                JSONB
context                  JSONB
counterclaims            JSONB
unresolved_questions     JSONB
confidence_score
risk_level
sensitive_topics         JSONB
ai_run_id
created_at
```

`claims_snapshot` preserves canonical status on every included claim:

```text
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

Optional derived partitions may be materialized for query convenience, but they must not replace the canonical per-claim status or omit refuted/partially-supported claims.

A Fact Sheet version used for publication is immutable.

Corrections create a new version.

---

# 23. Content Drafts

`content_drafts` represents one generated content package for a Fact Sheet/editorial brief.

Recommended fields:

```text
id
story_id
fact_sheet_id
fact_sheet_version
risk_level
sensitive_topics
review_state
created_by_ai_run_id
version
created_at
updated_at
```

---

# 24. Content Variants

`content_variants` stores platform/format/language variants.

Recommended fields:

```text
id
content_draft_id
platform
format
language
body/caption/title
structured_payload
claim_ids_used
source_ids_used
media_asset_ids
review_state
version
created_at
updated_at
```

A material edit after approval must not silently retain approval for the previous version.

`content_quality_checks` additionally persists nullable
`semantic_validation_passed`, `semantic_methodology_version`, and
`semantic_findings` (JSONB containing the entire typed semantic report, including
findings, quote matches and check counts). Quality methodology v4 and later rows contain
the deterministic report. Historical rows retain NULL in all three columns:
NULL means not evaluated, not a fabricated pass or semantic-validator-v1 result.
Migration `0013_semantic_validation` adds no fabricated historical backfill.

Migration `0014_certainty_firewall` adds nullable JSONB `certainty_escalations` to
ContentQualityCheck. New quality v5 assessments persist typed AI prose findings
(an empty array means evaluated with no escalations). Historical NULL means not
evaluated; no backfill or historical methodology change is performed. Deterministic
certainty findings remain in the typed semantic_findings report. Review detail
exposes both exact durable results without recomputation. Claim presentations are
part of ContentVariant structured_payload, not a second factual truth table.
The quality semantic key binds exact content, Fact Sheet/version, and both quality
and semantic methodology identities. Historical checks remain immutable.

---

# 25. Media Assets

`0015_claim_semantics` also adds nullable JSONB
`content_quality_checks.claim_semantic_escalations`. New v6 assessments store typed
prose findings ([] means evaluated, none found); historical NULL means not evaluated.
Deterministic CLAIM_SEMANTICS findings remain in semantic_findings, alongside PR1/PR2
reports. ClaimSemanticPresentation is inside immutable ContentVariant structured_payload,
not a second truth table. Review detail exposes exact rows without recomputation.

Recommended fields:

```text
id
asset_type
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
created_at
updated_at
```

Local storage does not imply public accessibility.

Current generated-image dimensions, provider/model identity, prompt version, and `ai_run_id` are
retained in `source_metadata`; the final watermarked JPEG hash remains the immutable byte identity.
The referenced `AIRun` records `IMAGE_GENERATION`, exact input hash, safe provider request
provenance, and validated output metadata. Historical caller-owned assets remain valid and do not
receive fabricated generation provenance.

---

# 26. Social Accounts

Store:

```text
id
platform
account_name
account_identifier
status
credential_reference
capabilities
rate_limit_state
metadata
created_at
updated_at
```

Never store raw tokens in ordinary plaintext application tables.

---

# 27. Publications

```text
publications
------------
id
content_variant_id
social_account_id
status -- PublicationStatus
scheduled_at
started_at
published_at
external_post_id
external_url
failure_reason
created_at
updated_at
```

For the MVP, external publication must not reach eligible `SCHEDULED` state without explicit human approval for the exact content version.

---

# 28. Publication Attempts

Each external attempt is a separate durable record.

```text
id
publication_id
attempt_number
started_at
completed_at
status
provider_response_metadata
error_code
error_class
created_at
```

Do not overwrite attempt history.

---

# 29. Analytics Snapshots

Store timestamped snapshots rather than one mutable counter row.

```text
id
publication_id
captured_at
metrics
provider_metadata
```

---

# 30. Jobs and Job Attempts

`jobs` stores durable async work state.

`job_attempts` stores individual attempts.

Redis delivery never replaces these durable records.

Useful job fields:

```text
job_type
priority
status
attempt_count
scheduled_at
started_at
completed_at
result_reference
last_error
```

---

# 31. Audit Log

Audit significant human/system actions:

```text
review approval/rejection/change request
source enable/disable
policy/config override
Fact Sheet correction
publication scheduling/cancellation/retry
credential/account administrative action
```

Approval audit must preserve:

```text
actor
artifact type/id
exact version
decision
timestamp
reason where applicable
```

---

# 32. Evidence Packet

A publication candidate must be reconstructable with:

```text
story
claims
sources
supporting evidence
contradictory evidence
primary evidence
counterclaims
Fact Sheet
content variant
risk/sensitivity
AI provenance
human review
publication history
```

The packet may be reconstructed from normalized tables rather than duplicated as one giant object.

---

# 33. Sensitive-Topic Metadata

Sensitivity is separate from `RiskLevel`.

Example:

```json
{
  "sensitive_topics": ["COMMUNAL_VIOLENCE", "SC_ST_ALLEGATION"],
  "risk_level": "HIGH"
}
```

Sensitive-topic metadata drives stricter research/review policy but must not change factual truth.

---

# 34. Demographic Data

Demographic records/Fact Sheets should preserve distinctions among:

```text
OBSERVED_DATA
STATISTICAL_INTERPRETATION
POSSIBLE_CAUSES
CAUSAL_EVIDENCE
EDITORIAL_INTERPRETATION
```

Do not automatically convert demographic change into communal causation/blame.

---

# 35. Civilizational Taxonomy

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

Distinct identities remain distinct.

---

# 36. JSONB

Use JSONB for evolving/provider-specific/snapshot structures where relational constraints would otherwise be brittle.

Do not use JSONB as a substitute for relational modeling of frequently queried integrity-critical relationships such as claim/evidence/publication ownership.

---

# 37. Indexing

Index based on measured query patterns.

Likely early indexes include:

```text
articles(source_id, canonical_url)
stories(last_updated_at)
claims(story_id, status)
claim_evidence(claim_id)
fact_checks(story_id, label)
fact_sheets(story_id, version)
content_variants(content_draft_id, platform)
publications(status, scheduled_at)
jobs(status, priority)
```

Use GIN selectively for JSONB/full-text patterns where justified.

---

# 38. Foreign Keys and Uniqueness

Prefer restrictive deletion for provenance-critical relationships.

Important uniqueness examples:

```text
(source_id, canonical_url)
(article_id, version_number)
(story_id, article_id)
(story_id, fact_sheet_version)
(publication_id, attempt_number)
```

---

# 39. Transaction Boundaries

Important state changes should be transactional.

Do not hold long database transactions open around external API calls.

Preferred pattern:

```text
PostgreSQL state/outbox
    ↓
commit
    ↓
external work
    ↓
PostgreSQL result
```

---

# 40. Idempotency

All external side effects must be idempotent where practical.

Publication idempotency relies on canonical publication state, attempt history, and verification of ambiguous external outcomes.

---

# 41. Optimistic Concurrency

Versioned mutable editorial/content artifacts should use optimistic concurrency or equivalent checks to prevent silent overwrites.

---

# 42. Retention

Long-term retention should favor:

```text
claims/evidence
Fact Sheets
reviews
publications/attempts
corrections
audit history
```

AI raw responses and temporary source/media data may have separate retention policies.

---

# 43. Migrations

Every schema change must use a versioned migration.

Recommended migration tool:

```text
Alembic
```

Migrations are tested against representative data before production use.

---

# 44. Final Data Rules

```text
PostgreSQL is durable truth.
Redis is not durable business truth.
Claim.status uses ClaimVerificationStatus only.
FactCheck.label uses FactCheckLabel only.
Use label consistently instead of fact-check status terminology.
Fact Sheet stores every material claim status, including PARTIALLY_SUPPORTED and REFUTED.
UNVERIFIED != REFUTED.
UNVERIFIED != FALSE.
Fact Sheets used for publication are immutable versions.
Publication approval is version-specific and mandatory for MVP external publishing.
Evidence/source provenance remains reconstructable.
```

---

## Value integrity persistence — 0016_value_integrity

Claims add nullable JSONB value_anchors, VARCHAR64 value_policy_version, and indexed
UUID value_ai_run_id FK to ai_runs. All three must be SQL NULL or all non-NULL.
Non-NULL value_anchors must be a JSON array. [] plus current policy/AIRun is evaluated
with no material values; SQL NULL is unevaluated history. No historical backfill/default.
ContentQualityCheck adds nullable JSONB value_escalations. Historical SQL NULL means
not evaluated; current v7 [] means evaluated without prose findings. Deterministic
VALUE_INTEGRITY findings remain in the existing typed semantic_findings report.
Value presentation metadata is inside immutable ContentVariant structured_payload;
there is no new truth table. Exact reviewed/hash bindings and historical methodologies remain intact.
Downgrade removes only these new columns, checks, FK and index, restoring 0015 schema.
