# News AI Social Media Manager — Event Contracts

## 1. Purpose

This document defines the canonical Redis Streams/event architecture for the News AI Social Media Manager.

It owns:

```text
event names
event envelopes
schema versions
stream/consumer-group conventions
producer/consumer contracts
idempotency
retries
dead-letter handling
transactional outbox
ordering assumptions
replay safety
event observability
```

Shared enums and lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 2. Core Principle

```text
PostgreSQL
    ↓
authoritative durable state

Redis Streams
    ↓
event/work transport

Workers
    ↓
processing

PostgreSQL
    ↓
persist result
```

Canonical invariant:

```text
EVENT != STATE
```

Workers must load current PostgreSQL state before material actions.

---

# 3. Canonical Event Pipeline

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
story.created / story.clustered
      ↓
CLAIM PROCESSOR
      ↓
claims.extracted
      ↓
RESEARCH / EVIDENCE ENGINE
      ↓
evidence.requested
      ↓
evidence.collected
      ↓
FACT CHECK / VERIFICATION
      ↓
fact_check.completed
      ↓
story.verified
      ↓
CONTENT ENGINE
      ↓
content.requested
      ↓
content.generated
      ↓
QUALITY GATE
      ↓
content.quality_checked
      ↓
HUMAN APPROVAL STORED IN POSTGRESQL (MVP)
      ↓
publication.scheduled
      ↓
PUBLISHER
      ↓
publication.executed / publication.failed
      ↓
ANALYTICS
      ↓
analytics.requested / analytics.collected
```

For the MVP, every external social publication requires explicit human approval before `publication.scheduled` becomes eligible.

---

# 4. Event Naming

Use:

```text
<aggregate>.<past-tense-action>
```

Examples:

```text
article.discovered
article.normalized
story.created
story.clustered
claims.extracted
evidence.collected
fact_check.completed
content.generated
publication.executed
```

Events describe what happened, not imperative worker commands.

---

# 5. Canonical Event Family

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

## Research/evidence

```text
evidence.requested
evidence.collected
```

## Verification

```text
fact_check.completed
story.verified
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

## Generic jobs

```text
job.retrying
job.failed
job.completed
```

---

# 6. Stream Names

Recommended logical streams:

```text
news:articles
news:stories
news:evidence
news:content
news:publishing
news:analytics
news:jobs
```

Use streams to separate operational domains, not to create a second business database.

---

# 7. Consumer Groups

Example groups:

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

Consumer groups track independent processing progress.

---

# 8. Standard Event Envelope

Every event must use a standard envelope.

```json
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
  "causation_id": null,
  "idempotency_key": "article:source:url",
  "payload": {}
}
```

Keep event payloads small. Large article bodies, research reports, AI outputs, and media remain in durable storage.

---

# 9. Envelope Fields

```text
event_id           globally unique event identifier
event_type         canonical event name
schema_version     event contract version
occurred_at        UTC event time
producer           logical producing service
producer_version   application version
aggregate_type     primary entity type
aggregate_id       primary entity ID
correlation_id     end-to-end flow identifier
causation_id       event that caused this event where applicable
idempotency_key    stable duplicate-detection key
payload            event-specific compact payload
```

Never silently change an existing schema version's semantics.

---

# 10. Article Discovered

Producer: collector

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

---

# 11. Article Normalized

```json
{
  "article_id": "uuid",
  "article_version_id": "uuid",
  "content_hash": "sha256",
  "language": "en",
  "title": "Normalized headline"
}
```

---

# 12. Story Created

```json
{
  "story_id": "uuid",
  "article_id": "uuid",
  "cluster_key": "..."
}
```

---

# 13. Story Clustered

```json
{
  "story_id": "uuid",
  "article_ids": ["uuid", "uuid"],
  "similarity_score": 0.91,
  "cluster_method": "semantic+entity+time"
}
```

This event means material was associated with a story. It does not imply all attached sources independently corroborate one another.

---

# 14. Claims Extracted

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid", "uuid"],
  "ai_run_id": "uuid",
  "model_id": "uuid"
}
```

Claims remain authoritative in PostgreSQL.

---

# 15. Evidence Requested

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid"],
  "research_scope": {
    "primary_sources": true,
    "independent_corroboration": true,
    "contradiction_search": true
  }
}
```

