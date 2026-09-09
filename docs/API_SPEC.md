# News AI Social Media Manager

# API_SPEC.md

**Status:** Canonical
**Document Role:** Source of truth for the HTTP API boundary, endpoint behavior, authorization expectations, asynchronous orchestration, and API-level state validation.

Shared enums and lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

Application payload contracts are defined by `CONTENT_SCHEMAS.md`.

---

# 1. Purpose

This document defines the FastAPI-facing contract for operator/admin clients and future integrations.

The API is an orchestration boundary.

```text
HTTP API
   ↓
Application Service
   ↓
Domain Logic
   ↓
Repository / Event / Provider Abstraction
```

Route handlers must remain thin.

---

# 2. API Responsibilities

The API may:

```text
authenticate and authorize users
read stories/claims/evidence/fact sheets
request research and generation jobs
manage review decisions
manage publication scheduling/cancellation
read job and publication state
read effective configuration
expose health/readiness
```

The API must not directly embed:

```text
provider-specific LLM SDK calls
provider-specific social API calls
long-running research
long-running inference
complex persistence logic
platform retry logic
```

Those belong behind services/adapters/workers.

---

# 3. Base Paths

Recommended versioned API:

```text
/api/v1
```

Operational endpoints may remain outside the versioned business API:

```text
/health
/ready
/metrics
```

---

# 4. Authentication and Authorization

Administrative and mutation endpoints require authenticated access.

Implementation may use secure session or JWT-based authentication according to deployment policy.

Authorization must support role/permission checks appropriate to actions such as:

```text
view
edit
research
review
approve
publish
configure
administer
```

Publishing permission must remain distinct from ordinary content-edit permission.

Secrets and credentials must never be returned by ordinary API responses.

---

# 5. Request Identity and Tracing

Requests should carry/produce:

```text
request_id
correlation_id where applicable
actor identity
timestamp
```

Long-running work should return a durable `job_id`.

---

# 6. Health

## GET /health

Basic process liveness.

Example:

```json
{
  "status": "ok"
}
```

This endpoint should be lightweight.

---

# 7. Readiness

## GET /ready

Reports whether required dependencies are ready for the API's current role.

Example:

```json
{
  "status": "ready",
  "postgres": true,
  "redis": true,
  "ai_router": true
}
```

Readiness must not expose secrets or sensitive configuration.

---

# 8. Stories

## GET /api/v1/stories

List stories with bounded pagination.

Supported filters may include:

```text
status
category
topic
risk_level
sensitive_topic
created_after
created_before
updated_after
review_state
minimum_confidence
```

Do not return unbounded result sets.

## GET /api/v1/stories/{story_id}

Returns the canonical story view plus linked summaries/references such as:

```text
claims
source summary
evidence summary
timeline
entities
editorial scores
risk/sensitivity
verification stage
review state
```

The response must not imply that `story.verified` means every claim is true.

---

# 9. Story Research

## POST /api/v1/stories/{story_id}/research

Requests research.

The endpoint should:

```text
validate current state
create/reuse durable job
persist request
emit evidence.requested through the normal service/outbox path
return quickly
```

Example response:

```json
{
  "story_id": "uuid",
  "job_id": "uuid",
  "status": "QUEUED"
}
```

Do not block the HTTP request until research completes.

---

# 10. Claims

## GET /api/v1/stories/{story_id}/claims

Returns claims using the `Claim` contract from `CONTENT_SCHEMAS.md`.

## GET /api/v1/claims/{claim_id}

Returns:

```text
claim
ClaimVerificationStatus
confidence assessment
supporting evidence references
contradictory evidence references
fact-check references where present
```

Fact-check verdict labels must not be substituted into `Claim.status`.

---

# 11. Evidence

## GET /api/v1/claims/{claim_id}/evidence

Returns evidence relationships for a claim.

## POST /api/v1/claims/{claim_id}/research

Requests additional claim-specific research.

The API must not present AI-generated prose as evidence unless it refers to an actual persisted source/evidence item.

---

# 12. Sources

## GET /api/v1/sources

