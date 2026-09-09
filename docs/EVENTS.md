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
HUMAN REVIEW
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

---

## `event_type`

Canonical event name.

Example:

```text
article.discovered
```

---

## `schema_version`

Integer version.

Example:

```text
1
```

Never silently change the meaning of an existing version.

Breaking changes require a new version.

---

## `occurred_at`

UTC timestamp representing when the event occurred.

---

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

---

## `producer_version`

Application version.

Useful when debugging deployment-specific failures.

---

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

---

## `aggregate_id`

UUID of the affected database record.

---

## `correlation_id`

Groups all events belonging to one processing flow.

Example:

```text
RSS article
→ story
→ claims
→ evidence
→ content
→ publication
```

All events can share one correlation ID.

---

## `causation_id`

References the event that caused the current event.

Example:

```text
article.normalized
```

may have:

```text
causation_id = article.discovered event_id
```

This creates an event lineage.

---

## `idempotency_key`

Stable key allowing a consumer to recognize duplicate delivery.

---

## `payload`

Event-specific data.

Keep payloads small.

The database remains authoritative.

---

# 10. Article Discovered

Event:

```text
article.discovered
```

Produced by:

```text
collector
```

Stream:

```text
news:articles
```

Payload:

```json id="r8u4pq"
{
  "article_id": "uuid",
  "source_id": "uuid",
  "source_feed_id": "uuid",
  "canonical_url": "https://example.com/article",
  "title": "Example headline",
  "published_at": "2026-09-09T09:30:00Z"
}
```

Consumer:

```text
processor
```

Action:

```text
fetch/normalize article
```

---

# 11. Article Normalized

Event:

```text
article.normalized
```

Produced by:

```text
processor
```

Payload:

```json id="5h2tqk"
{
  "article_id": "uuid",
  "article_version_id": "uuid",
  "content_hash": "sha256",
  "language": "en",
  "title": "Normalized headline"
}
```

Consumer:

```text
story-processor
```

---

# 12. Story Created

Event:

```text
story.created
```

Produced by:

```text
story-processor
```

Payload:

```json id="8n9w7s"
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

Event:

```text
story.clustered
```

Payload:

```json id="w6r1bk"
{
  "story_id": "uuid",
  "article_ids": [
    "uuid",
    "uuid"
  ],
  "similarity_score": 0.91,
  "cluster_method": "semantic+entity+time"
}
```

The event indicates that related source material has been associated with the story.

---

# 14. Claims Extracted

Event:

```text
claims.extracted
```

Produced by:

```text
ai-worker
```

Payload:

```json id="x6j3y4"
{
  "story_id": "uuid",
  "claim_ids": [
    "uuid",
    "uuid"
  ],
  "ai_run_id": "uuid",
  "model_id": "uuid"
}
```

Claims themselves remain in PostgreSQL.

Do not place entire claim objects in the event unless required.

---

# 15. Evidence Requested

Event:

```text
evidence.requested
```

Produced by:

```text
fact-check-worker
```

Payload:

```json id="2e9gfr"
{
  "story_id": "uuid",
  "claim_ids": [
    "uuid"
  ],
  "research_scope": {
    "primary_sources": true,
    "government_sources": true,
    "court_sources": true,
    "academic_sources": true,
    "international_sources": true
  }
}
```

Consumer:

```text
research-worker
```

---

# 16. Evidence Collected

Event:

```text
evidence.collected
```

Payload:

```json id="m83gxy"
{
  "story_id": "uuid",
  "claim_ids": [
    "uuid"
  ],
  "evidence_ids": [
    "uuid",
    "uuid"
  ],
  "research_run_id": "uuid"
}
```

Consumer:

```text
factcheck-worker
```

---

# 17. Story Verified

Event:

```text
story.verified
```

Payload:

```json id="5cnw9h"
{
  "story_id": "uuid",
  "fact_check_ids": [
    "uuid"
  ],
  "confidence_score": 0.84,
  "risk_level": "MEDIUM",
  "review_required": true
}
```

This event does not necessarily mean:

```text
"every statement is true"
```

It means the verification stage completed.

Individual claims retain their own status.

---

# 18. Fact Check Completed

Event:

```text
fact_check.completed
```

Payload:

```json id="z9m4g6"
{
  "story_id": "uuid",
  "fact_check_id": "uuid",
  "status": "PARTIALLY_SUPPORTED",
  "confidence_score": 0.78,
  "review_required": true
}
```

---

# 19. Content Requested

Event:

```text
content.requested
```

Produced after a story has an adequate fact sheet.

Payload:

```json id="y7w5p4"
{
  "story_id": "uuid",
  "fact_sheet_id": "uuid",
  "requested_platforms": [
    "INSTAGRAM",
    "X"
  ],
  "requested_formats": [
    "CAROUSEL",
    "POST"
  ]
}
```

---

# 20. Content Generated

Event:

```text
content.generated
```

Payload:

```json id="g5s4w1"
{
  "story_id": "uuid",
  "content_draft_id": "uuid",
  "content_variant_ids": [
    "uuid",
    "uuid"
  ],
  "ai_run_id": "uuid"
}
```

---

# 21. Content Quality Checked

Event:

```text
content.quality_checked
```

Payload:

```json id="j8f0tq"
{
  "content_draft_id": "uuid",
  "passed": true,
  "fact_check_passed": true,
  "source_check_passed": true,
  "style_check_passed": true,
  "risk_level": "LOW",
  "review_required": false
}
```

For sensitive topics:

```text
review_required = true
```

may remain mandatory even when automated checks pass.

---

# 22. Publication Scheduled

Event:

```text
publication.scheduled
```

Payload:

```json id="a4e6zp"
{
  "publication_id": "uuid",
  "content_variant_id": "uuid",
  "social_account_id": "uuid",
  "scheduled_at": "2026-09-09T15:00:00Z"
}
```

Consumer:

```text
publisher
```

---

# 23. Publication Executed

Event:

```text
publication.executed
```

Payload:

```json id="2j4b8k"
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

