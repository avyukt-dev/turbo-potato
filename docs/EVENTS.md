# News AI Social Media Manager — Event Contracts

## 1. Purpose

This document defines the canonical event architecture for the News AI Social Media Manager.

It specifies:

* Redis Streams
* event names
* payload contracts
* schema versions
* producers
* consumers
* idempotency
* retries
* dead-letter handling
* event ordering
* transactional outbox
* failure recovery
* observability
* event retention

The event system connects the processing pipeline without making Redis the source of truth.

Shared enums and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 2. Core Principle

The architecture is:

```text
PostgreSQL
    ↓
authoritative state

Redis Streams
    ↓
event transport

Workers
    ↓
perform processing

PostgreSQL
    ↓
persist result
```

Redis events MUST NOT become the authoritative representation of business state.

---

# 3. Event Pipeline

```text
NEWS / SOCIAL
      ↓
COLLECTOR
      ↓
article.discovered
      ↓
NORMALIZER
      ↓
article.normalized
      ↓
STORY PROCESSOR
      ↓
story.created
story.clustered
      ↓
CLAIM PROCESSOR
      ↓
claims.extracted
      ↓
EVIDENCE ENGINE
      ↓
evidence.requested
evidence.collected
      ↓
FACT ENGINE
      ↓
story.verified
      ↓
CONTENT ENGINE
      ↓
content.requested
content.generated
      ↓
QUALITY GATE
      ↓
content.quality_checked
      ↓
HUMAN APPROVAL
      ↓
publication.scheduled
      ↓
PUBLISHER
      ↓
publication.executed
      ↓
ANALYTICS
      ↓
analytics.collected
```

For the MVP, every external social publication requires explicit human approval. A future low-risk auto-approval mode may be introduced only under the conditions defined in `CANONICAL_CONTRACTS.md`.

---

# 4. Event Naming Convention

Use:

```text
<aggregate>.<action>
```

Examples:

```text
article.discovered
article.normalized
story.created
claims.extracted
evidence.collected
content.generated
publication.executed
```

Event names should describe what happened, not what a worker should do.

Prefer:

```text
claims.extracted
```

over:

```text
extract.claims
```

---

# 5. Canonical Event List

## Collection

```text
article.discovered
article.normalized
```

## Story intelligence

```text
story.created
story.clustered
```

## Claims

```text
claims.extracted
```

## Evidence

```text
evidence.requested
evidence.collected
```

## Verification

```text
story.verified
fact_check.completed
```

## Content

```text
content.requested
content.generated
content.quality_checked
```

## Publishing

```text
publication.scheduled
publication.executed
publication.failed
```

## Analytics

```text
analytics.requested
analytics.collected
```

## Jobs

```text
job.failed
job.retrying
job.completed
```

---

# 6. Redis Stream Names

Use one stream per logical pipeline domain.

Recommended:

```text
news:articles
news:stories
news:evidence
news:content
news:publishing
news:analytics
news:jobs
```

This keeps high-volume collection events separate from slower publishing operations.

---

# 7. Consumer Groups

Recommended consumer groups:

```text
news:articles
    ├── processor
    └── archive-worker

news:stories
    ├── claim-worker
    ├── editorial-worker
    └── enrichment-worker

news:evidence
    ├── research-worker
    └── factcheck-worker

news:content
    ├── content-worker
    └── quality-worker

news:publishing
    ├── publisher
    └── publication-monitor

news:analytics
    └── analytics-worker

news:jobs
    └── job-monitor
```

A consumer group provides independent progress tracking.

---

# 8. Event Envelope

Every event MUST use a standard envelope.

Example:

```json id="m2w8y8"
{
  "event_id": "uuid",
  "event_type": "article.discovered",
  "schema_version": 1,
  "occurred_at": "2026-09-09T10:00:00Z",
  "producer": "collector",
  "producer_version": "0.1.0",
  "aggregate_type": "article",
  "aggregate_id": "uuid",
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "idempotency_key": "article:source:url",
  "payload": {}
}
```

---

# 9. Event Envelope Fields

## `event_id`

Globally unique event UUID.

Used for:

* tracing
* deduplication
* debugging