The effective research methodology comes from `SOURCE_AND_RESEARCH.md` and `config/research/`.

---

# 16. Evidence Collected

```json
{
  "story_id": "uuid",
  "claim_ids": ["uuid"],
  "evidence_ids": ["uuid", "uuid"],
  "research_run_id": "uuid"
}
```

This event does not state whether the evidence supports or contradicts the claim; that relationship is stored in PostgreSQL.

---

# 17. Fact Check Completed

`label` uses `FactCheckLabel`, not `ClaimVerificationStatus`.

```json
{
  "story_id": "uuid",
  "fact_check_id": "uuid",
  "label": "PARTIALLY_TRUE",
  "confidence_score": 0.78,
  "review_required": true
}
```

Allowed labels:

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

Critical invariant:

```text
UNVERIFIED != FALSE
```

---

# 18. Story Verified

```json
{
  "story_id": "uuid",
  "fact_check_ids": ["uuid"],
  "confidence_score": 0.84,
  "risk_level": "MEDIUM",
  "review_required": true
}
```

`story.verified` means the configured verification stage completed.

It does not mean every claim is true.

Individual claims retain canonical `ClaimVerificationStatus`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

---

# 19. Content Requested

```json
{
  "story_id": "uuid",
  "fact_sheet_id": "uuid",
  "requested_platforms": ["INSTAGRAM", "X"],
  "requested_formats": ["CAROUSEL", "POST"]
}
```

Content generation must resolve to a valid Fact Sheet version.

---

# 20. Content Generated

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

For the MVP, all externally publishable content remains review-required.

A future explicitly enabled low-risk mode may set `review_required = false` only under `CANONICAL_CONTRACTS.md`; mandatory-review categories can never use that bypass.

---

# 22. Human Approval Boundary

Human review is durable database state, not an event consumer assumption.

MVP path:

```text
content.quality_checked
        ↓
PostgreSQL review_state = READY_FOR_REVIEW
        ↓
human review
        ↓
PostgreSQL review_state = APPROVED
        ↓
publication becomes eligible for scheduling
```

Approval must identify the exact artifact/version.

No queue event alone authorizes external publication.

---

# 23. Publication Scheduled

```json
{
  "publication_id": "uuid",
  "content_variant_id": "uuid",
  "social_account_id": "uuid",
  "scheduled_at": "2026-09-09T15:00:00Z"
}
```

Publisher must reload PostgreSQL and re-check approval/current state before external execution.

---

# 24. Publication Executed

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

Version 1 remains unchanged and requires a non-empty `external_url`.
Version 2 has the same fields, but `external_url` may be `null`: a provider can
confirm the exact published media ID without returning a permalink. Publishers
emit version 2 rather than fabricate a URL. Both versions require confirmed
publication; a container ID or an unverified publish response is insufficient.

---

# 25. Publication Failed

```json
{
  "publication_id": "uuid",
  "attempt_id": "uuid",
  "platform": "INSTAGRAM",
  "error_code": "RATE_LIMIT",
  "retryable": true
}
```

A failure event does not authorize a blind retry if the external outcome is ambiguous.

---

# 26. Analytics Events

Request:

```json
{
  "publication_id": "uuid",
  "platform": "INSTAGRAM"
}
```

Collected:

```json
{
  "publication_id": "uuid",
  "analytics_snapshot_id": "uuid",
  "captured_at": "2026-09-09T18:00:00Z"
}
```

---

# 27. Generic Job Events

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

Job state remains durable in PostgreSQL.

---

# 28. Consumer Processing Contract

Every consumer follows:

```text
READ
 ↓
VALIDATE SCHEMA
 ↓
CHECK IDEMPOTENCY
 ↓
LOAD CURRENT POSTGRESQL STATE
 ↓
VALIDATE TRANSITION
 ↓
PROCESS
 ↓
WRITE DURABLE RESULT
 ↓
WRITE/EMIT NEXT EVENT
 ↓
ACK
```

Never ACK before durable processing completes.

---

# 29. Idempotency

Consumers must assume duplicate delivery.

For high-value consumers, a durable table such as this may be used:

```text
processed_events
---------------
event_id
consumer_group
processed_at
result
```

