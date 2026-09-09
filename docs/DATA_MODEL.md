# News AI Social Media Manager — Data Model

## 1. Purpose

This document defines the canonical PostgreSQL data model for the News AI Social Media Manager.

It is the implementation reference for:

* database schema
* entity relationships
* provenance
* claims and evidence
* story clustering
* editorial scoring
* AI execution
* fact checking
* content generation
* human review
* social publishing
* jobs and retries
* analytics
* auditing

The database is the **system of record**.

Redis Streams is the event transport layer, not the source of truth.

---

# 2. Database Principles

## 2.1 PostgreSQL is authoritative

Persistent business state MUST live in PostgreSQL.

Redis MUST NOT be treated as the canonical store for:

* stories
* claims
* evidence
* fact checks
* generated content
* approvals
* publications
* credentials
* audit history

Redis may contain:

* queues
* transient processing state
* stream messages
* locks
* short-lived caches

---

## 2.2 Provenance is mandatory

Every important factual object should be traceable to its origin.

The system should be able to answer:

> Where did this claim come from?

> Which sources support it?

> Which sources contradict it?

> Which model produced this interpretation?

> Who approved the final content?

---

## 2.3 IDs

Use UUIDs for externally meaningful database records.

Recommended PostgreSQL type:

```sql
uuid
```

Generate UUIDs application-side or with PostgreSQL UUID support.

Use database-generated timestamps where practical.

---

## 2.4 Timestamps

All timestamps MUST be stored as:

```sql
TIMESTAMPTZ
```

Never store server-local timestamps as the canonical representation.

The application may render timestamps in:

```text
Asia/Kolkata
```

or another user-configured timezone.

---

## 2.5 Soft deletion

Records that participate in provenance or audit history should generally not be physically deleted.

Preferred approach:

```text
deleted_at TIMESTAMPTZ NULL
```

Historical evidence, publications, AI runs and audit records should remain immutable.

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

AI execution and jobs connect across the pipeline:

```text
STORY
  │
  ├── AI_RUN
  ├── JOB
  ├── AI_MODEL
  └── AI_PROMPT
```

All important mutations can be represented in:

```text
AUDIT_LOG
```

---

# 4. PostgreSQL Extensions

Recommended extensions:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

`pgcrypto` may be used for UUID generation and cryptographic helpers.

If vector similarity is introduced later:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Do NOT make `pgvector` mandatory for MVP.

Story similarity can initially use:

* normalized text
* named entities
* source/time/location overlap
* lightweight embeddings
* application-side similarity

---

# 5. Enumerations

Prefer explicit application enums backed by PostgreSQL enums only where the state is stable.

For rapidly evolving AI metadata, use strings.

Recommended enums include:

```text
article_status
story_status
claim_status
evidence_type
evidence_strength
fact_check_status
review_status
content_status
publication_status
publication_attempt_status
job_status
source_type
risk_level
```

Example:

```sql
CREATE TYPE story_status AS ENUM (
    'DISCOVERED',
    'PROCESSING',
    'VERIFICATION_REQUIRED',
    'VERIFIED',
    'READY_FOR_CONTENT',
    'IN_REVIEW',
    'APPROVED',
    'PUBLISHED',
    'REJECTED',
    'ARCHIVED'
);
```

---

# 6. Sources

## 6.1 `sources`

Represents an external information source.

Examples:

* newspaper
* government website
* court
* academic journal
* official social account
* research organization
* RSS publisher

Schema:

```text
sources
---------
id                  UUID PK
name                TEXT NOT NULL
domain              TEXT
base_url             TEXT
source_type          TEXT NOT NULL
authority_level      SMALLINT
country              TEXT
language              TEXT
description          TEXT
is_active             BOOLEAN NOT NULL DEFAULT TRUE
metadata              JSONB
created_at            TIMESTAMPTZ NOT NULL
updated_at            TIMESTAMPTZ NOT NULL
```

### Authority levels

```text
1 = primary
2 = established
3 = specialist/research
4 = discovery/social
```

This field represents source role, not absolute truth.

A Level 1 source can still be incorrect.

---

# 7. Source Feeds

## 7.1 `source_feeds`

Represents a mechanism through which articles are collected.

Examples:

* RSS
* Atom
* API
* sitemap
* webpage
* social API

```text
source_feeds
------------
id                  UUID PK
source_id            UUID FK → sources.id
name                 TEXT NOT NULL
feed_url              TEXT
feed_type             TEXT NOT NULL
poll_interval_seconds INTEGER
last_polled_at        TIMESTAMPTZ
etag                  TEXT
last_modified         TEXT
is_active             BOOLEAN NOT NULL DEFAULT TRUE
configuration         JSONB
created_at            TIMESTAMPTZ NOT NULL
updated_at            TIMESTAMPTZ NOT NULL
```