## `event_type`

Canonical event name.

Example:

```text
article.discovered
```

## `schema_version`

Integer version.

Never silently change the meaning of an existing version. Breaking semantic changes require a new version.

## `occurred_at`

UTC timestamp representing when the event occurred.

## `producer`

Logical service that generated the event.

Examples:

```text
collector
processor
ai-worker
publisher
scheduler
analytics-worker
```

## `producer_version`

Application version.

## `aggregate_type`

Primary entity affected.

Examples:

```text
article
story
claim
evidence
content
publication
job
```

## `aggregate_id`

UUID of the affected database record.

## `correlation_id`

Groups all events belonging to one processing flow.

## `causation_id`

References the event that caused the current event.

## `idempotency_key`

Stable key allowing a consumer to recognize duplicate delivery.

## `payload`

Event-specific data. Keep payloads small. PostgreSQL remains authoritative.

---

# 10. Article Discovered

Event: `article.discovered`

Produced by: `collector`

Stream: `news:articles`

```json
{
  "article_id": "uuid",
  "source_id": "uuid",
  "source_feed_id": "uuid",
  "canonical_url": "https://example.com/article",
  "title": "Example headline",
  "published_at": "2026-09-09T09:30:00Z"
}
```

Consumer: `processor`

---

# 11. Article Normalized

Event: `article.normalized`

Produced by: `processor`

```json
{
  "article_id": "uuid",
  "article_version_id": "uuid",
  "content_hash": "sha256",
  "language": "en",
  "title": "Normalized headline"
}
```

Consumer: `story-processor`

---

# 12. Story Created

Event: `story.created`

Produced by: `story-processor`

```json
{
  "story_id": "uuid",
  "article_id": "uuid",
  "cluster_key": "..."
}
```

Consumers:

```text
claim-worker
editorial-worker
```

---

# 13. Story Clustered

Event: `story.clustered`

```json
{
  "story_id": "uuid",
  "article_ids": ["uuid", "uuid"],
  "similarity_score": 0.91,
  "cluster_method": "semantic+entity+time"
}
```

The event indicates that related source material has been associated with the story.

---

# 14. Claims Extracted

Event: `claims.extracted`

Produced by: `ai-worker`

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid", "uuid"],
  "ai_run_id": "uuid",
  "model_id": "uuid"
}
```

Claims remain in PostgreSQL. Do not place entire claim objects in the event unless required.

---

# 15. Evidence Requested

Event: `evidence.requested`

Produced by: `fact-check-worker`

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid"],
  "research_scope": {
    "primary_sources": true,
    "government_sources": true,
    "court_sources": true,
    "academic_sources": true,
    "international_sources": true
  }
}
```

Consumer: `research-worker`

---

# 16. Evidence Collected