Unique:

```text
(event_id, consumer_group)
```

Idempotency must also be enforced through domain state, not only a processed-events table.

---

# 30. External Side-Effect Idempotency

For publication:

```text
load publication
check status
check attempts
check external identifiers
verify ambiguous prior outcome where possible
send only if safe
persist external result
```

A timeout after request transmission must not trigger an immediate duplicate post.

---

# 31. Redis Acknowledgement

Use consumer-group reads and explicit ACK only after durable success.

If a worker crashes before ACK, the event may remain pending and later be claimed by another worker.

The replacement worker must process idempotently.

---

# 32. Pending Messages

Recovery logic should inspect:

```text
pending age
original consumer
current PostgreSQL state
attempt history
transition validity
```

Do not automatically replay an old side-effect event solely because it is pending.

---

# 33. Retry Policy

Use bounded retry with exponential backoff and jitter.

Illustrative defaults:

```text
attempt 1 → 30 seconds
attempt 2 → 2 minutes
attempt 3 → 10 minutes
attempt 4 → 30 minutes
attempt 5 → 2 hours
```

Exact values are configuration, not universal constants.

Never retry indefinitely.

---

# 34. Retry Classification

Typical retryable errors:

```text
NETWORK_TIMEOUT
TEMPORARY_PROVIDER_ERROR
RATE_LIMIT
DATABASE_CONNECTION_ERROR
REDIS_CONNECTION_ERROR
TEMPORARY_SEARCH_FAILURE
```

Typical non-retryable errors:

```text
INVALID_PAYLOAD
SCHEMA_ERROR
MISSING_REQUIRED_RECORD
AUTHENTICATION_FAILURE
PERMISSION_DENIED
INVALID_SOCIAL_ACCOUNT
POLICY_BLOCK
```

Ambiguous publication outcomes require verification, not ordinary retry classification alone.

---

# 35. Dead-Letter Queue

Repeated terminal failures may enter:

```text
news:dead-letter
```

Preserve:

```text
original event ID/reference
consumer group
attempt count
final error
failure time
payload/reference
current durable job/state reference
```

Dead-letter storage is operational recovery data, not the source of truth.

---

# 36. Ordering

Do not assume global ordering across Redis Streams.

Even within a stream, consumers must validate current PostgreSQL state before acting because replay, retry, delayed delivery, and multiple workflows can produce stale messages.

---

# 37. Schema Versioning

Backward-compatible additions may remain on the same version when semantics are unchanged.

Breaking changes require a new schema version.

Breaking changes include:

```text
field removal
field type change
meaning change
enum meaning change
required-field semantic change
```

---

# 38. Transactional Outbox

For important state transitions:

```text
BEGIN POSTGRESQL TRANSACTION
        ↓
update business state
        ↓
insert outbox record
        ↓
COMMIT
        ↓
outbox publisher
        ↓
Redis Streams
```

This avoids committing business state without a corresponding event intent.

---

# 39. Outbox Model

Recommended:

```text
event_outbox
------------
id
event_id
event_type
schema_version
aggregate_type
aggregate_id
correlation_id
causation_id
payload
status
attempt_count
next_attempt_at
published_at
last_error
created_at
updated_at
```

Statuses:

```text
PENDING
PUBLISHING
PUBLISHED
FAILED
```

---

# 40. Outbox Duplicate Scenario

If Redis publish succeeds but the outbox worker crashes before marking the row `PUBLISHED`, the event may be published again.

Therefore consumers remain idempotent.

Exactly-once processing is not assumed.

---

# 41. Correlation and Causation

Use `correlation_id` to trace one story/workflow end-to-end.

Use `causation_id` to show which event triggered the next event where applicable.

This supports debugging without treating the event log as authoritative state.

---

# 42. Event Security

Never place in event payloads:

```text
API keys
OAuth tokens
refresh tokens
passwords
session cookies
private keys
authorization headers
```

Credential references/IDs may be included where needed.

Minimize unnecessary personal data.

---

# 43. Prompt Injection Boundary

Source content may contain malicious instructions.

Events that reference research/source material must never transform source text into system/runtime instructions.

Retrieved text cannot authorize:

```text
credential access
host commands
configuration changes
publication
policy changes
```

---

