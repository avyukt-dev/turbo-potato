# News AI Social Media Manager

# SOCIAL_PUBLISHING.md

**Status:** Canonical
**Document Role:** Source of truth for social-platform adapters, media preparation, publishing, scheduling, retries, account credentials, publication state, and platform-specific constraints.

Shared publication/review enums and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This document defines how eligible, quality-checked content moves from the content engine to external social platforms.

It covers social accounts, adapters, media preparation and delivery, authentication, publication state, scheduling, retries, rate limits, idempotency, verification, corrections, and analytics handoff.

It does not define evidence evaluation, editorial scoring, AI model architecture, or database infrastructure.

---

# 2. Publishing Principle

The social layer is a transport/execution boundary, not a source of factual truth and not an editorial decision-maker.

Canonical MVP flow:

```text
FACT SHEET
  ↓
CONTENT
  ↓
QUALITY GATE
  ↓
HUMAN APPROVAL
  ↓
PUBLICATION RECORD
  ↓
SCHEDULER / PUBLISHER
  ↓
SOCIAL PLATFORM
```

The publisher must not rewrite factual claims.

---

# 3. MVP Approval Policy

For the MVP and current brainstorming implementation phase:

```text
ALL external social publication requires explicit human approval.
```

This applies to low-, medium-, high-, and critical-risk content.

Automated quality checks may determine whether content is ready for review, but they do not authorize publication.

A future release MAY permit low-risk auto-approval only when explicitly enabled and only under the conditions in `CANONICAL_CONTRACTS.md`.

Sensitive or mandatory-review categories must never use low-risk auto-approval.

---

# 4. Supported Platforms

Initial adapters:

```text
INSTAGRAM
X
FACEBOOK
TELEGRAM
```

Future adapters may include YouTube, LinkedIn, Threads, WhatsApp Channels, Bluesky, or Mastodon without changing the editorial engine.

---

# 5. Adapter Architecture

```text
Content Variant
   ↓
Platform Renderer
   ↓
Media Preparation
   ↓
Platform Adapter
   ↓
Publish
   ↓
Verify
   ↓
Publication Record
```

Conceptual contract:

```python
class SocialPlatformAdapter:
    def validate_content(...): ...
    def prepare_media(...): ...
    def publish(...): ...
    def verify_publication(...): ...
    def delete_or_archive(...): ...
```

Adapters own platform communication mechanics. They do not own editorial decisions.

---

# 6. Renderer vs Adapter

```text
Renderer → converts approved content into platform presentation
Adapter  → communicates with the platform API
```

Keep these responsibilities separate.

---

# 7. Content Contract

The publishing layer consumes an already generated content variant tied to a specific Fact Sheet/content version.

It must preserve:

```text
story_id
fact_sheet_id/version
content_variant_id
platform
format
language
content/media references
review state
risk level
```

Normal publishing must not accept arbitrary free-form factual content that bypasses the content/review pipeline.

---

# 8. Review State vs Publication State

Review state and publication state are separate.

Canonical `ReviewState`:

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

Canonical `PublicationStatus`:

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

Post-publication descriptors may additionally record:

```text
UPDATED
CORRECTED
ARCHIVED
```

They must not erase the original audit trail.

---

# 9. Publication State Rules

For the MVP, an external publication must follow:

```text
DRAFT
  ↓
READY_FOR_REVIEW
  ↓
APPROVED (explicit human decision)
  ↓
SCHEDULED
  ↓
PUBLISHING
  ↓
PUBLISHED
```

Failure branches:

```text
PUBLISHING
    ├── RETRYING
    ├── BLOCKED
    └── FAILED
```

No `DRAFT → PUBLISHED` bypass exists in the MVP.

---

# 10. Approval Integrity

Approval must identify:

```text
reviewer
artifact/content version
fact sheet version
approval time
decision/reason
```

If material facts, sources, wording, or media change after approval, the approval must be invalidated when policy requires and the new version returned to review.

---

# 11. Publication Record

Each publication should retain:

```text
publication_id
story_id
content_variant_id
social_account_id
platform
status
scheduled_at
started_at
published_at
external_post_id
external_url
error_code
error_message
attempt_count
created_at
updated_at
```

---

# 12. Publication Attempts

Each external attempt is a separate record:

```text
attempt_id
publication_id
attempt_number
started_at
completed_at
status
platform_response metadata
error_code
error_class
```

Never overwrite retry history.

---

# 13. Idempotency

Publishing must be idempotent.

The system must avoid duplicate posts caused by worker restarts, API/network timeouts, process crashes, queue redelivery, or duplicate scheduler execution.

Canonical internal operation is the `publication_id` tied to its content variant and account.

Before publishing:

```text
check durable publication state
        ↓
check attempts/external identifiers
        ↓
verify ambiguous previous outcome where possible
        ↓
publish only when safe
```

