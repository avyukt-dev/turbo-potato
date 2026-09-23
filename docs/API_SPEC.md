# News AI Social Media Manager

# API_SPEC.md

**Status:** Canonical
**Document Role:** Source of truth for the HTTP API boundary, endpoint behavior, authorization expectations, asynchronous orchestration, and API-level state validation.

Shared enums, configuration ownership, runtime semantics, and lifecycle contracts are defined by `CANONICAL_CONTRACTS.md`.

Application payload contracts are defined by `CONTENT_SCHEMAS.md`.

---

# 1. Purpose

This document defines the FastAPI-facing contract for operator/admin clients and future integrations.

The API is an orchestration boundary:

```text
HTTP API
   ↓
Application Service
   ↓
Domain Logic
   ↓
Repository / Event / Provider Abstraction
```

Route handlers remain thin.

---

# 2. API Responsibilities

The API may:

```text
authenticate and authorize users
read stories/claims/evidence/Fact Sheets
request research/generation jobs
manage review decisions
manage publication scheduling/cancellation
read jobs/publication state
read effective non-secret configuration
expose health/readiness/metrics
```

The API must not directly embed:

```text
provider-specific LLM SDK calls
provider-specific search SDK calls
provider-specific social API calls
long-running research
long-running inference
complex persistence logic
platform retry logic
host-specific service-manager commands
```

These belong behind services/adapters/workers/runtime abstractions.

---

# 3. Base Paths

Recommended versioned business API:

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

Authorization should support capabilities such as:

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

Publishing permission remains distinct from ordinary editing.

Secrets and credentials must never be returned through ordinary API responses.

---

# 5. Request Identity and Tracing

Requests should carry/produce:

```text
request_id
correlation_id where applicable
actor identity
timestamp
```

Long-running operations return durable `job_id` values.

---

# 6. Health and Readiness

## GET /health

Lightweight process liveness.

```json
{
  "status": "ok"
}
```

## GET /ready

Dependency readiness.

```json
{
  "status": "ready",
  "postgres": true,
  "redis": true,
  "ai_router": true
}
```

Readiness must not expose secrets or raw host/runtime command details.

---

# 7. Runtime Boundary

Host/service-manager control is not part of the ordinary HTTP application API.

The canonical host-control surface is `newsctl`/`RuntimeController` as defined by infrastructure/operations documents.

This prevents the web API from becoming a privileged remote shell or embedding `systemctl`, `rc-service`, `launchctl`, or other native commands.

A future secure runtime-status API may expose read-only normalized health metadata, but native command execution must remain behind the runtime abstraction and strict authorization.

---

# 8. Stories

## GET /api/v1/stories

List stories with bounded pagination.

Useful filters:

```text
status
category/topic
risk_level
sensitive_topic
created_after
created_before
updated_after
review_state
minimum_confidence
```

## GET /api/v1/stories/{story_id}

Returns story state plus linked summaries/references:

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

Requests research asynchronously.

```text
validate current state
create/reuse durable job
persist request
write outbox/event intent
return 202 quickly
```

Example:

```json
{
  "story_id": "uuid",
  "job_id": "uuid",
  "status": "QUEUED"
}
```

Do not block until research completes.

---

# 10. Claims

## GET /api/v1/stories/{story_id}/claims

Returns `Claim` contracts.

## GET /api/v1/claims/{claim_id}

Returns:

```text
claim
ClaimVerificationStatus
confidence assessment
supporting/contradicting evidence references
fact-check references where present
```

Fact-check labels must never be substituted into `Claim.status`.

---

# 11. Evidence

## GET /api/v1/claims/{claim_id}/evidence

Returns evidence relationships.

## POST /api/v1/claims/{claim_id}/research

Requests additional claim-specific research.

The API must not present AI-generated prose as evidence unless it refers to actual persisted source/evidence records.

---

# 12. Sources

## GET /api/v1/sources

Lists source-registry records from the `config/sources/`/database domain.

Filters may include:

```text
source level/role
country
language
enabled
source type/domain
collection method
```

## GET /api/v1/sources/{source_id}

Returns source identity, collection metadata, and non-secret source-registry state.

Evidence-policy interpretation belongs to the research domain and should not be presented as an intrinsic truth property of the source record.

## POST /api/v1/sources/{source_id}/disable

## POST /api/v1/sources/{source_id}/enable

Administrative source changes are audited.

---

# 13. Research Policy View

Authorized read-only endpoints may expose effective research/evidence policy separately from source registry metadata.

Recommended:

```text
GET /api/v1/config/research
```

This may expose safe effective values derived from:

```text
config/research/source-policy.yaml
config/research/search-policy.yaml
config/research/corroboration.yaml
config/research/fact-check.yaml
config/research/historical-research.yaml
```

Do not expose secrets/provider credentials.

---

# 14. Fact Checks

## GET /api/v1/stories/{story_id}/fact-checks

## GET /api/v1/fact-checks/{fact_check_id}

Fact-check responses use `FactCheckLabel`:

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

# 15. Fact Sheets