Event: `evidence.collected`

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid"],
  "evidence_ids": ["uuid", "uuid"],
  "research_run_id": "uuid"
}
```

Consumer: `factcheck-worker`

---

# 17. Story Verified

Event: `story.verified`

```json
{
  "story_id": "uuid",
  "fact_check_ids": ["uuid"],
  "confidence_score": 0.84,
  "risk_level": "MEDIUM",
  "review_required": true
}
```

This event means the configured verification stage completed. It does **not** mean every statement is true. Individual claims retain their own `ClaimVerificationStatus` as defined in `CANONICAL_CONTRACTS.md`.

---

# 18. Fact Check Completed

Event: `fact_check.completed`

The verdict uses the `FactCheckLabel` namespace, not `ClaimVerificationStatus`.

```json
{
  "story_id": "uuid",
  "fact_check_id": "uuid",
  "label": "PARTIALLY_TRUE",
  "confidence_score": 0.78,
  "review_required": true
}
```

Canonical labels are:

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

---

# 19. Content Requested

Event: `content.requested`

Produced after a story has an adequate Fact Sheet.

```json
{
  "story_id": "uuid",
  "fact_sheet_id": "uuid",
  "requested_platforms": ["INSTAGRAM", "X"],
  "requested_formats": ["CAROUSEL", "POST"]
}
```

---

# 20. Content Generated

Event: `content.generated`

```json
{
  "story_id": "uuid",
  "content_draft_id": "uuid",
  "content_variant_ids": ["uuid", "uuid"],
  "ai_run_id": "uuid"
}
```

---

# 21. Content Quality Checked

Event: `content.quality_checked`

```json
{
  "content_draft_id": "uuid",
  "passed": true,
  "fact_check_passed": true,
  "source_check_passed": true,
  "style_check_passed": true,
  "risk_level": "LOW",
  "review_required": true
}
```

For the MVP, `review_required` remains `true` for every externally publishable content item because all external publication requires explicit human approval.

A future explicitly enabled low-risk auto-approval policy may permit `review_required = false` only under the conditions in `CANONICAL_CONTRACTS.md`. Sensitive or mandatory-review topics may never use that low-risk bypass.

---

# 22. Publication Scheduled

Event: `publication.scheduled`

```json
{
  "publication_id": "uuid",
  "content_variant_id": "uuid",
  "social_account_id": "uuid",
  "scheduled_at": "2026-09-09T15:00:00Z"
}
```

Consumer: `publisher`

---

# 23. Publication Executed

Event: `publication.executed`

```json
{
  "publication_id": "uuid",
  "attempt_id": "uuid",
  "platform": "INSTAGRAM",
  "external_post_id": "...",
  "external_url": "...",
  "published_at": "2026-09-09T15:01:02Z"
}
```

---

# 24. Publication Failed

Event: `publication.failed`

```json
{
  "publication_id": "uuid",
  "attempt_id": "uuid",
  "platform": "INSTAGRAM",
  "error_code": "RATE_LIMIT",
  "retryable": true
}
```

The publisher determines whether retry is safe.

---

# 25. Analytics Requested

Event: `analytics.requested`

```json
{
  "publication_id": "uuid",
  "platform": "INSTAGRAM"
}
```

---

# 26. Analytics Collected

Event: `analytics.collected`

```json
{
  "publication_id": "uuid",
  "analytics_snapshot_id": "uuid",
  "captured_at": "2026-09-09T18:00:00Z"
}
```

---

# 27. Job Events

Generic job lifecycle:

```text
job.failed
job.retrying
job.completed
```

Example:

```json
{
  "job_id": "uuid",
  "job_type": "EVIDENCE_RESEARCH",
  "attempt_number": 2,
  "retryable": true,
  "error_code": "SEARCH_TIMEOUT"
}
```

---

# 28. Event Processing Contract

Every consumer follows:

```text
READ
 ↓
VALIDATE
 ↓
CHECK IDEMPOTENCY
 ↓
LOAD DATABASE STATE
 ↓
PROCESS
 ↓
WRITE DATABASE STATE
 ↓
EMIT NEXT EVENT
 ↓