Event:

```text
publication.failed
```

Payload:

```json id="v4u0k8"
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

Event:

```text
analytics.requested
```

Payload:

```json id="c2x8sj"
{
  "publication_id": "uuid",
  "platform": "INSTAGRAM"
}
```

---

# 26. Analytics Collected

Event:

```text
analytics.collected
```

Payload:

```json id="x5k3g1"
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

```json id="y8f6p0"
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

Never:

```text
READ
 ↓
ACK
 ↓
PROCESS
```

because a worker crash after ACK can lose the event.

---

# 29. Idempotency

Consumers MUST assume duplicate delivery.

Example:

```text
article.discovered
```

may arrive twice.

Processor:

```text
1. Read article_id.
2. Check current database state.
3. Determine whether normalization already completed.
4. If completed, safely ACK.
5. Otherwise process.
```

---

# 30. Idempotency Table

For high-value operations, maintain explicit processing records.

Recommended future table:

```text id="bq4k7m"
processed_events
---------------
event_id
consumer_group
processed_at
result
```

Unique constraint:

```text id="y7f3t1"
(event_id, consumer_group)
```

This prevents the same event from being processed twice by the same logical consumer.

---

# 31. External Side-Effect Idempotency

External APIs require additional protection.

For publication:

```text
publication_id
```

is the canonical internal operation.

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

Use:

```text
XREADGROUP
```

with explicit:

```text
XACK
```

Only acknowledge after durable processing succeeds.

---

# 33. Pending Messages

If a worker crashes while processing:

```text
Redis
→ pending message
```

A recovery worker should periodically inspect pending messages.

Recovery flow:

```text
pending message
      ↓
check consumer ownership
      ↓
check idle time
      ↓
claim message
      ↓