---

# 8. Articles

## 8.1 `articles`

Represents a discovered source article.

```text
articles
--------
id                  UUID PK
source_id            UUID FK
source_feed_id       UUID FK NULL
canonical_url        TEXT NOT NULL
original_url         TEXT
title                TEXT
author               TEXT
published_at          TIMESTAMPTZ
discovered_at         TIMESTAMPTZ NOT NULL
language              TEXT
content_hash         TEXT
title_hash           TEXT
status                TEXT
raw_metadata         JSONB
created_at            TIMESTAMPTZ NOT NULL
updated_at            TIMESTAMPTZ NOT NULL
```

Unique constraint:

```text
(source_id, canonical_url)
```

---

# 9. Article Versions

## 9.1 `article_versions`

Articles may change after publication.

Store versions rather than silently replacing historical content.

```text
article_versions
----------------
id                  UUID PK
article_id           UUID FK → articles.id
version_number       INTEGER NOT NULL
title                TEXT
body_text            TEXT
summary              TEXT
author               TEXT
published_at         TIMESTAMPTZ
retrieved_at         TIMESTAMPTZ NOT NULL
content_hash         TEXT
metadata              JSONB
created_at            TIMESTAMPTZ NOT NULL
```

Unique:

```text
(article_id, version_number)
```

---

# 10. Stories

## 10.1 `stories`

A story represents the normalized real-world event/topic being tracked.

Multiple articles may belong to one story.

```text
stories
-------
id                    UUID PK
canonical_headline     TEXT
summary                TEXT
status                 TEXT NOT NULL
language                TEXT
first_seen_at          TIMESTAMPTZ
last_updated_at        TIMESTAMPTZ
importance_score       NUMERIC
evidence_strength      NUMERIC
controversy_score      NUMERIC
risk_score             NUMERIC
confidence_score       NUMERIC
cluster_key            TEXT
metadata                JSONB
created_at              TIMESTAMPTZ NOT NULL
updated_at              TIMESTAMPTZ NOT NULL
```

---

# 11. Story Sources

## 11.1 `story_sources`

Many-to-many relationship between stories and articles.

```text
story_sources
-------------
story_id              UUID FK
article_id            UUID FK
relationship_type     TEXT
relevance_score       NUMERIC
added_at              TIMESTAMPTZ
```

Primary key:

```text
(story_id, article_id)
```

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

## 12.1 `claims`

A claim is a discrete factual proposition.

Examples:

```text
"Government X announced policy Y."

"Person X was arrested on date Y."

"Court X issued order Y."

"Population statistic changed from A to B."
```

Schema:

```text
claims
------
id                    UUID PK
story_id              UUID FK
claim_text             TEXT NOT NULL
normalized_claim       TEXT
claim_type             TEXT
status                 TEXT
confidence_score       NUMERIC
importance_score       NUMERIC
risk_level             TEXT
temporal_start         TIMESTAMPTZ
temporal_end           TIMESTAMPTZ
location_id            UUID NULL
created_by_ai_run_id   UUID NULL
metadata               JSONB
created_at             TIMESTAMPTZ NOT NULL
updated_at             TIMESTAMPTZ NOT NULL
```

---

# 13. Claim Status