Lists configured source records.

Filters may include:

```text
level
country
language
enabled
source type/domain
```

## GET /api/v1/sources/{source_id}

Returns source registry metadata and effective policy.

## POST /api/v1/sources/{source_id}/disable

Disables the source according to authorization/policy.

## POST /api/v1/sources/{source_id}/enable

Re-enables it.

All administrative source changes must be audited.

---

# 13. Fact Checks

## GET /api/v1/stories/{story_id}/fact-checks

Returns fact checks using `FactCheckLabel`.

## GET /api/v1/fact-checks/{fact_check_id}

Returns:

```text
label
summary
confidence assessment
evidence references
review state
AI provenance reference where applicable
```

Critical invariant:

```text
UNVERIFIED != FALSE
```

---

# 14. Fact Sheets

## GET /api/v1/stories/{story_id}/fact-sheets

Lists versions.

## GET /api/v1/stories/{story_id}/fact-sheet

Returns the current eligible/current version according to application policy.

## GET /api/v1/fact-sheets/{fact_sheet_id}

Returns the exact immutable Fact Sheet version.

## POST /api/v1/stories/{story_id}/fact-sheet/generate

Queues Fact Sheet generation from current research/evidence state.

Example response:

```json
{
  "job_id": "uuid",
  "status": "QUEUED"
}
```

A Fact Sheet must not be generated by bypassing the claim/evidence layer.

---

# 15. Content Generation

## POST /api/v1/stories/{story_id}/content

Requests platform variants from a valid Fact Sheet.

Example request:

```json
{
  "fact_sheet_id": "uuid",
  "platforms": ["INSTAGRAM", "X", "TELEGRAM"],
  "formats": ["CAROUSEL", "POST"],
  "languages": ["en"]
}
```

Response:

```json
{
  "job_id": "uuid",
  "status": "QUEUED"
}
```

The API must reject requests that cannot resolve to a valid Fact Sheet version.

---

# 16. Content Retrieval

## GET /api/v1/stories/{story_id}/content

Lists content drafts/variants.

## GET /api/v1/content/{content_variant_id}

Returns:

```text
content payload
Fact Sheet reference/version
claim IDs used
source IDs used
quality state
review state
risk/sensitivity
AI provenance references
```

---

# 17. Quality Checks

## POST /api/v1/content/{content_variant_id}/quality-check

Queues a quality check.

## GET /api/v1/content/{content_variant_id}/quality-check

Returns the `QualityCheck` contract from `CONTENT_SCHEMAS.md`.

For the MVP:

```text
quality passed != publication approved
```

A passing automated check does not authorize an external post.

---

# 18. Review Queue

## GET /api/v1/review/queue

Returns reviewable artifacts.

Useful filters:

```text
risk level
sensitive topic
story category
review state
publication deadline
breaking-news priority
```

Ordering may consider risk, priority, age, and scheduled intent, but must not change factual confidence.

---

# 19. Review Detail

## GET /api/v1/review/{artifact_type}/{artifact_id}

Returns the reviewable artifact together with the evidence packet and exact version.

Review UI should expose evidence, contradictions, Fact Sheet, content, quality results, and provenance—not only the final caption.

---

# 20. Review Actions

## POST /api/v1/review/{artifact_type}/{artifact_id}/approve

Approves the exact version supplied/loaded.

## POST /api/v1/review/{artifact_type}/{artifact_id}/reject

Rejects it.

## POST /api/v1/review/{artifact_type}/{artifact_id}/request-changes

Returns it for editing/regeneration.

## POST /api/v1/review/{artifact_type}/{artifact_id}/request-research

Requests additional research.

Every decision must be audited with actor, artifact/version, decision, timestamp, and reason where applicable.

---

# 21. MVP Approval Invariant

For the MVP/current brainstorming implementation phase:

```text
ALL external social publication requires explicit human approval.
```

Therefore API mutation paths must not permit:

```text
DRAFT → publish
QUALITY_CHECKED → publish
READY_FOR_REVIEW → publish
```

without an explicit approved human review state.