retry
```

---

# 34. Dead-Letter Queue

Messages that repeatedly fail should eventually enter a dead-letter stream.

Recommended:

```text
news:dead-letter
```

Dead-letter payload:

```json id="c0l2s6"
{
  "original_event_id": "uuid",
  "original_event_type": "evidence.requested",
  "consumer": "research-worker",
  "attempts": 8,
  "last_error": "SEARCH_TIMEOUT",
  "failed_at": "2026-09-09T12:00:00Z",
  "original_payload": {}
}
```

Dead-letter events MUST remain inspectable.

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

Add jitter.

Example:

```text
delay = base_delay × random_factor
```

Do not retry forever.

---

# 36. Retry Classification

Errors should be classified.

## Retryable

```text
NETWORK_TIMEOUT
TEMPORARY_PROVIDER_ERROR
RATE_LIMIT
DATABASE_CONNECTION_ERROR
REDIS_CONNECTION_ERROR
TEMPORARY_SEARCH_FAILURE
```

## Non-retryable

```text
INVALID_PAYLOAD
SCHEMA_ERROR
MISSING_REQUIRED_RECORD
AUTHENTICATION_FAILURE
PERMISSION_DENIED
INVALID_SOCIAL_ACCOUNT
POLICY_BLOCK
```

Some provider errors may change classification depending on context.

---

# 37. Rate Limiting

Workers must respect external provider limits.

Implement rate limiting separately from event semantics.

Possible mechanisms:

```text
Redis token bucket
Redis counter + TTL
provider-aware scheduler
```

The event should remain queued rather than being discarded.

---

# 38. Ordering

Do not assume global ordering across Redis Streams.

Ordering should be meaningful only where required.

For story-specific processing:

```text
aggregate_id = story_id
```

can be used for logical sequencing.

Example:

```text
story.created
→ story.clustered
→ claims.extracted
```

The consumer should still verify database state before acting.

---

# 39. Event Versioning

Schema changes:

### Backward-compatible

Examples:

```text
add optional field
add metadata
```

May remain:

```text
schema_version = 1
```

### Breaking

Examples:

```text
rename field
change field type
change semantics
```

Require:

```text
schema_version = 2
```

Consumers should support a transition period where necessary.

---

# 40. Event Size

Events should generally contain:

```text
IDs
small metadata
state references
```

Avoid putting:

```text
full article bodies
large HTML
large images
full AI transcripts
large search results
```

into Redis events.

Store large data in:

```text
PostgreSQL
object storage
```

and pass references.

---

# 41. Correlation Tracing

One story should be traceable end-to-end.

Example:

```text
correlation_id = STORY-UUID
```

Then:

```text
article.discovered
article.normalized
story.created
claims.extracted
evidence.requested
evidence.collected
fact_check.completed
content.generated
content.quality_checked
publication.scheduled
publication.executed
```

can all be associated with the same processing flow.

---

# 42. Causation Graph

Example:

```text
article.discovered
        │
        ▼
article.normalized
        │
        ▼
story.created
        │
        ├──────────────┐
        ▼              ▼
claims.extracted   editorial.score
        │
        ▼
evidence.requested
        │
        ▼
evidence.collected
        │
        ▼
fact_check.completed
        │
        ▼
content.requested
        │
        ▼
content.generated
        │
        ▼
content.quality_checked
        │
        ▼
publication.scheduled
        │
        ▼
publication.executed
```

This should be reconstructable using:

```text
correlation_id
causation_id
event_id
```

---

# 43. Transactional Outbox

Directly publishing an event after a database update creates a failure window:

```text
DB UPDATE
   ↓
process crashes
   ↓
event never published
```

Use an outbox.

Transaction:

```text
BEGIN

UPDATE stories
SET status = 'VERIFIED'

INSERT INTO event_outbox (...)

COMMIT
```

Then:

```text
Outbox Worker
    ↓
Redis Streams
```

---

# 44. Outbox Schema

Recommended:

```text id="d8c7e5"
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

Flow:

```text
PostgreSQL
    ↓
SELECT pending outbox rows
    ↓
claim rows
    ↓
publish Redis event
    ↓
mark PUBLISHED
```

If the worker crashes after Redis publish but before marking PostgreSQL:

```text
event may be published twice
```

Therefore consumers MUST remain idempotent.

Exactly-once processing should not be assumed.

---

# 46. Event Retention

Redis streams are transport infrastructure.

Do not rely on indefinite retention.

Recommended approach:

```text
Redis
    → operational event window

PostgreSQL
    → permanent provenance where required
```

For important events, preserve the event record in:

```text
event_outbox
audit_log
ai_runs
publication_attempts
```

as appropriate.

---

# 47. Event Observability

Every worker should expose:

```text
events_received_total
events_processed_total
events_failed_total
events_retried_total
events_dead_lettered_total
event_processing_latency_ms
```

Track by:

```text
event_type
consumer
status
```

---

# 48. Queue Health

The monitoring system should expose:

```text
stream
consumer group
pending count
oldest pending age
processing rate
failure rate
retry count
dead-letter count
```

Example:

```text
EVENT QUEUES

news:articles
  pending: 12
  oldest: 8s

news:evidence
  pending: 4
  oldest: 31s

news:content
  pending: 2
  oldest: 12s

news:publishing
  pending: 0
```

---

# 49. Worker Health

Every worker should expose:

```text
worker status
last successful event
last failure
current job
queue depth
processing latency
memory usage
```

The existing server health architecture should eventually incorporate:

```text
SERVICES
├── Collector
├── Processor
├── AI Worker
├── Publisher
└── Scheduler

QUEUES
├── articles
├── stories
├── evidence
├── content
├── publishing
└── analytics
```

---

# 50. Failure Isolation

A failure in one domain must not stop the entire pipeline.

Example:

```text
Instagram API down
```

must NOT prevent:

```text
news collection
fact checking
story research
content generation
```

Only publishing should accumulate retryable work.

---

# 51. Cloud AI Failure

If a cloud AI provider fails:

```text
content generation
      ↓
provider failure
      ↓
retry
      ↓
alternate provider
```

Provider fallback should be controlled by the AI router.

The event contract remains:

```text
content.requested
```

The internal provider choice does not need a separate event.

---

# 52. Local AI Failure

If POCO local inference becomes unavailable:

```text
local AI unavailable
       ↓
health check
       ↓
router fallback
       ↓
cloud provider
```

Do not lose the job.

---

# 53. Sensitive Story Failure

If a sensitive story cannot be verified adequately:

```text
evidence.requested
        ↓
research failure
        ↓
retry
        ↓
insufficient evidence
        ↓
human review
```

Never automatically publish simply because the retry budget has been exhausted.

---

# 54. Human Review Boundary

Human review is not a Redis event consumer.

The database is the authoritative review state.

Workflow:

```text
quality check
    ↓
database:
review_required = true
    ↓
dashboard
    ↓
human decision
    ↓
database update
    ↓
publication.scheduled
```

Audit the human decision.

---

# 55. Publication Safety

Publishing must require a valid database state.

Minimum:

```text
story = VERIFIED
fact_sheet = valid
content = QUALITY_CHECKED
review = APPROVED where required
publication = SCHEDULED
```

Publisher must re-check state immediately before external publication.

Never trust an old queue message alone.

---

# 56. Stale Event Handling

Events can arrive after state has changed.

Example:

```text
content.requested
```

arrives after:

```text
story = REJECTED
```

Consumer behavior:

```text
load current state
→ detect invalid transition
→ do not process
→ ACK safely
→ record reason
```

Do not blindly execute historical events.

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
VERIFIED
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

Allowed transitions should be enforced by application services.

---

# 58. Event Contract Testing

Every event schema should have tests for:

```text
valid payload
missing required field
invalid UUID
invalid enum
unsupported schema version
duplicate event
malformed payload
```

Consumer tests should verify:

```text
given event
→ expected database state
→ expected next event
```

---

# 59. Integration Testing

The integration test environment should include:

```text
PostgreSQL
Redis
Collector
Processor
AI Worker
Publisher mock
```

Test:

```text
article.discovered
→ article normalized
→ story created
→ claims extracted
→ evidence collected
→ fact check completed
→ content generated
→ quality check
→ publication scheduled
```

No real social post should be created during automated integration tests.

---

# 60. Replay

Events should be replayable where practical.

Example:

```text
historical event
→ new consumer version
→ rebuild derived state
```

Replay MUST NOT trigger dangerous external side effects automatically.

Especially:

```text
publication.executed
```

should not be replayed into a live publisher.

---

# 61. Event Replay Modes

Recommended:

```text
DRY_RUN
REBUILD
BACKFILL
LIVE
```

Default:

```text
REBUILD
```

for historical processing.

Publishing workers should require explicit:

```text
LIVE
```

mode.

---

# 62. Event Security

Do not place secrets in event payloads.

Never include:

```text
API keys
access tokens
refresh tokens
passwords
session cookies
authorization headers
```

Events may contain:

```text
credential_reference
```

but not the credential itself.

---

# 63. Payload Privacy

Minimize personally identifying information.

A claim event should normally contain:

```text
claim_id
story_id
```