Recommended values:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
FALSE
FABRICATED
OUT_OF_CONTEXT
SATIRE
```

Important:

```text
UNVERIFIED != FALSE
```

Lack of evidence is not automatically evidence of falsity.

---

# 14. Evidence Items

## 14.1 `evidence_items`

Represents an individual piece of evidence.

Examples:

* court judgment
* government release
* police statement
* original research paper
* official statistics
* article
* interview
* photograph
* video
* social post
* archived webpage

```text
evidence_items
--------------
id                    UUID PK
source_id             UUID FK NULL
evidence_type         TEXT NOT NULL
title                 TEXT
url                   TEXT
publisher             TEXT
published_at          TIMESTAMPTZ
retrieved_at          TIMESTAMPTZ NOT NULL
content_excerpt       TEXT
content_hash          TEXT
authority_level       SMALLINT
strength               TEXT
language              TEXT
metadata              JSONB
created_at            TIMESTAMPTZ NOT NULL
```

---

# 15. Claim Evidence

## 15.1 `claim_evidence`

Links evidence to claims.

```text
claim_evidence
--------------
claim_id              UUID FK
evidence_id           UUID FK
relationship           TEXT NOT NULL
strength_score        NUMERIC
supports_claim        BOOLEAN
notes                 TEXT
created_at            TIMESTAMPTZ NOT NULL
```

Relationship examples:

```text
DIRECT_SUPPORT
INDIRECT_SUPPORT
CONTRADICTS
CONTEXT
PRIMARY_EVIDENCE
SECONDARY_EVIDENCE
```

Primary key:

```text
(claim_id, evidence_id)
```

---

# 16. Evidence Provenance

Evidence should preserve enough information to reconstruct how it entered the system.

Recommended metadata:

```json
{
  "collector": "rss",
  "collector_version": "1.0.0",
  "retrieval_method": "http",
  "http_status": 200,
  "content_hash": "...",
  "canonical_url": "...",
  "archive_url": null
}
```

Do not store credentials or authorization headers.

---

# 17. Entities

## 17.1 `entities`

Canonical entities referenced by stories and claims.

```text
entities
--------
id                    UUID PK
entity_type            TEXT NOT NULL
canonical_name         TEXT NOT NULL
normalized_name        TEXT
description            TEXT
country                TEXT
language               TEXT
external_ids            JSONB
metadata                JSONB
created_at             TIMESTAMPTZ NOT NULL
updated_at             TIMESTAMPTZ NOT NULL
```

Entity types:

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

---

# 18. Entity Mentions

## 18.1 `entity_mentions`

Links entities to source material.

```text
entity_mentions
---------------
id                    UUID PK
entity_id             UUID FK
article_id            UUID FK NULL
story_id              UUID FK NULL
claim_id              UUID FK NULL
mention_text          TEXT
confidence_score      NUMERIC
context               TEXT
created_at            TIMESTAMPTZ
```

---

# 19. Events

## 19.1 `events`

Represents a concrete event extracted from reporting.

```text
events
------
id                    UUID PK
story_id              UUID FK
event_type            TEXT
title                 TEXT
description           TEXT
start_time            TIMESTAMPTZ
end_time              TIMESTAMPTZ
precision              TEXT
confidence_score      NUMERIC
metadata              JSONB
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

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

# 20. Event Locations

## 20.1 `event_locations`

```text
event_locations
---------------
event_id               UUID FK
name                   TEXT
country                TEXT
region                 TEXT
city                   TEXT
latitude               NUMERIC
longitude              NUMERIC
precision              TEXT
metadata               JSONB
```

Do not require coordinates for every event.

---

# 21. Event Entities

## 21.1 `event_entities`

```text
event_entities
--------------
event_id               UUID FK
entity_id              UUID FK
role                   TEXT
confidence_score       NUMERIC
```

Example roles:

```text
ACTOR
TARGET
LOCATION
ORGANIZER
VICTIM
ACCUSED
RESPONDENT
AUTHORITY
WITNESS
```

---

# 22. Historical Events

## 22.1 `historical_events`

Historical research requires a richer evidence model than ordinary breaking news.

```text
historical_events
-----------------
id                       UUID PK
title                    TEXT NOT NULL
date_start                TEXT
date_end                  TEXT
date_precision            TEXT
location                  TEXT
summary                   TEXT
confidence_score          NUMERIC
metadata                  JSONB
created_at                TIMESTAMPTZ
updated_at                TIMESTAMPTZ
```

Date fields intentionally permit uncertain historical chronology.

Examples:

```text
"c. 1500 BCE"
"2nd millennium BCE"
"late medieval period"
```

---

# 23. Historical Sources

## 23.1 `historical_sources`

```text
historical_sources
------------------
id                       UUID PK
historical_event_id      UUID FK
source_type              TEXT
title                    TEXT
author                    TEXT
date_text                 TEXT
url                       TEXT
description               TEXT
reliability_assessment    TEXT
metadata                  JSONB
created_at                TIMESTAMPTZ
```

Possible source types:

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

---

# 24. Historical Interpretation

Historical claims should support multiple interpretations.

Example JSON:

```json
{
  "hypotheses": [
    {
      "id": "H1",
      "description": "Population movement contributed to language spread.",
      "supporting_evidence": [],
      "contradicting_evidence": [],
      "confidence": 0.64
    },
    {
      "id": "H2",
      "description": "Alternative model emphasizing local continuity.",
      "supporting_evidence": [],
      "contradicting_evidence": [],
      "confidence": 0.31
    }
  ]
}
```

The system MUST NOT reduce complex historical controversies to a binary editorial switch.

Separate evidence domains:

```text
LANGUAGE
ARCHAEOLOGY
GENETICS
LITERARY_SOURCES
EPIGRAPHY
MATERIAL_CULTURE
CHRONOLOGY
POPULATION_MOVEMENT
CULTURAL_CHANGE
MILITARY_CONQUEST
```

---

# 25. Editorial Rules

## 25.1 `editorial_rules`

Stores configurable editorial priorities.

```text
editorial_rules
---------------
id                    UUID PK
rule_key              TEXT UNIQUE
category              TEXT
priority              NUMERIC
enabled               BOOLEAN
configuration         JSONB
version               INTEGER
created_at            TIMESTAMPTZ
updated_at            TIMESTAMPTZ
```