Future low-risk automation may be introduced only under `CANONICAL_CONTRACTS.md` and is disabled by default.

---

# 22. Publications

## POST /api/v1/publications

Creates a publication record/request for approved content.

Example request:

```json
{
  "content_variant_id": "uuid",
  "social_account_id": "uuid",
  "platform": "INSTAGRAM",
  "scheduled_at": "2026-09-10T14:00:00Z",
  "idempotency_key": "..."
}
```

The service must re-check:

```text
content exists
Fact Sheet version exists
human approval is valid for this content version
account is eligible
risk policy permits scheduling
publication duplicate does not exist
```

---

# 23. Publication Retrieval

## GET /api/v1/publications/{publication_id}

Returns current durable publication state and attempt summary.

## GET /api/v1/publications/{publication_id}/attempts

Returns immutable publication attempts.

---

# 24. Publication Cancellation

## POST /api/v1/publications/{publication_id}/cancel

Cancels only when current state permits.

Do not claim cancellation of an already externalized post merely because the internal record changed.

---

# 25. Publication Retry

## POST /api/v1/publications/{publication_id}/retry

Manual retry is permitted only when safe.

For ambiguous external outcomes:

```text
verify platform state first
```

Do not blindly create a duplicate external post.

---

# 26. Publish Now

## POST /api/v1/publications/{publication_id}/publish-now

This endpoint may enqueue immediate execution for an already eligible and human-approved publication.

It must not bypass:

```text
approval
platform validation
account validation
idempotency
duplicate protection
kill switch
```

---

# 27. Social Accounts

## GET /api/v1/social-accounts

Returns non-secret account metadata/capabilities.

## GET /api/v1/social-accounts/{account_id}

Returns account state without raw credentials.

Administrative connect/reauth flows should use secure credential mechanisms and platform-specific adapters.

---

# 28. Jobs

## GET /api/v1/jobs/{job_id}

Returns:

```text
job type
status
priority
attempt count
created/start/completion timestamps
safe error summary
result references
```

Do not expose secrets, raw credentials, or unnecessary provider internals.

---

# 29. AI Operations

## GET /api/v1/ai/models

Lists configured model capabilities and enablement state where authorized.

## GET /api/v1/ai/runs/{ai_run_id}

Returns safe provenance metadata such as:

```text
provider
model
task type
prompt version
latency
usage where available
validation result
status
```

Raw prompts/outputs may require additional authorization and retention/privacy checks.

---

# 30. Configuration

Read-only administrative endpoints may expose effective configuration:

```text
GET /api/v1/config/editorial
GET /api/v1/config/sources
GET /api/v1/config/models
GET /api/v1/config/platforms
```

Runtime mutation should be restricted, audited, and must not create a second configuration truth source that conflicts with versioned configuration policy.

---

# 31. Metrics

Recommended operational endpoint:

```text
GET /metrics
```

Metrics may include:

```text
articles_collected_total
stories_created_total
claims_extracted_total
research_jobs_total
research_failures_total
ai_runs_total
ai_failures_total
content_generated_total
quality_failures_total
publication_attempts_total
publication_failures_total
queue_depth
```

Do not expose secrets or sensitive source content through metrics labels.

---

# 32. Error Contract

Use a consistent envelope.

```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Content is not approved for publication",
    "request_id": "uuid"
  }
}
```

Do not expose stack traces, filesystem paths, credentials, tokens, or database secrets to ordinary clients.

---

# 33. HTTP Status Semantics

Suggested general use:

```text
200 / 201   success
202         asynchronous work accepted
400         malformed/invalid request
401         unauthenticated
403         unauthorized/policy blocked
404         resource not found
409         state/version/idempotency conflict
422         schema/domain validation error
429         application-level rate limit where applicable
5xx         server/dependency failure
```

Provider-specific errors should be normalized behind adapters.

---

# 34. Optimistic Concurrency

Mutation endpoints for versioned editorial artifacts should accept an expected version or equivalent precondition.

If the artifact has changed:

```text
409 CONFLICT
```