ACK
```

Never ACK before durable processing completes.

---

# 29. Idempotency

Consumers MUST assume duplicate delivery.

A consumer loads current PostgreSQL state and safely no-ops when the work has already completed.

---

# 30. Idempotency Table

For high-value operations, maintain explicit processing records where useful:

```text
processed_events
---------------
event_id
consumer_group
processed_at
result
```

Unique constraint:

```text
(event_id, consumer_group)
```

---

# 31. External Side-Effect Idempotency

For publication, `publication_id` is the canonical internal operation.

Before sending:

```text
1. Check publication status.
2. Check existing publication attempts.
3. Check provider state where possible.
4. Send only if safe.
5. Store provider ID.
```

A retry MUST NOT blindly create another post.

---

# 32. Redis Consumer Acknowledgement

Use `XREADGROUP` with explicit `XACK` only after durable processing succeeds.

---

# 33. Pending Messages

If a worker crashes while processing, the message remains pending. Recovery workers inspect stale pending messages, claim them when safe, and retry idempotently.

---

# 34. Dead-Letter Queue

Repeated failures should eventually enter:

```text
news:dead-letter
```

Dead-letter events MUST remain inspectable and retain the original event reference, consumer, attempt count, final error, timestamp, and original payload/reference.

---

# 35. Retry Policy

Default exponential backoff:

```text
attempt 1 → 30 seconds
attempt 2 → 2 minutes
attempt 3 → 10 minutes
attempt 4 → 30 minutes
attempt 5 → 2 hours
```

Add jitter. Do not retry forever.

---

# 36. Retry Classification

Retryable examples:

```text
NETWORK_TIMEOUT
TEMPORARY_PROVIDER_ERROR
RATE_LIMIT
DATABASE_CONNECTION_ERROR
REDIS_CONNECTION_ERROR
TEMPORARY_SEARCH_FAILURE
```

Non-retryable examples:

```text
INVALID_PAYLOAD
SCHEMA_ERROR
MISSING_REQUIRED_RECORD
AUTHENTICATION_FAILURE
PERMISSION_DENIED
INVALID_SOCIAL_ACCOUNT
POLICY_BLOCK
```

---

# 37. Rate Limiting

Workers must respect provider limits. Rate limiting is separate from event semantics. Work should remain queued rather than being discarded.

---

# 38. Ordering

Do not assume global ordering across Redis Streams. Consumers must always verify current PostgreSQL state before acting.

---

# 39. Event Versioning

Backward-compatible additions may remain on the same schema version. Breaking changes to field names, field types, or semantics require a new version.

---

# 40. Event Size

Events should generally contain IDs, small metadata, and state references. Store large article bodies, media, AI transcripts, and research results in PostgreSQL or media/object storage and pass references.

---

# 41. Correlation Tracing

One story should be traceable end-to-end through `correlation_id`, `causation_id`, and `event_id`.

---

# 42. Causation Graph

```text
article.discovered
        ↓
article.normalized
        ↓
story.created
        ├──────────────┐
        ↓              ↓
claims.extracted   editorial.score
        ↓
evidence.requested
        ↓
evidence.collected
        ↓
fact_check.completed
        ↓
content.requested
        ↓
content.generated
        ↓
content.quality_checked
        ↓
human approval (database state)
        ↓
publication.scheduled
        ↓
publication.executed
```

---

# 43. Transactional Outbox

Important database state transitions should write the business update and outbox event in the same PostgreSQL transaction. The outbox worker then publishes to Redis Streams.

---

# 44. Outbox Schema

Recommended:

```text
event_outbox
------------
id                    UUID PK
event_id              UUID UNIQUE
event_type            TEXT
schema_version        INTEGER
aggregate_type        TEXT
aggregate_id          UUID
correlation_id        UUID
causation_id          UUID
payload               JSONB
status                TEXT
attempt_count         INTEGER
next_attempt_at       TIMESTAMPTZ
published_at          TIMESTAMPTZ
last_error             TEXT
created_at             TIMESTAMPTZ
updated_at             TIMESTAMPTZ
```

Statuses:

```text
PENDING
PUBLISHING
PUBLISHED
FAILED
```

---

# 45. Outbox Publishing

If the outbox worker crashes after Redis publish but before marking PostgreSQL, an event may be delivered twice. Consumers MUST therefore remain idempotent. Exactly-once processing is not assumed.

---

# 46. Event Retention

Redis provides an operational event window. Permanent provenance lives in PostgreSQL through the appropriate business, outbox, audit, AI-run, and publication-attempt records.

---

# 47. Event Observability

Workers should expose counts and latency for received, processed, failed, retried, and dead-lettered events by event type and consumer.

---

# 48. Queue Health

Monitor stream, consumer group, pending count, oldest pending age, processing rate, failure rate, retry count, and dead-letter count.

---

# 49. Worker Health

Expose worker status, last successful event, last failure, current job, queue depth, processing latency, and resource use.

---

# 50. Failure Isolation

A failure in one domain must not stop unrelated domains. For example, an Instagram API outage must not stop collection, research, fact checking, or content generation.

---

# 51. Cloud AI Failure

Cloud-provider fallback is controlled by the AI router. Event contracts remain provider-independent.

---

# 52. Local AI Failure

If local inference is unavailable, the router may retry or use an allowed fallback provider without losing the job.

---

# 53. Sensitive Story Failure

If a sensitive story cannot be verified adequately, it must not become publishable simply because research retries are exhausted. Preserve insufficient-evidence state and route to human review.

---

# 54. Human Review Boundary

Human review is durable database state, not a Redis consumer.

For the MVP:

```text
quality check
    ↓