Examples:

```text
india_governance
indian_civilization
religious_freedom
history
fact_check
geopolitics
```

Editorial rules determine:

* what receives attention
* ranking
* framing preferences
* review requirements

They MUST NOT determine whether evidence is considered true.

---

# 26. Editorial Scores

## 26.1 `editorial_scores`

Stores story-level scoring.

```text
editorial_scores
----------------
id                       UUID PK
story_id                 UUID FK
importance                NUMERIC
india_relevance           NUMERIC
geopolitical_relevance    NUMERIC
civilizational_relevance NUMERIC
religious_relevance      NUMERIC
historical_relevance     NUMERIC
fact_check_value          NUMERIC
breaking_news_score       NUMERIC
audience_interest         NUMERIC
evidence_strength         NUMERIC
controversy               NUMERIC
publication_risk          NUMERIC
final_score               NUMERIC
model_version             TEXT
created_at                TIMESTAMPTZ
```

Important:

```text
editorial priority != evidence strength
```

---

# 27. AI Models

## 27.1 `ai_models`

```text
ai_models
---------
id                    UUID PK
provider              TEXT NOT NULL
model_name            TEXT NOT NULL
model_type            TEXT
version               TEXT
local_or_cloud        TEXT
capabilities           JSONB
context_window         INTEGER
enabled                BOOLEAN
configuration          JSONB
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

Examples:

```text
qwen-local
llama-local
openai
gemini
claude
```

Do not store provider API keys here.

---

# 28. AI Runs

## 28.1 `ai_runs`

Every significant AI operation should be traceable.

```text
ai_runs
-------
id                    UUID PK
model_id               UUID FK
task_type              TEXT NOT NULL
input_hash             TEXT
input_reference        JSONB
output                 JSONB
prompt_version         TEXT
temperature            NUMERIC
tokens_input           INTEGER
tokens_output          INTEGER
latency_ms             INTEGER
status                 TEXT
error_message          TEXT
created_at             TIMESTAMPTZ
completed_at           TIMESTAMPTZ
```

Examples of task types:

```text
CLASSIFICATION
ENTITY_EXTRACTION
CLAIM_EXTRACTION
SUMMARIZATION
STORY_CLUSTERING
RESEARCH_SYNTHESIS
FACT_CHECK
FACT_SHEET
CONTENT_GENERATION
QUALITY_CHECK
```

Sensitive raw prompts should not automatically be persisted indefinitely.

---

# 29. AI Prompts

## 29.1 `ai_prompts`

Version-controlled prompt metadata.

```text
ai_prompts
----------
id                    UUID PK
prompt_key             TEXT NOT NULL
version                TEXT NOT NULL
task_type              TEXT
template               TEXT NOT NULL
configuration          JSONB
is_active              BOOLEAN
created_at             TIMESTAMPTZ
```

Prompts should also exist in Git where practical.

Database records provide runtime provenance.

---

# 30. Fact Checks

## 30.1 `fact_checks`

```text
fact_checks
-----------
id                    UUID PK
story_id               UUID FK
claim_id               UUID FK NULL
status                 TEXT NOT NULL
confidence_score       NUMERIC
summary                TEXT
reasoning_summary      TEXT
primary_evidence_count INTEGER
supporting_count       INTEGER
contradicting_count    INTEGER
review_required        BOOLEAN
review_status          TEXT
ai_run_id              UUID FK NULL
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

The system should distinguish:

```text
automated assessment
```

from:

```text
human-approved assessment
```

---

# 31. Fact Sheets

The fact sheet is the canonical intermediate representation between research and content generation.

A dedicated table is recommended.

## 31.1 `fact_sheets`

```text
fact_sheets
-----------
id                    UUID PK
story_id               UUID FK
version                INTEGER
headline               TEXT
summary                TEXT
verified_claims        JSONB
disputed_claims        JSONB
unverified_claims      JSONB
evidence               JSONB
timeline               JSONB
entities               JSONB
locations              JSONB
context                JSONB
counterclaims          JSONB
confidence_score       NUMERIC
risk_level             TEXT
source_snapshot        JSONB
ai_run_id              UUID FK NULL
created_at             TIMESTAMPTZ
```

Unique:

```text
(story_id, version)
```

A fact sheet should be immutable after publication.

If corrections are required, create a new version.

---

# 32. Content Drafts

## 32.1 `content_drafts`

Represents a generated content package.