Never blindly retry an ambiguous publish timeout.

---

# 14. Scheduling

The scheduler finds eligible `SCHEDULED` publications and enqueues publication work. It does not directly call platform APIs.

Before execution verify:

```text
review = APPROVED
content quality checks passed
account active
credentials valid
media available
public media reachable where required
platform constraints satisfied
publication not already completed
risk/policy satisfied
```

If eligibility fails, use `BLOCKED` or `RETRYING` according to cause.

---

# 15. Time Zones

Store canonical timestamps in UTC/`TIMESTAMPTZ`.

Editorial scheduling may use `Asia/Kolkata` or another configured audience timezone, but the intended timezone must be explicit.

---

# 16. Media Storage vs Public Delivery

Media persistence and public delivery are separate concerns.

Initial persistence may use local POCO storage:

```text
/opt/news-ai/media/
```

If a platform requires publicly accessible media:

```text
Local / Generated Asset
      ↓
Media Delivery Layer or Object Storage
      ↓
Public HTTPS URL
      ↓
Platform API
```

The publishing layer must never expose arbitrary local filesystem paths, database/admin services, backups, or credentials merely to satisfy media ingestion.

---

# 17. Media Lifecycle

Recommended states:

```text
GENERATED
VALIDATED
UPLOADED
PUBLICLY_AVAILABLE
USED
ARCHIVED
DELETED
```

Do not delete assets while pending publications depend on them.

Use content hashes such as SHA-256 to detect duplicate/corrupted assets.

---

# 18. Instagram

Instagram publishing should use the current official Meta/Instagram APIs supported by the connected professional account.

Initial required formats:

```text
IMAGE
VIDEO
REEL
CAROUSEL
```

Canonical flow:

```text
Approved Instagram Content
       ↓
Renderer
       ↓
Media Validation
       ↓
Public HTTPS Delivery where required
       ↓
Container Creation
       ↓
Publish
       ↓
Verify
       ↓
Store Platform ID
```

Platform limits, permissions, eligibility, token requirements, dimensions, duration, codecs, and current API versions must be validated against current official documentation at implementation/deployment time and kept configuration-driven.

---

# 19. X

The X adapter may support single posts, threads, replies, quote posts, and media attachments according to current API capabilities.

Thread publication must record each successfully created post. If a later post fails, preserve partial state and resume only when safe.

Never restart an already-partially-published thread from the beginning without verifying platform state.

---

# 20. Facebook

Facebook uses the appropriate current Meta API for the connected destination. Adapter logic handles media upload/preparation, publication, verification, and platform identifiers.

---

# 21. Telegram

Telegram supports channel posts and supported media through the configured bot/API mechanism.

Verify target channel, membership/permission, and credential validity before scheduling.

---

# 22. Social Accounts

Canonical account data:

```text
account_id
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

Recommended account states:

```text
ACTIVE
PAUSED
AUTH_ERROR
RATE_LIMITED
DISABLED
REAUTH_REQUIRED
```

Raw tokens must never be stored in ordinary plaintext application tables.

---

# 23. Credentials

Use environment secrets, encrypted credential storage, or a secret manager according to deployment maturity.

Never place access tokens, refresh tokens, API secrets, cookies, or authorization headers in Git, logs, prompts, frontend state, or ordinary plaintext database fields.

Support expiration, refresh, replacement, revocation, and validation.

---

# 24. Capability Discovery

Adapters should expose/configure capabilities rather than assuming every account supports every feature.

Example:

```json
{
  "text": true,
  "image": true,
  "video": true,
  "carousel": true,
  "thread": false,
  "scheduled_publish": false
}
```

---

# 25. Platform Validation

Before scheduling/execution validate:

```text
platform capability
content format
media
account
credentials
policy/review state
```

Invalid content should fail before external API execution.

---

# 26. Rate Limiting and Retries

Rate limits operate at platform, account, and application levels.

Classify failures:

```text
TRANSIENT
RATE_LIMIT
AUTHENTICATION
VALIDATION
PERMISSION
MEDIA
DUPLICATE
NOT_FOUND
PLATFORM
UNKNOWN
```

Only safe classes automatically retry.

Use provider `retry-after`/reset data when available; otherwise bounded exponential backoff with jitter.

Never retry indefinitely.

---

# 27. Publication Verification

A successful HTTP response is not always enough.

When supported:

```text
create
 ↓
obtain platform ID
 ↓
query/verify
 ↓
store external ID + URL
```

Generate/store external URLs from platform responses when possible rather than guessing.

---

# 28. Worker Crash Recovery

A watchdog should detect stale `PUBLISHING` operations.

Recovery:

```text
verify platform
 ↓