rather than copying unnecessary personal data.

---

# 64. Event Metadata

Useful optional metadata:

```json id="g8o2cc"
{
  "environment": "production",
  "region": "india",
  "trace_id": "...",
  "request_id": "...",
  "worker_id": "ai-worker-01"
}
```

Do not make operational metadata part of the business payload.

---

# 65. Service Responsibilities

## Collector

Produces:

```text
article.discovered
```

## Processor

Consumes:

```text
article.discovered
```

Produces:

```text
article.normalized
story.created
story.clustered
```

## AI Worker

Consumes:

```text
claims.extracted requests
content.requested
```

Produces:

```text
claims.extracted
content.generated
```

## Research Worker

Consumes:

```text
evidence.requested
```

Produces:

```text
evidence.collected
```

## Fact Check Worker

Consumes:

```text
evidence.collected
```

Produces:

```text
fact_check.completed
story.verified
```

## Content Worker

Consumes:

```text
content.requested
```

Produces:

```text
content.generated
```

## Quality Worker

Consumes:

```text
content.generated
```

Produces:

```text
content.quality_checked
```

## Scheduler

Produces:

```text
publication.scheduled
analytics.requested
```

## Publisher

Consumes:

```text
publication.scheduled
```

Produces:

```text
publication.executed
publication.failed
```

## Analytics Worker

Consumes:

```text
analytics.requested
```

Produces:

```text
analytics.collected
```

---

# 66. Recommended Event Package

Repository structure:

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

Central registry:

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

Conceptually:

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

Application code should use:

```python
await event_bus.publish(
    event_type="claims.extracted",
    aggregate_id=story.id,
    payload={
        "story_id": str(story.id),
        "claim_ids": claim_ids,
        "ai_run_id": str(ai_run.id),
    },
)
```

It should NOT directly construct Redis commands throughout the codebase.

---

# 70. Consumer API

Worker code should conceptually use:

```python
async for event in consumer:
    try:
        await processor.handle(event)
        await consumer.ack(event)
    except RetryableError:
        await consumer.retry(event)
    except PermanentError:
        await consumer.dead_letter(event)
```

The infrastructure layer owns Redis mechanics.

Business services own processing decisions.

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

This pattern should be used for important state transitions.

---

# 72. Event-Driven vs Direct Calls

Use events for:

```text
long-running work
background processing
external APIs
AI inference
research
publication
analytics
```

Use direct service calls for:

```text
small deterministic transformations
validation
pure domain calculations
repository operations
```

Do not turn every function call into an event.

---

# 73. Queue Backpressure

If consumers become slower than producers:

```text
queue depth increases
```

The system should:

```text
1. expose queue depth
2. limit worker concurrency
3. prioritize important stories
4. apply provider rate limits
5. autoscale where appropriate
6. preserve events
```

Priority should be represented in job/database state rather than relying solely on Redis ordering.

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

Examples:

```text
major breaking geopolitical event → P1
routine article enrichment → P3
historical backfill → P4
```

---

# 75. Breaking News

Breaking news should not bypass evidence requirements.

Instead:

```text
breaking story
    ↓
higher processing priority
    ↓
faster collection/research
    ↓
stricter evidence handling
```

Speed changes scheduling priority.

It does not change factual standards.

---

# 76. Historical Research

Historical jobs may be long-running.

Recommended event flow:

```text
historical.research.requested
        ↓
historical.sources.collected
        ↓
historical.analysis.completed
        ↓
historical.fact_sheet.created
```

These events can be added when the historical research subsystem is implemented.

---

# 77. Event Governance

Adding a new event requires:

```text
1. Event name
2. Schema version
3. Producer
4. Consumers
5. Payload schema
6. Idempotency key
7. Retry behavior
8. Failure behavior
9. Security review
10. Tests
```

Do not add ad-hoc Redis messages.

---

# 78. Canonical Rule

The event system must preserve this principle:

```text
EVENT
  ≠
STATE

EVENT
  →
notification that state changed or work should be processed

DATABASE
  →
authoritative state
```

Workers must always validate current database state before performing important actions.

---

# 79. Final Architecture

The canonical runtime architecture is:

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
                        Human Review
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

The fundamental reliability rule is:

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

This is the canonical event architecture for the News AI Social Media Manager.