```text
content_drafts
--------------
id                    UUID PK
story_id               UUID FK
fact_sheet_id          UUID FK
status                 TEXT
requested_by           TEXT
generation_ai_run_id   UUID FK NULL
editorial_notes        TEXT
risk_level             TEXT
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

# 33. Content Variants

One story may generate multiple platform-specific outputs.

## 33.1 `content_variants`

```text
content_variants
----------------
id                    UUID PK
content_draft_id       UUID FK
platform               TEXT
format                 TEXT
language               TEXT
title                  TEXT
body                   TEXT
caption                TEXT
hashtags               JSONB
thread                 JSONB
slides                 JSONB
video_script           TEXT
metadata               JSONB
version                INTEGER
status                 TEXT
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

Possible platforms:

```text
INSTAGRAM
X
FACEBOOK
TELEGRAM
YOUTUBE
```

Possible formats:

```text
POST
CAROUSEL
REEL
THREAD
SHORT
MESSAGE
```

---

# 34. Media Assets

## 34.1 `media_assets`

```text
media_assets
-----------
id                    UUID PK
content_draft_id       UUID FK NULL
content_variant_id     UUID FK NULL
asset_type              TEXT
storage_provider        TEXT
storage_key             TEXT
public_url              TEXT
mime_type               TEXT
width                   INTEGER
height                  INTEGER
duration_ms             INTEGER
file_hash               TEXT
generation_ai_run_id    UUID FK NULL
source_metadata         JSONB
visual_check_status     TEXT
created_at              TIMESTAMPTZ
updated_at              TIMESTAMPTZ
```

Supported asset types may include:

```text
IMAGE
VIDEO
AUDIO
SLIDE
THUMBNAIL
```

---

# 35. Social Accounts

## 35.1 `social_accounts`

Represents a connected publishing account.

```text
social_accounts
---------------
id                    UUID PK
platform              TEXT NOT NULL
account_name          TEXT
external_account_id   TEXT
status                TEXT
credential_reference  TEXT
metadata              JSONB
created_at            TIMESTAMPTZ
updated_at            TIMESTAMPTZ
```

Never store raw access tokens here.

`credential_reference` points to a secure secret mechanism.

---

# 36. Publications

## 36.1 `publications`

Represents the intended publication.

```text
publications
------------
id                    UUID PK
content_variant_id     UUID FK
social_account_id      UUID FK
status                 TEXT
scheduled_at           TIMESTAMPTZ
published_at           TIMESTAMPTZ
external_post_id       TEXT
external_url           TEXT
failure_reason         TEXT
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

# 37. Publication Attempts

## 37.1 `publication_attempts`

Retries must be separately recorded.

```text
publication_attempts
--------------------
id                    UUID PK
publication_id         UUID FK
attempt_number         INTEGER
started_at             TIMESTAMPTZ
completed_at           TIMESTAMPTZ
status                 TEXT
http_status            INTEGER
provider_request_id    TEXT
response_metadata      JSONB
error_code             TEXT
error_message          TEXT
created_at             TIMESTAMPTZ
```

This enables debugging without overwriting previous attempts.

---

# 38. Analytics Snapshots

## 38.1 `analytics_snapshots`

```text
analytics_snapshots
-------------------
id                    UUID PK
publication_id         UUID FK
platform               TEXT
captured_at            TIMESTAMPTZ
impressions            BIGINT
reach                   BIGINT
likes                   BIGINT
comments                BIGINT
shares                  BIGINT
saves                   BIGINT
clicks                  BIGINT
views                   BIGINT
engagement_rate        NUMERIC
metadata                JSONB
created_at              TIMESTAMPTZ
```

Analytics are snapshots rather than mutable counters.

---

# 39. Jobs

## 39.1 `jobs`

Represents asynchronous work.

```text
jobs
----
id                    UUID PK
job_type               TEXT NOT NULL
entity_type            TEXT
entity_id              UUID
status                 TEXT NOT NULL
priority               INTEGER
attempt_count          INTEGER
max_attempts           INTEGER
scheduled_at           TIMESTAMPTZ
started_at             TIMESTAMPTZ
completed_at           TIMESTAMPTZ
locked_at              TIMESTAMPTZ
worker_id              TEXT
payload                JSONB
result                 JSONB
error_message          TEXT
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

---

# 40. Job Attempts

## 40.1 `job_attempts`

```text
job_attempts
------------
id                    UUID PK
job_id                 UUID FK
attempt_number         INTEGER
worker_id              TEXT
started_at             TIMESTAMPTZ
completed_at           TIMESTAMPTZ
status                 TEXT
error_message          TEXT
metrics                JSONB
created_at             TIMESTAMPTZ
```

---

# 41. Audit Log

## 41.1 `audit_log`

The audit log records meaningful human/system actions.

```text
audit_log
---------
id                    UUID PK
actor_type             TEXT
actor_id               UUID NULL
action                 TEXT NOT NULL
entity_type            TEXT NOT NULL
entity_id              UUID
before_state           JSONB
after_state            JSONB
reason                 TEXT
request_id             TEXT
ip_hash                TEXT NULL
created_at             TIMESTAMPTZ NOT NULL
```