database:
review_state = READY_FOR_REVIEW
    ↓
dashboard
    ↓
human decision
    ↓
database:
review_state = APPROVED
    ↓
publication.scheduled
```

Every external publication requires this explicit human approval in the MVP. Sensitive and mandatory-review categories always require human review, including in any future low-risk automation mode.

Audit the human decision and the exact artifact/version approved.

---

# 55. Publication Safety

Publisher must re-check current PostgreSQL state immediately before an external side effect.

MVP minimum:

```text
verification stage = complete
fact_sheet = valid
content = QUALITY_CHECKED
review = APPROVED
publication = SCHEDULED
```

Never trust an old queue message alone.

---

# 56. Stale Event Handling

If an event arrives after state has changed, the consumer must load current state, reject invalid transitions, record the reason, and ACK safely without executing stale work.

---

# 57. Event State Machine

The event pipeline should respect:

```text
DISCOVERED
    ↓
NORMALIZED
    ↓
CLUSTERED
    ↓
CLAIMS_EXTRACTED
    ↓
EVIDENCE_COLLECTED
    ↓
VERIFICATION_COMPLETED
    ↓
CONTENT_GENERATED
    ↓
QUALITY_CHECKED
    ↓
APPROVED
    ↓
SCHEDULED
    ↓
PUBLISHED
```

`VERIFICATION_COMPLETED` is a pipeline stage and must not be interpreted as “all claims true.”

---

# 58. Event Contract Testing

Every event schema should have tests for valid payload, missing required fields, invalid UUIDs/enums, unsupported schema versions, duplicate delivery, and malformed payloads.

---

# 59. Integration Testing

Integration tests should cover the end-to-end event sequence using PostgreSQL, Redis, workers, and mocked external publishers. Automated tests must not create real social posts.

---

# 60. Replay

Events should be replayable where practical, but replay MUST NOT automatically trigger dangerous external side effects.

---

# 61. Event Replay Modes

Recommended modes:

```text
DRY_RUN
REBUILD
BACKFILL
LIVE
```

Publishing workers require explicit `LIVE` mode.

---

# 62. Event Security

Never place API keys, access tokens, refresh tokens, passwords, session cookies, or authorization headers in event payloads. Credential references are allowed.

---

# 63. Payload Privacy

Minimize personally identifying information in events. Prefer IDs and database references over copying unnecessary personal data.

---

# 64. Event Metadata

Operational metadata such as environment, trace ID, request ID, and worker ID should remain separate from business payload semantics.

---

# 65. Service Responsibilities

## Collector
Produces `article.discovered`.

## Processor
Consumes `article.discovered`; produces `article.normalized`, `story.created`, and `story.clustered`.

## AI Worker
Produces AI-derived events such as `claims.extracted` and `content.generated` according to task routing.

## Research Worker
Consumes `evidence.requested`; produces `evidence.collected`.

## Fact Check Worker
Consumes `evidence.collected`; produces `fact_check.completed` and `story.verified`.

## Content Worker
Consumes `content.requested`; produces `content.generated`.

## Quality Worker
Consumes `content.generated`; produces `content.quality_checked`.

## Scheduler
Produces `publication.scheduled` and `analytics.requested` for eligible durable state.

## Publisher
Consumes `publication.scheduled`; produces `publication.executed` or `publication.failed`.

## Analytics Worker
Consumes `analytics.requested`; produces `analytics.collected`.

---

# 66. Recommended Event Package

```text
packages/events/
├── __init__.py
├── envelope.py
├── schemas/
│   ├── article.py
│   ├── story.py
│   ├── claims.py
│   ├── evidence.py
│   ├── fact_check.py
│   ├── content.py
│   ├── publication.py
│   ├── analytics.py
│   └── jobs.py
├── producer.py
├── consumer.py
├── idempotency.py
├── retry.py
├── outbox.py
└── registry.py
```

Use Pydantic models for event validation.

---

# 67. Event Registry

```python
EVENT_REGISTRY = {
    "article.discovered": ArticleDiscoveredV1,
    "article.normalized": ArticleNormalizedV1,
    "story.created": StoryCreatedV1,
    "story.clustered": StoryClusteredV1,
    "claims.extracted": ClaimsExtractedV1,
    "evidence.requested": EvidenceRequestedV1,
    "evidence.collected": EvidenceCollectedV1,
    "fact_check.completed": FactCheckCompletedV1,
    "story.verified": StoryVerifiedV1,
    "content.requested": ContentRequestedV1,
    "content.generated": ContentGeneratedV1,
    "content.quality_checked": ContentQualityCheckedV1,
    "publication.scheduled": PublicationScheduledV1,
    "publication.executed": PublicationExecutedV1,
    "publication.failed": PublicationFailedV1,
    "analytics.requested": AnalyticsRequestedV1,
    "analytics.collected": AnalyticsCollectedV1
}
```

---

# 68. Example Event Model

```python
class EventEnvelope(BaseModel):
    event_id: UUID
    event_type: str
    schema_version: int
    occurred_at: datetime
    producer: str
    producer_version: str
    aggregate_type: str
    aggregate_id: UUID
    correlation_id: UUID
    causation_id: UUID | None
    idempotency_key: str
    payload: dict