## GET /api/v1/stories/{story_id}/fact-sheets

Lists versions.

## GET /api/v1/stories/{story_id}/fact-sheet

Returns the current application-selected version.

## GET /api/v1/fact-sheets/{fact_sheet_id}

Returns one exact immutable version.

## POST /api/v1/stories/{story_id}/fact-sheet/generate

Queues generation from current claim/evidence state.

A Fact Sheet must not bypass the claim/evidence layer.

---

# 16. Content Generation

## POST /api/v1/stories/{story_id}/content

Example request:

```json
{
  "fact_sheet_id": "uuid",
  "platforms": ["INSTAGRAM", "X", "TELEGRAM"],
  "formats": ["CAROUSEL", "POST"],
  "languages": ["en"]
}
```

Returns `202 + job_id`.

Requests without a valid Fact Sheet version are rejected.

---

# 17. Content Retrieval

## GET /api/v1/stories/{story_id}/content

## GET /api/v1/content/{content_variant_id}

Return:

```text
content payload
Fact Sheet reference/version
claim/source IDs used
quality state
review state
risk/sensitivity
AI provenance references
```

---

# 18. Quality Checks

## POST /api/v1/content/{content_variant_id}/quality-check

Queues a check.

## GET /api/v1/content/{content_variant_id}/quality-check

Returns `QualityCheck`.

MVP invariant:

```text
quality passed != publication approved
```

---

# 19. Review Queue

## GET /api/v1/review/queue

Useful filters:

```text
risk level
sensitive topic
story category
review state
publication deadline
breaking-news priority
```

Ordering may consider editorial priority and urgency but must not alter factual confidence.

---

# 20. Review Detail

## GET /api/v1/review/{artifact_type}/{artifact_id}

Returns the exact reviewable artifact/version with:

```text
Fact Sheet
claims/evidence
contradictions
quality results
content
risk/sensitivity
AI provenance
```

Reviewers should not receive only the final caption without evidence context.

---

# 21. Review Actions

```text
POST /api/v1/review/{artifact_type}/{artifact_id}/approve
POST /api/v1/review/{artifact_type}/{artifact_id}/reject
POST /api/v1/review/{artifact_type}/{artifact_id}/request-changes
POST /api/v1/review/{artifact_type}/{artifact_id}/request-research
```

Every decision is audited with actor, artifact/version, decision, timestamp, and reason where applicable.

### Telegram approval channel

```text
POST /api/v1/integrations/telegram/review/webhook
```

Telegram is an authenticated interaction channel over the same `ReviewService`; it is not a
second approval state machine. The webhook requires Telegram's
`X-Telegram-Bot-Api-Secret-Token`, an allowlisted chat ID, and an allowlisted Telegram user ID
mapped by deployment configuration to the existing stable `reviewer_id` and capabilities.

The `/review` command returns the next exact reviewable artifact as a bounded sequence of
messages containing the complete publication-visible content, immutable Fact Sheet, editorial
brief, quality findings, and safe AI/media provenance. Exact reviewed images are sent as media
previews. The approval button is sent only after every context message and media preview succeeds,
and binds the content-variant UUID and exact artifact version. Callback replay uses a deterministic
idempotency identity; stale,
superseded, already-decided, or otherwise ineligible artifacts remain rejected by the canonical
transactional review service. Telegram credentials, user IDs, callback identifiers, and raw
provider failures are not approval evidence and must not appear in public diagnostics.

The current Telegram channel supports exact-version approval. Decisions that require a reason
(`REJECTED` and `CHANGES_REQUESTED`) continue through a reason-capable review client rather than
fabricating or omitting the required human rationale.

---

# 22. MVP Approval Invariant

For the MVP:

```text
ALL external social publication requires explicit human approval.
```

Invalid transitions:

```text
DRAFT → publish
QUALITY_CHECKED → publish
READY_FOR_REVIEW → publish
LOW risk without approval → publish
```

Future low-risk automation may exist only under `CANONICAL_CONTRACTS.md` and is disabled by default.

---

# 23. Publications

## POST /api/v1/publications

Creates a publication request for approved content.

Example:

```json
{
  "content_variant_id": "uuid",
  "social_account_id": "uuid",
  "platform": "INSTAGRAM",
  "scheduled_at": "2026-09-10T14:00:00Z",
  "idempotency_key": "..."
}
```

Service re-checks:

```text
content exists
Fact Sheet version exists
human approval valid for exact content version
account eligible
risk/policy permits scheduling
no conflicting publication operation
```

---

# 24. Publication Retrieval and Attempts

```text
GET /api/v1/publications/{publication_id}
GET /api/v1/publications/{publication_id}/attempts
```

Attempt history is immutable/auditable.

---

# 25. Publication Cancellation

## POST /api/v1/publications/{publication_id}/cancel

Cancels only when the current durable state permits.

Internal cancellation does not imply an already externalized social post disappeared.

---

# 26. Publication Retry

## POST /api/v1/publications/{publication_id}/retry

Retry is permitted only when safe.