Possible actors:

```text
USER
SYSTEM
AI
WORKER
SCHEDULER
```

Examples:

```text
STORY_APPROVED
STORY_REJECTED
FACT_CHECK_OVERRIDDEN
CONTENT_EDITED
PUBLICATION_APPROVED
PUBLICATION_CANCELLED
SOURCE_DISABLED
EDITORIAL_RULE_CHANGED
```

---

# 42. Evidence Packet

Every publication candidate should be reconstructable as an evidence packet.

Logical structure:

```json
{
  "story": {},
  "claims": [],
  "sources": [],
  "primary_evidence": [],
  "supporting_evidence": [],
  "contradicting_evidence": [],
  "timeline": [],
  "entities": [],
  "locations": [],
  "counterclaims": [],
  "fact_check": {},
  "confidence": 0.82,
  "risk": "MEDIUM",
  "editorial_angle": {},
  "ai_runs": [],
  "human_review": {}
}
```

The packet does not necessarily need to be stored as one giant JSON document.

It should be reconstructable from normalized tables.

---

# 43. Sensitive-Topic Metadata

Stories and claims may contain:

```json
{
  "sensitive_topics": [
    "COMMUNAL_VIOLENCE",
    "RELIGIOUS_ALLEGATION",
    "SC_ST_ALLEGATION",
    "SEXUAL_ASSAULT",
    "TERRORISM",
    "WAR_CASUALTIES",
    "ELECTION_FRAUD"
  ]
}
```

Sensitive topics trigger stricter quality gates.

Do not infer sensitive classifications from demographic identity alone.

---

# 44. Demographic Data

Demographic information must distinguish:

```text
OBSERVED_DATA
STATISTICAL_INTERPRETATION
POSSIBLE_CAUSES
EDITORIAL_INTERPRETATION
```

Example JSON:

```json
{
  "observed": {
    "population_change": 0.12
  },
  "statistical_interpretation": {
    "confidence": 0.88
  },
  "possible_causes": [
    {
      "description": "...",
      "confidence": 0.42
    }
  ],
  "editorial_interpretation": null
}
```

Never automatically convert demographic change into communal blame.

---

# 45. Civilizational Taxonomy

Use separate identities while allowing civilizational grouping.

Recommended taxonomy:

```text
INDIC_CIVILIZATIONAL_CONTEXT
├── HINDU_TRADITIONS
├── BUDDHIST_TRADITIONS
├── JAIN_TRADITIONS
├── SIKH_TRADITIONS
├── INDIGENOUS_REGIONAL_TRADITIONS
└── ANCIENT_INDIAN_CULTURAL_TRADITIONS
```

This allows shared historical context without collapsing distinct religious identities.

---

# 46. Indexing Strategy

Indexes should follow actual query patterns.

Initial indexes:

```text
articles(canonical_url)
articles(source_id, published_at)
articles(content_hash)

stories(status)
stories(last_updated_at)
stories(final_score)

story_sources(article_id)
story_sources(story_id)

claims(story_id)
claims(status)
claims(risk_level)

claim_evidence(claim_id)
claim_evidence(evidence_id)

evidence_items(source_id)
evidence_items(published_at)

entity_mentions(entity_id)
entity_mentions(story_id)

editorial_scores(story_id)
editorial_scores(final_score)

ai_runs(model_id)
ai_runs(task_type)
ai_runs(created_at)

content_drafts(story_id)
content_drafts(status)

content_variants(content_draft_id)
content_variants(platform, status)

publications(status)
publications(scheduled_at)

publication_attempts(publication_id)

jobs(status)
jobs(scheduled_at)
jobs(job_type)

audit_log(entity_type, entity_id)
audit_log(created_at)
```

---

# 47. Partial Indexes

Use partial indexes for active queues.

Example:

```sql
CREATE INDEX idx_jobs_pending
ON jobs (priority DESC, scheduled_at)
WHERE status IN ('PENDING', 'RETRYING');
```

Similarly:

```sql
CREATE INDEX idx_publications_scheduled
ON publications (scheduled_at)
WHERE status = 'SCHEDULED';
```

Avoid indexing every JSONB field.

---

# 48. JSONB Strategy

Use JSONB for:

* provider metadata
* AI outputs
* source-specific metadata
* evolving configuration
* external API responses
* experimental fields

Do NOT use JSONB as an excuse to avoid relational modeling.

If a field is:

* queried frequently
* joined frequently
* indexed frequently
* part of business logic
* required for integrity

it should usually become a proper column.

---

# 49. Foreign-Key Rules

Recommended defaults:

```text
story → article
claim → story
claim_evidence → claim/evidence
entity_mention → entity
event → story
fact_check → story/claim
content_draft → story/fact_sheet
content_variant → content_draft
publication → content_variant/social_account
publication_attempt → publication
analytics_snapshot → publication
job → optional entity
```

Prefer:

```text
ON DELETE RESTRICT
```

for provenance-critical relationships.

Do not cascade-delete evidence chains accidentally.

---

# 50. Uniqueness Rules

Recommended uniqueness:

```text
sources.name + domain

source_feeds.source_id + feed_url

articles.source_id + canonical_url

article_versions.article_id + version_number

story_sources.story_id + article_id

historical_sources.historical_event_id + title

fact_sheets.story_id + version

content_variants.content_draft_id + platform + format + version

publication_attempts.publication_id + attempt_number
```

---

# 51. Idempotency

All external side effects must be idempotent where possible.

Recommended idempotency keys:

```text
article ingestion
story clustering
content generation
publication
analytics collection
```

Example:

```text
publication:
social_account_id + content_variant_id
```

Before publishing:

```text
1. Check whether an equivalent publication already exists.
2. Check publication status.
3. Check external provider state where supported.
4. Publish only if safe.
5. Persist external ID immediately.
```

---

# 52. Transaction Boundaries

Important state transitions should be transactional.

Example:

```text
Approve content
    ↓
transaction
    ├── content status = APPROVED
    ├── publication created
    └── audit event written
```

Do not perform an external API call inside a long PostgreSQL transaction.

External publication should use:

```text
database state
→ job
→ external API
→ database result
```

---

# 53. Optimistic Concurrency

Human review interfaces should prevent silent overwrites.

Use:

```text
updated_at
```

or preferably:

```text
version INTEGER
```

for mutable editorial objects.

Example:

```text
Reviewer A opens version 4
Reviewer B edits version 4
Reviewer A attempts save
→ conflict
```

The system should ask the reviewer to reload/merge.

---

# 54. Retention

Suggested retention classes:

```text
SOURCE_METADATA
    long-term

ARTICLES
    long-term

CLAIMS
    long-term

EVIDENCE
    long-term

FACT_CHECKS
    long-term

FACT_SHEETS
    long-term

PUBLICATIONS
    long-term

AUDIT_LOG
    long-term

AI_RUNS
    configurable

RAW_PROVIDER_RESPONSES
    shorter configurable retention

TEMPORARY_JOB_PAYLOADS
    short retention
```

Retention must comply with applicable provider terms and privacy requirements.

---

# 55. Database Migrations

Use versioned migrations.

Recommended tool:

```text
Alembic
```

Migration sequence:

```text
001_extensions
002_sources
003_articles
004_stories
005_claims
006_evidence
007_entities
008_events
009_historical
010_editorial
011_ai
012_fact_checks
013_content
014_social
015_jobs
016_audit
017_indexes
```

Actual migration count may differ.

The important rule is:

```text
Every schema change → migration
```

Never modify production schema manually without recording the change.

---

# 56. SQLAlchemy Organization

Recommended package:

```text
packages/database/
├── base.py
├── session.py
├── models/
│   ├── source.py
│   ├── article.py
│   ├── story.py
│   ├── claim.py
│   ├── evidence.py
│   ├── entity.py
│   ├── event.py
│   ├── historical.py
│   ├── editorial.py
│   ├── ai.py
│   ├── fact_check.py
│   ├── content.py
│   ├── social.py
│   ├── job.py
│   └── audit.py
└── repositories/
```

Keep domain logic out of raw ORM models.

---

# 57. Repository Layer

Services should not scatter SQL throughout the application.

Recommended pattern:

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

Examples:

```text
StoryRepository
ClaimRepository
EvidenceRepository
FactCheckRepository
ContentRepository
PublicationRepository
JobRepository
```

---

# 58. Domain Services

Recommended services:

```text
StoryClusteringService
ClaimExtractionService
EvidenceService
FactCheckService
HistoricalResearchService
EditorialScoringService
FactSheetService
ContentGenerationService
PublicationService
AnalyticsService
```

Repositories handle persistence.

Services handle business logic.

---

# 59. Event References

Redis events should reference database records rather than duplicate large payloads.

Example:

```json
{
  "event": "claims.extracted",
  "story_id": "...",
  "claim_ids": [
    "..."
  ],
  "schema_version": 1
}
```

Avoid putting the entire article or fact sheet into Redis messages.

---

# 60. Database + Event Consistency

Where reliable event emission is required, use an outbox pattern.

Recommended future table:

```text
event_outbox
------------
id
event_type
aggregate_type
aggregate_id
payload
status
created_at
published_at
```

Flow:

```text
PostgreSQL transaction
    ├── business state update
    └── outbox event

        ↓

Outbox publisher

        ↓

Redis Streams
```

This prevents:

```text
database updated
BUT
event lost
```

---

# 61. Initial Core Relationships

```text
Source
  └──< SourceFeed

Source
  └──< Article
          └──< ArticleVersion

Story
  └──< StorySource >── Article

Story
  ├──< Claim
  │      └──< ClaimEvidence >── EvidenceItem
  │
  ├──< EntityMention >── Entity
  │
  ├──< Event
  │      ├──< EventLocation
  │      └──< EventEntity >── Entity
  │
  ├──< FactCheck
  ├──< FactSheet
  ├──< EditorialScore
  └──< ContentDraft
           └──< ContentVariant
                    └──< Publication
                            └──< PublicationAttempt
                                    └──< AnalyticsSnapshot
```

---

# 62. MVP Database Scope

Do not implement every table before the first working pipeline.

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

Historical-specific tables can be added immediately after the core pipeline if historical research is included in MVP.

---

# 63. First Working Flow

The first end-to-end database flow should be:

```text
RSS item
   ↓
sources
   ↓
source_feeds
   ↓
articles
   ↓
article_versions
   ↓
stories
   ↓
story_sources
   ↓
claims
   ↓
evidence_items
   ↓
claim_evidence
   ↓
fact_checks
   ↓
fact_sheets
   ↓
content_drafts
   ↓
content_variants
   ↓
publications
```

Every stage must be independently inspectable.

---

# 64. Example Story Lifecycle

```text
DISCOVERED
    ↓
PROCESSING
    ↓
CLUSTERED
    ↓
CLAIMS_EXTRACTED
    ↓
EVIDENCE_COLLECTED
    ↓
FACT_CHECKED
    ↓
FACT_SHEET_CREATED
    ↓
CONTENT_GENERATED
    ↓
QUALITY_CHECKED
    ↓
IN_REVIEW
    ↓
APPROVED
    ↓
SCHEDULED
    ↓
PUBLISHED
```

Failure at any stage should preserve previous state and create retryable work.

---

# 65. Data Integrity Rules

The application MUST prevent:

* publication without a content variant
* content generation without a story
* fact sheet without a story
* claim evidence without a claim
* evidence without provenance where provenance is expected
* publication without a social account
* publication retry without an attempt record
* human approval without an audit record
* fact-check status without associated evidence assessment
* deletion of evidence required by published content

---

# 66. Review Integrity

Human approval should record:

```text
who
what
when
which version
decision
reason
```

Example:

```json
{
  "actor": "human",
  "action": "PUBLICATION_APPROVED",
  "content_variant_id": "...",
  "version": 3,
  "reason": "Evidence packet reviewed."
}
```

---

# 67. Corrections

If a published story is later found to contain an error:

```text
Original fact sheet
      ↓
Correction research
      ↓
New fact sheet version
      ↓
Correction content
      ↓
Human approval
      ↓
Correction publication
```

Do not overwrite the original evidence trail.

The audit history should show:

```text
original claim
→ original evidence
→ original publication
→ correction evidence
→ corrected claim
→ correction publication
```

---

# 68. Source-of-Truth Rule

When documents disagree:

```text
ARCHITECTURE.md
    ↓
DATA_MODEL.md
    ↓
implementation
```

`ARCHITECTURE.md` defines system-level decisions.

`DATA_MODEL.md` defines persistent data structures.

Implementation MUST conform to both.

If implementation reveals a necessary change:

```text
1. Change canonical documentation.
2. Update migration/model.
3. Update tests.
4. Then implement dependent code.
```

Do not create an undocumented parallel architecture.

---

# 69. Implementation Order

Implement the schema in this order:

```text
01 PostgreSQL setup
02 Extensions
03 Base metadata
04 Sources
05 Source feeds
06 Articles
07 Article versions
08 Stories
09 Story sources
10 Claims
11 Evidence
12 Claim evidence
13 Entities
14 Entity mentions
15 Editorial scores
16 AI models
17 AI runs
18 Fact checks
19 Fact sheets
20 Content drafts
21 Content variants
22 Social accounts
23 Publications
24 Publication attempts
25 Jobs
26 Audit log
27 Indexes
28 Constraints
29 Seed configuration
30 Integration tests
```

---

# 70. Final Canonical Model

The database exists to preserve one chain:

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
HUMAN REVIEW
  ↓
PUBLICATION
  ↓
ANALYTICS
```

AI is an analysis and generation layer around this chain.

It is not the source of truth.

The final architecture therefore remains:

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
  ↓
CONTENT
  ↓
HUMAN APPROVAL
  ↓
PUBLICATION
```

This data model is the canonical persistence layer for that architecture.