if published → mark PUBLISHED
if not published and safe → retry
if uncertain → BLOCKED / human intervention
```

---

# 29. Corrections and Updates

If facts materially change:

```text
new evidence
 ↓
new Fact Sheet version
 ↓
new content version
 ↓
human review
 ↓
platform update/correction
```

Do not silently rewrite the historical evidence/publication trail.

If a platform cannot edit the original item, publish a correction/update rather than pretending the original changed.

---

# 30. Deletion

Deletion may be used for material factual error, legal requirement, platform violation, privacy/security issue, or duplicate content.

Deletion itself must be audited and must not erase internal publication history.

---

# 31. Security and Authorization

Enforce least privilege, token isolation, HTTPS, audit logging, account-level authorization, and role-based access.

Publishing permission should be separate from ordinary content-edit permission.

Manual overrides such as approve, reject, pause, cancel, retry, reschedule, or account changes must be audited.

---

# 32. Emergency Stops

Support:

```text
global publishing pause
platform-level pause
account-level pause
```

A publishing pause should stop new external side effects while allowing collection, research, and content preparation to continue where safe.

---

# 33. Platform Configuration

Keep mutable platform constraints under:

```text
config/platforms/
├── instagram.yaml
├── x.yaml
├── facebook.yaml
└── telegram.yaml
```

Do not scatter remembered limits/API versions through business logic.

---

# 34. Manual and Scheduled Publishing

Manual “Publish Now” and scheduled publication use the same validation, approval, idempotency, adapter, and audit pipeline.

Manual execution must not bypass safeguards.

---

# 35. Breaking News

Breaking news may receive urgent review, fast-track research, and immediate execution after approval, but it must not bypass evidence requirements or the MVP human-approval gate.

---

# 36. Multi-Platform Independence

One story may produce independent publications per platform/account.

Example:

```text
Instagram → PUBLISHED
X         → PUBLISHED
Telegram  → FAILED
```

A failure on one platform must not mark successful publications on other platforms as failed.

---

# 37. Mock / Sandbox / Live Modes

Development must support:

```text
SOCIAL_MODE=MOCK
```

Mock mode never contacts real platforms.

Where supported, controlled integration may use sandbox/test modes.

Live mode requires explicit production environment, credentials, publishing enabled, account active, quality gates passed, and human approval under MVP policy.

---

# 38. Testing

Every adapter requires unit, contract, mock API, failure, retry, idempotency, and controlled integration tests.

Tests must cover timeouts, rate limits, authentication/permission failures, invalid media, duplicate requests, worker crashes, scheduler duplication, and partial thread publication.

Automated tests must never accidentally publish real content.

---

# 39. Observability

Track:

```text
publication_attempts_total
publication_success_total
publication_failure_total
publication_retry_total
publication_latency
platform_api_latency
rate_limit_events
auth_failures
media_failures
duplicate_prevented_total
```

Health reporting should include publisher worker, queue/database connectivity, media delivery/storage, adapter availability, and credential status without exposing secrets.

---

# 40. Audit Logging

Audit at least:

```text
content approval
publication scheduling
publication start
publication success/failure
retry
credential error
manual override
correction
deletion
```

Never log secrets.

---

# 41. Final Publishing Pipeline

MVP:

```text
VERIFICATION COMPLETED STORY
      ↓
FACT SHEET
      ↓
CONTENT VARIANT
      ↓
QUALITY GATE
      ↓
EXPLICIT HUMAN APPROVAL
      ↓
PUBLICATION RECORD
      ↓
SCHEDULER / REDIS
      ↓
PUBLISHER WORKER
      ↓
RENDERER
      ↓
MEDIA PREPARATION
      ↓
PLATFORM ADAPTER
      ↓
API
      ↓
VERIFY
      ↓
PUBLISHED
      ↓
ANALYTICS
```

---

# 42. Final Rules

```text
Never publish externally without explicit human approval in the MVP.
Never allow mandatory-review content to use future low-risk auto-approval.
Never store or log social credentials in plaintext.
Never blindly retry ambiguous publish outcomes.
Never create duplicate publications because of worker retries.
Never let one platform failure corrupt other platform states.
Never let platform-specific code leak into editorial/evidence logic.
Never let the publisher change factual claims.
Never assume platform limits remain unchanged.
Never delete publication history.
Always preserve platform IDs and attempt history.
Always support emergency publication pause.
Always verify publication when practical.
Always audit manual overrides.
```

---

# 43. Source of Truth

This document is authoritative for social platform architecture, adapters, renderers, publication execution, scheduling, retry behavior, idempotency, media preparation/delivery, account management, credential handling, platform failures, verification, and publishing observability.

Shared state semantics and MVP approval policy are authoritative in `CANONICAL_CONTRACTS.md` and are used here without alternate definitions.