rather than silently overwriting another reviewer/editor's work.

---

# 35. Idempotency

Requests that can produce duplicate durable work or external side effects should support idempotency.

Especially:

```text
research requests
content generation requests
publication creation
publish-now execution
```

Publication idempotency is mandatory.

---

# 36. Long-Running Work

Canonical pattern:

```text
POST request
    ↓
validate
    ↓
persist job/request
    ↓
transaction/outbox
    ↓
event
    ↓
return 202 + job_id
```

Worker:

```text
consume event
    ↓
load PostgreSQL state
    ↓
process
    ↓
persist result
    ↓
emit next event
```

The API should not hold open long requests for research, inference, media generation, or social publishing.

---

# 37. Event Integration

Use the event names owned by `EVENTS.md`.

Core event family:

```text
article.discovered
article.normalized
story.created
story.clustered
claims.extracted
evidence.requested
evidence.collected
fact_check.completed
story.verified
content.requested
content.generated
content.quality_checked
publication.scheduled
publication.executed
publication.failed
analytics.requested
analytics.collected
```

Do not invent alternate event names for the same lifecycle step inside API code.

---

# 38. State Validation Examples

Invalid:

```text
unresearched story → factual publication
unapproved content → publication execution
rejected artifact → schedule
stale approval → publish modified version
ambiguous timeout → blind retry
```

Valid state transitions are enforced by domain services, not only UI controls.

---

# 39. Sensitive Topics

The API may expose sensitive-topic metadata to authorized reviewers.

Sensitive/high-risk categories must not receive an endpoint-specific shortcut around evidence or review requirements.

MVP approval requirements apply to all external publication regardless of risk.

---

# 40. Pagination

Collection endpoints must use bounded pagination consistently.

Cursor pagination is preferred for high-volume timelines/feeds, but the implementation may use page-based pagination initially if consistent and tested.

---

# 41. Sorting and Filtering

Sort fields should be allowlisted.

Do not expose arbitrary database-column or raw SQL ordering/filter expressions from client input.

---

# 42. Audit Logging

Audit significant mutations:

```text
source enable/disable
editorial/config changes
review decisions
content edits
publication creation
schedule changes
cancellation
manual retry
kill-switch changes
credential/account state changes
```

---

# 43. Security

API security must include:

```text
authentication
authorization
input validation
rate limiting where needed
CSRF/session protections where applicable
secret isolation
audit logging
secure headers/TLS at public boundary
least privilege
```

Admin endpoints should not be exposed publicly merely for convenience.

---

# 44. API Versioning

Breaking HTTP contract changes require a new API version or an explicitly managed compatibility transition.

Event schema versions and application artifact schema versions are separate from HTTP API versioning.

---

# 45. Contract Testing

`TESTING_AND_EVALUATION.md` owns testing strategy.

API contract tests should cover:

```text
authentication/authorization
payload validation
enum validation
pagination
optimistic concurrency
idempotency
state-transition rejection
MVP human-approval enforcement
job creation
safe error responses
no-secret leakage
```

---

# 46. Final API Rules

```text
The API orchestrates; it does not become the business logic layer.
Long-running work is asynchronous.
PostgreSQL remains authoritative.
Redis messages are not API truth.
Claim status is not a fact-check verdict.
Fact Sheet is required before normal content generation.
Quality pass is not publication approval.
All external MVP publication requires explicit human approval.
Publication retries must be idempotent and ambiguity-aware.
Provider details stay behind adapters.
Secrets never appear in ordinary responses.
```

---

# 47. Documentation Relationship

This document owns the HTTP API boundary.

`CANONICAL_CONTRACTS.md` owns shared enums and lifecycle semantics.

`CONTENT_SCHEMAS.md` owns structured payload contracts.

`DATA_MODEL.md` owns persistence.

`EVENTS.md` owns event contracts.

`SOURCE_AND_RESEARCH.md` owns research behavior.

`AI_PLATFORM.md` owns AI routing/execution.

`SOCIAL_PUBLISHING.md` owns platform execution and publication safety.