```

Specific events should use typed payload models.

---

# 69. Producer API

Application code should publish through the event abstraction rather than scattering raw Redis commands through business logic.

---

# 70. Consumer API

Workers should use a common consumer abstraction that handles ACK, retry, and dead-letter mechanics while business services own processing decisions.

---

# 71. Database Transaction + Event

Preferred:

```text
Service
  ↓
BEGIN
  ↓
update database
  ↓
insert outbox event
  ↓
COMMIT
  ↓
outbox publisher
  ↓
Redis
```

---

# 72. Event-Driven vs Direct Calls

Use events for long-running/background work and direct service calls for small deterministic transformations, validation, calculations, and repository operations. Do not turn every function call into an event.

---

# 73. Queue Backpressure

If consumers lag, expose queue depth, limit concurrency, prioritize important stories, apply provider rate limits, and preserve events. Priority belongs in durable job/database state rather than relying solely on Redis ordering.

---

# 74. Priority Classes

Recommended:

```text
P0 = emergency / critical operational
P1 = breaking major story
P2 = high editorial importance
P3 = normal
P4 = background enrichment
```

---

# 75. Breaking News

Breaking news receives higher processing priority, not lower factual standards.

---

# 76. Historical Research

Historical event families may be introduced when that subsystem is implemented, for example:

```text
historical.research.requested
historical.sources.collected
historical.analysis.completed
historical.fact_sheet.created
```

They must follow the same versioning, idempotency, provenance, and evidence-boundary rules.

---

# 77. Event Governance

Adding an event requires a name, schema version, producer, consumers, payload schema, idempotency key, retry/failure behavior, security review, and tests.

---

# 78. Canonical Rule

```text
EVENT ≠ STATE

EVENT → notification that state changed or work should be processed
DATABASE → authoritative state
```

Workers must always validate current PostgreSQL state before important actions.

---

# 79. Final Architecture

```text
                    ┌─────────────────┐
                    │   PostgreSQL    │
                    │ Source of Truth │
                    └────────┬────────┘
                             │
                       Transaction
                             │
                             ▼
                    ┌─────────────────┐
                    │  Event Outbox   │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Redis Streams   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        │                    │                     │
        ▼                    ▼                     ▼
   Processor            AI Worker            Research Worker
        │                    │                     │
        └────────────────────┼─────────────────────┘
                             │
                             ▼
                       PostgreSQL
                             │
                             ▼
                       Quality Gate
                             │
                             ▼
                       Human Approval
                             │
                             ▼
                         Scheduler
                             │
                             ▼
                         Publisher
                             │
                             ▼
                      Social Platforms
                             │
                             ▼
                         Analytics
```

The reliability rule remains:

```text
DATABASE TRANSACTION
        +
OUTBOX EVENT
        +
IDEMPOTENT CONSUMER
        +
RETRY
        +
DEAD LETTER
        =
RELIABLE PIPELINE
```

This document uses the shared semantics defined in `CANONICAL_CONTRACTS.md`.