Ambiguous external outcomes require platform verification before retry.

---

# 27. Publish Now

## POST /api/v1/publications/{publication_id}/publish-now

Enqueues immediate execution for an already eligible and human-approved publication.

It does not bypass:

```text
approval
account/platform validation
idempotency
duplicate protection
kill switch
```

---

# 28. Social Accounts

```text
GET /api/v1/social-accounts
GET /api/v1/social-accounts/{account_id}
```

Return non-secret state/capability metadata only.

Credential connection/reauth uses secure platform-specific mechanisms.

---

# 29. Jobs

## GET /api/v1/jobs/{job_id}

Returns:

```text
job type
status
priority
attempt count
timestamps
safe error summary
result references
```

Do not expose raw credentials or unnecessary provider internals.

---

# 30. AI Operations

```text
GET /api/v1/ai/models
GET /api/v1/ai/runs/{ai_run_id}
```

Safe provenance may include:

```text
provider
model
task
prompt version
latency
usage where available
validation result
status
```

Raw prompts/outputs may require additional authorization and retention/privacy checks.

---

# 31. Configuration Endpoints

Authorized read-only effective configuration may expose:

```text
GET /api/v1/config/sources
GET /api/v1/config/research
GET /api/v1/config/editorial
GET /api/v1/config/models
GET /api/v1/config/platforms
```

These domains must preserve the ownership defined by `CANONICAL_CONTRACTS.md`.

Runtime mutation is restricted/audited and must not create a competing configuration source of truth.

Secrets are excluded.

---

# 32. Metrics

## GET /metrics

Returns Prometheus-compatible, read-only aggregate telemetry. A dependency outage
does not turn this endpoint into service control or an external-provider call.
Labels use bounded operational categories and never durable entity identifiers,
content, URLs, account identifiers, or raw error messages.

Possible metrics:

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

Do not put secrets or high-cardinality sensitive source content in metric labels.

---

# 33. Error Contract

```json
{
  "error": {
    "code": "INVALID_STATE",
    "message": "Content is not approved for publication",
    "request_id": "uuid"
  }
}
```

Never expose stack traces, filesystem paths, tokens, credentials, or database secrets to ordinary clients.

---

# 34. HTTP Status Semantics

Suggested:

```text
200 / 201 success
202       asynchronous work accepted
400       malformed request
401       unauthenticated
403       unauthorized/policy blocked
404       not found
409       state/version/idempotency conflict
422       schema/domain validation error
429       application rate limit where appropriate
5xx       server/dependency failure
```

Provider-specific failures should be normalized behind adapters.

---

# 35. Optimistic Concurrency

Versioned editorial artifacts should accept an expected version/precondition.

Stale edits produce a conflict rather than silently overwriting reviewed material.

---

# 36. Idempotency

Support idempotency for operations that can duplicate durable work or side effects, especially:

```text
research requests
content generation
publication creation
publish-now execution
```

Publication idempotency is mandatory.

---

# 37. Long-Running Work

Canonical pattern:

```text
POST
 ↓
validate
 ↓
persist job/request
 ↓
transaction/outbox
 ↓
event
 ↓
202 + job_id
```

Worker:

```text
consume
 ↓
load PostgreSQL state
 ↓
process
 ↓
persist result
 ↓
emit next event
```

The HTTP request does not remain open for research, inference, media generation, or social publishing.

---

# 38. Event Integration

Use event names owned by `EVENTS.md`:

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

Do not invent alternate names for the same lifecycle step.

---

# 39. State Validation

Invalid examples:

```text
unresearched story → factual publication
unapproved content → external execution
rejected artifact → scheduling
stale approval → publish modified version
ambiguous external timeout → blind retry
```

Domain services enforce transitions; UI controls are not the security boundary.

---

# 40. Sensitive Topics

Sensitive/high-risk categories receive no API shortcut around evidence or review requirements.

MVP approval applies to all external publication regardless of risk.

---

# 41. Pagination

Collection endpoints use bounded pagination.

Cursor pagination is preferred for high-volume timelines/feeds where practical.

---

# 42. Audit Logging

Audit significant API mutations:

```text
review decisions
source enable/disable
configuration overrides
publication create/cancel/retry/publish-now
account administrative actions
```

Audit actor, action, target, exact version/state, timestamp, and result.

---

# 43. Security

The API must not become a privileged host-control shell.

Host/service-manager commands remain behind `RuntimeController/newsctl` and the runtime adapter layer.

The API also must not expose:

```text
provider secrets
social tokens
private keys
internal database credentials
raw authorization headers
```

---

# 44. Final API Rules

```text
FastAPI is an orchestration boundary, not a provider SDK dumping ground.
Long-running work is asynchronous and durable.
Claim status and fact-check labels remain separate.
Fact Sheet is required before normal content generation.
Research config is a first-class separate domain.
Source registry metadata is not itself evidence policy.
All external MVP publication requires explicit human approval.
Publication retries are idempotency/ambiguity safe.
Host-specific service commands are not exposed through business API routes.
```