# 44. Replay

Replay modes may include:

```text
DRY_RUN
REBUILD
BACKFILL
LIVE
```

Publishing side effects require explicit live eligibility and current durable approval state.

Replay must never automatically re-publish historical content because an old `publication.scheduled` event is replayed.

## 44.1 Redis transport-loss reconciliation

A `PUBLISHED` outbox row proves that the dispatcher completed a prior Redis write; it
does not prove Redis still retains that transport copy. After verified Redis loss,
operators may restore bounded unfinished work from:

```text
original PUBLISHED event_outbox envelope
        + ProcessedEvent for the canonical consumer group
        + event-specific current PostgreSQL state
        + a closed event/stream/consumer policy
```

`newsctl events reconcile --dry-run --limit 100` is read-only. APPLY requires an
existing effective publication pause, a bounded limit, and a reason. It restores the
original envelope and event ID directly to the canonical stream, creates only missing
canonical consumer groups at `0`, and never resets an existing group offset or mutates
outbox history. Unsupported, malformed, obsolete, completed, and unsafe post-intent
publication work is not replayed. `ProcessedEvent` remains primary completion evidence;
workers still reload current PostgreSQL state, so repeated transport copies are safe.
Reports return `next_after_outbox_id`; pass it as `--after-outbox-id` to advance through
deterministically ordered bounded pages instead of repeatedly scanning the same history.

This operation is explicit incident recovery, not a periodic scan or a replacement for
normal outbox dispatch, pending-message reclaim, or publication-attempt recovery.

---

# 45. Priority

Recommended durable job priority classes:

```text
P0 critical operational
P1 breaking major story
P2 high editorial importance
P3 normal
P4 background enrichment
```

Priority belongs in durable job/business state. Do not rely solely on Redis insertion order.

Breaking news receives higher priority, not lower evidence standards.

---

# 46. Failure Isolation

Failures should remain domain-isolated.

For example:

```text
Instagram outage
```

must not stop:

```text
collection
research
fact checking
content preparation
```

AI/search/provider failures must preserve job state and evidence already collected.

---

# 47. Observability

Track at least:

```text
events_received_total
events_processed_total
events_failed_total
events_retried_total
events_dead_letter_total
processing_latency
pending_count
oldest_pending_age
consumer_lag
outbox_pending
outbox_failures
```

Break down by event type and consumer where useful.

---

# 48. Contract Testing

Every event schema requires tests for:

```text
valid payload
missing required field
invalid enum/UUID
unsupported schema version
duplicate delivery
out-of-order/stale delivery
retry handling
dead-letter behavior
security-sensitive field rejection
```

The E2E test sequence must include `evidence.collected` and `fact_check.completed`; they are not optional diagram shortcuts.

---

# 49. Service Responsibilities

```text
collector          → article.discovered
processor          → article.normalized, story.created, story.clustered
claim/AI worker    → claims.extracted
research worker    → evidence.collected
fact-check worker  → fact_check.completed, story.verified
content worker     → content.generated
quality worker     → content.quality_checked
scheduler          → publication.scheduled, analytics.requested
publisher          → publication.executed / publication.failed
analytics worker   → analytics.collected
```

`evidence.requested` may be produced by the fact-check/research orchestration service depending on implementation, but its semantics remain stable.

---

# 50. Event Package

Recommended:

```text
packages/events/
├── envelope.py
├── registry.py
├── producer.py
├── consumer.py
├── idempotency.py
├── retry.py
├── outbox.py
└── schemas/
    ├── article.py
    ├── story.py
    ├── claims.py
    ├── evidence.py
    ├── fact_check.py
    ├── content.py
    ├── publication.py
    ├── analytics.py
    └── jobs.py
```

Use typed Pydantic models for event validation.

---

# 51. Final Rules

```text
EVENT != STATE.
PostgreSQL is authoritative.
Redis delivery is at-least-once in practice; consumers are idempotent.
Current examples include the full verification sequence.
fact_check.completed uses FactCheckLabel.
story.verified means verification completed, not all claims true.
Human approval is durable state and mandatory for external MVP publication.
Publisher reloads current state before side effects.
Ambiguous external outcomes are verified before retry.
Events never contain secrets.
Replay never bypasses current publication eligibility.
```
