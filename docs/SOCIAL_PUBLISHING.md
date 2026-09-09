# News AI Social Media Manager

# SOCIAL_PUBLISHING.md

**Status:** Canonical
**Document Role:** Source of truth for social-platform adapters, media preparation, publishing, scheduling, retries, account credentials, publication state, and platform-specific constraints.

---

# 1. Purpose

This document defines how verified content moves from the content engine to social platforms.

It covers:

* social account management
* platform adapters
* media preparation
* public media hosting
* platform authentication
* publication state
* scheduling
* retries
* rate limits
* idempotency
* publication verification
* platform errors
* corrections
* analytics handoff

It does not define:

* news collection
* evidence evaluation
* editorial scoring
* AI model architecture
* database infrastructure

Those remain defined by:

```text
ARCHITECTURE.md
DATA_MODEL.md
EVENTS.md
AI_PLATFORM.md
CONTENT_AND_EDITORIAL.md
```

---

# 2. Publishing Principle

The social layer must never become the source of factual truth.

The canonical flow is:

```text
FACTS
  ↓
FACT SHEET
  ↓
CONTENT
  ↓
QUALITY GATE
  ↓
HUMAN APPROVAL
  ↓
SOCIAL PUBLISHER
```

The publisher transports approved content.

It must not rewrite factual claims.

---

# 3. Supported Platforms

Initial platform set:

```text
INSTAGRAM
X
FACEBOOK
TELEGRAM
```

Future platforms must use the same adapter architecture.

Potential future adapters:

```text
YouTube
LinkedIn
Threads
WhatsApp Channels
Bluesky
Mastodon
```

They must not require changes to the core editorial engine.

---

# 4. Platform Adapter Architecture

Canonical interface:

```text
Content
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

Each platform implements a common contract.

Conceptually:

```python id="qkz9na"
class SocialPlatformAdapter:
    def validate_content(...)
    def prepare_media(...)
    def create_publication(...)
    def publish(...)
    def verify_publication(...)
    def delete_or_archive(...)
```

Exact implementation language and signatures remain implementation details.

---

# 5. Platform Independence

Core application code must never contain:

```text
if instagram:
    ...
elif twitter:
    ...
```

inside business logic.

Instead:

```text
SocialPublisher
      ↓
AdapterRegistry
      ↓
InstagramAdapter
XAdapter
FacebookAdapter
TelegramAdapter
```

---

# 6. Adapter Responsibilities

Each adapter owns:

* authentication mechanics
* platform API calls
* media upload requirements
* platform validation
* platform-specific formatting
* rate limits
* retryable errors
* permanent errors
* publication verification
* platform IDs
* platform-specific metadata

The adapter must not own editorial decisions.

---

# 7. Content Contract

The content engine produces a platform-neutral content object.

Example:

```json id="q7x4re"
{
  "story_id": "story_123",
  "fact_sheet_version": 4,
  "content_type": "news",
  "headline": "...",
  "body": "...",
  "claims": [],
  "sources": [],
  "media_assets": [],
  "platform": "instagram"
}
```

The final platform renderer converts this into the platform-specific payload.

---

# 8. Content Variants

One story may have:

```text
content_variants
├── instagram_carousel
├── instagram_caption
├── x_post
├── x_thread
├── facebook_post
├── telegram_post
└── youtube_short_script
```

Each variant has its own:

```text
variant_id
platform
format
content
media
status
quality_check
approval
```

---

# 9. Publication State Machine

Canonical publication states:

```text id="f9t4yk"
DRAFT
  ↓
READY_FOR_REVIEW
  ↓
APPROVED
  ↓
SCHEDULED
  ↓
PUBLISHING
  ↓
PUBLISHED
```

Failure branches:

```text id="7q3gdu"
PUBLISHING
    ├── RETRYING
    ├── FAILED
    └── BLOCKED
```

Post-publication:

```text id="d1g6xw"
PUBLISHED
    ├── UPDATED
    ├── CORRECTED
    └── ARCHIVED
```

---

# 10. Publication State Rules

A publication must never transition directly from:

```text
DRAFT
```

to:

```text
PUBLISHED
```

unless an explicitly configured low-risk automation policy permits it.

Sensitive content requires human approval.

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
platform_post_id
platform_url
error_code
error_message
attempt_count
created_at
updated_at
```

---

# 12. Publication Attempt

Each attempt should be separately recorded.

```text
publication_attempts
├── attempt_id
├── publication_id
├── attempt_number
├── started_at
├── completed_at
├── status
├── platform_response
├── error_code
└── error_class
```

This prevents retry history from being lost.

---

# 13. Idempotency

Publishing must be idempotent.

The system must avoid duplicate posts caused by:

* worker restart
* network timeout
* API timeout
* process crash
* queue redelivery
* duplicate scheduler execution

Canonical idempotency key:

```text
story_id
+
content_variant_id
+
social_account_id
```

A publication should have only one active publishing operation for a given idempotency key.

---

# 14. Duplicate Protection

Before publishing:

```text
check publication record
        ↓
already published?
    YES → verify existing publication
    NO  → publish
```

If the API response is ambiguous:

```text
timeout
 ↓
DO NOT immediately retry blindly
 ↓
query platform
 ↓
determine whether post exists
```

---

# 15. Scheduling

Scheduler responsibilities:

```text
find SCHEDULED publications
        ↓
check eligibility
        ↓
enqueue publication job
        ↓
publisher executes
        ↓
record result
```

The scheduler should not directly perform platform API calls.

---

# 16. Scheduling Eligibility

Before a scheduled publication executes:

```text
content approved
account active
credentials valid
media available
media publicly accessible where required
platform constraints satisfied
publication not already completed
risk policy satisfied
```

If any condition fails:

```text
BLOCKED
```

or:

```text
RETRYING
```

depending on the cause.

---

# 17. Time Zones

Internally:

```text
UTC
```

should be used for timestamps.

Editorial scheduling may use:

```text
Asia/Kolkata
```

or another configured audience timezone.

Every scheduled publication must preserve the intended timezone explicitly.

---

# 18. Instagram Adapter

Instagram publishing is implemented through the Meta/Instagram platform APIs appropriate to the connected professional account.

The adapter must support the initial required formats:

```text
IMAGE
VIDEO
REEL
CAROUSEL
```

The exact API permissions, account eligibility, token requirements, and platform constraints must be validated against current platform documentation during implementation.

---

# 19. Instagram Publishing Flow

Canonical flow:

```text
Instagram Content
       ↓
Instagram Renderer
       ↓
Media Validation
       ↓
Public Media Hosting
       ↓
Media Container Creation
       ↓
Publish Container
       ↓
Verify
       ↓
Store Instagram Media ID
```

---

# 20. Instagram Media Requirements

Before publication validate:

```text
media type
file format
dimensions
aspect ratio
file size
duration
codec
accessibility
```

Platform-specific limits should be configuration-driven.

Do not hard-code temporary platform limits throughout the application.

---

# 21. Instagram Carousel

Carousel flow:

```text
Fact Sheet
 ↓
Slide Content
 ↓
Image Assets
 ↓
Validate Every Asset
 ↓
Create Child Media Containers
 ↓
Create Carousel Container
 ↓
Publish
 ↓
Verify
```

A carousel is treated as one logical publication even though it contains multiple media items.

---

# 22. Instagram Caption

Caption generation belongs to the content engine.

The adapter performs:

```text
length validation
character validation
hashtag validation
mention validation
platform formatting
```

It must not rewrite factual claims.

---

# 23. Instagram Media Hosting

If the platform requires publicly accessible media:

```text
Generated Asset
      ↓
Object Storage / Media Server
      ↓
Public HTTPS URL
      ↓
Instagram API
```

The URL must be:

```text
HTTPS
reachable
stable
correct MIME type
available during ingestion
```

Private local filesystem paths must never be sent to a platform API.

---

# 24. Media Lifecycle

Media should have explicit states:

```text
GENERATED
VALIDATED
UPLOADED
PUBLICLY_AVAILABLE
USED
ARCHIVED
DELETED
```

Do not delete an asset while a pending publication still depends on it.

---

# 25. X Adapter

The X adapter supports:

```text
single post
thread
reply
quote post
media attachment
```

Canonical flow:

```text
X Content
 ↓
X Renderer
 ↓
Text Validation
 ↓
Media Preparation
 ↓
Media Upload
 ↓
Create Post
 ↓
Verify
 ↓
Store Post ID
```

---

# 26. X Single Post

A single post should contain:

```text
hook
fact
context
source / attribution where appropriate
```

The renderer must validate current platform length rules.

Do not assume that all characters consume length identically.

Use platform-aware validation.

---

# 27. X Threads

Thread creation:

```text
Thread
 ↓
Validate each post
 ↓
Publish post 1
 ↓
Store ID
 ↓
Publish post 2 as reply
 ↓
Store ID
 ↓
Continue
```

If a later post fails:

```text
thread status = PARTIAL
```

The system must preserve which posts succeeded.

---

# 28. X Thread Recovery

Never blindly restart a partially published thread.

Recovery should:

```text
query publication state
 ↓
identify last successful post
 ↓
resume from next post
```

If safe resumption is impossible:

```text
BLOCK
```

and require human intervention.

---

# 29. Facebook Adapter

Facebook should use the appropriate Meta API for the connected publishing destination.

Supported initial content:

```text
text
image
video
link where supported
```

Adapter flow:

```text
Facebook Content
 ↓
Renderer
 ↓
Media Validation
 ↓
Upload / Prepare
 ↓
Publish
 ↓
Verify
 ↓
Store Platform ID
```

---

# 30. Telegram Adapter

Telegram should support:

```text
channel posts
text
image
video
album/media group where supported
```

Canonical flow:

```text
Telegram Content
 ↓
Renderer
 ↓
Media Preparation
 ↓
Bot / API
 ↓
Publish
 ↓
Verify
 ↓
Store Message ID
```

---

# 31. Telegram Channel Safety

The connected bot/account must have only the permissions necessary for publication.

The system should verify:

```text
target channel
bot membership
posting permission
credential validity
```

before scheduling publication.

---

# 32. Account Model

Canonical:

```text
social_accounts
├── account_id
├── platform
├── account_name
├── account_identifier
├── status
├── credential_reference
├── capabilities
├── rate_limit_state
├── metadata
├── created_at
└── updated_at
```

Credentials must never be stored as plaintext in ordinary application tables.

---

# 33. Credential Storage

Social credentials should be stored using:

```text
environment secrets
encrypted credential storage
secret manager
```

depending on deployment maturity.

Never:

```text
Git
logs
AI prompts
frontend state
ordinary plaintext database fields
```

---

# 34. Token Rotation

The system must support:

```text
token expiration
token refresh
token replacement
credential revocation
credential validation
```

A failed credential should transition the account to an appropriate state.

Example:

```text
ACTIVE
 ↓
AUTH_ERROR
 ↓
REAUTH_REQUIRED
```

---

# 35. Account States

Recommended:

```text
ACTIVE
PAUSED
AUTH_ERROR
RATE_LIMITED
DISABLED
REAUTH_REQUIRED
```

A paused account must not receive new publication jobs.

---

# 36. Capability Discovery

Each adapter should expose capabilities.

Example:

```json id="6a9qk5"
{
  "text": true,
  "image": true,
  "video": true,
  "carousel": true,
  "thread": false,
  "scheduled_publish": false
}
```

Capabilities may differ by account type.

The system must query or configure capabilities rather than assuming universal support.

---

# 37. Platform Validation

Before scheduling:

```text
Content
 ↓
Platform Capability Check
 ↓
Format Validation
 ↓
Media Validation
 ↓
Account Validation
```

Invalid content should fail before entering the publishing queue.

---

# 38. Rate Limiting

Rate limits should be handled at three levels:

```text
platform
account
application
```

Use:

```text
rate_limit_state
next_allowed_at
remaining
reset_at
```

where the platform exposes such information.

---

# 39. Rate-Limit Handling

If rate limited:

```text
API response
 ↓
identify retry-after/reset
 ↓
schedule retry
 ↓
do not hammer API
```

Exponential backoff should be used where no explicit retry time is supplied.

---

# 40. Retry Classes

Errors should be classified as:

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

Only appropriate classes should automatically retry.

---

# 41. Retry Policy

Recommended:

```text
TRANSIENT
→ retry

RATE_LIMIT
→ wait according to reset/retry-after

AUTHENTICATION
→ stop and request reauthentication

VALIDATION
→ do not retry

PERMISSION
→ stop

MEDIA
→ repair/reprocess, then retry

DUPLICATE
→ verify existing publication

UNKNOWN
→ limited retry then human review
```

---

# 42. Exponential Backoff

Example:

```text
attempt 1 → 30 sec
attempt 2 → 2 min
attempt 3 → 10 min
attempt 4 → 30 min
```

Exact intervals are configurable.

Avoid infinite retries.

---

# 43. Dead-Letter Handling

After retry exhaustion:

```text
FAILED
 ↓
DEAD_LETTER
```

The system should preserve:

```text
publication
attempt history
last error
platform response
```

A human can then retry manually.

---

# 44. Publication Verification

A successful HTTP/API response is not always enough.

After publishing:

```text
create
 ↓
obtain platform ID
 ↓
query/verify when supported
 ↓
store canonical platform ID
```

Publication is considered complete only when the adapter has sufficient confirmation.

---

# 45. Platform URL

When available, store:

```text
platform_post_id
platform_url
```

The URL should be generated from the platform response rather than guessed.

---

# 46. Publication Events

The publisher integrates with the event system.

Relevant events:

```text
publication.scheduled
publication.started
publication.executed
publication.failed
publication.retrying
publication.published
publication.corrected
```

Exact event schemas are defined in `EVENTS.md`.

---

# 47. Queue Architecture

Publishing jobs should execute asynchronously.

```text
Scheduler
   ↓
Redis Stream
   ↓
Publisher Worker
   ↓
Platform Adapter
```

The API should not wait for a social platform to finish publishing.

---

# 48. Worker Concurrency

Concurrency must be controlled by:

```text
platform
account
publication type
rate limits
worker capacity
```

Example:

```text
Instagram account A → max 1 active publish
X account A → configurable
Telegram → configurable
```

Do not allow uncontrolled parallel publishing.

---

# 49. Locking

Use a distributed lock or equivalent mechanism around:

```text
publication_id
```

to prevent two workers from publishing the same item simultaneously.

---

# 50. Worker Crash Recovery

If a worker crashes:

```text
PUBLISHING
```

must not remain permanently stuck.

A watchdog should identify stale operations.

Then:

```text
verify platform
 ↓
if published → mark PUBLISHED
if not published → retry safely
if uncertain → human review
```

---

# 51. Scheduling Conflicts

The system must detect:

```text
duplicate scheduled content
same story repeated
same platform/account collision
campaign conflict
manual publication already completed
```

Before publishing.

---

# 52. Content Frequency

Posting frequency should be configurable.

Example:

```yaml id="7y5d01"
instagram:
  max_posts_per_day: configurable

x:
  max_posts_per_day: configurable

telegram:
  max_posts_per_day: configurable
```

These are editorial controls, not universal platform limits.

---

# 53. Platform Limits

Platform limits change.

Therefore:

```text
platform constraints
```

must be represented in:

```text
config/platforms/
```

rather than duplicated throughout application code.

Recommended:

```text
config/platforms/
├── instagram.yaml
├── x.yaml
├── facebook.yaml
└── telegram.yaml
```

---

# 54. Platform Configuration

Example:

```yaml id="g3u4i6"
platform: instagram

formats:
  image:
    enabled: true
  carousel:
    enabled: true
  reel:
    enabled: true

validation:
  max_caption_length: provider_defined
  supported_media: provider_defined
```

Values that are provider-dependent should be populated from current platform documentation or capability discovery.

---

# 55. Manual Publishing

The system should support manual publication.

Example:

```text
Approved Content
 ↓
[Publish Now]
```

The same publication pipeline must be used.

Manual publication must not bypass:

```text
validation
account checks
audit logging
duplicate protection
```

---

# 56. Scheduled Publishing

UI:

```text
CONTENT
Status: APPROVED

Platform:
☑ Instagram
☑ X
☑ Telegram

Schedule:
2026-09-10 19:30 IST

[Schedule]
```

The UI creates publication records.

The scheduler performs execution.

---

# 57. Approval Expiration

For sensitive stories, approval may expire if:

```text
new evidence appears
story materially changes
publication is delayed significantly
source status changes
```

The system should support:

```text
APPROVAL_INVALIDATED
```

which returns the content to review.

---

# 58. Breaking News

Breaking-news publication should support:

```text
urgent review
fast-track research
rapid approval
immediate publication
post-publication update
```

Fast publication must not eliminate evidence requirements.

---

# 59. Post-Publication Updates

If facts change:

```text
New Evidence
 ↓
Fact Sheet Revision
 ↓
Content Revision
 ↓
Review
 ↓
Platform Update / Correction
```

Whether a platform post can be edited depends on platform capabilities.

If editing is unavailable:

```text
publish correction/update
```

rather than pretending the original post changed.

---

# 60. Corrections

A correction must preserve:

```text
original publication
error
correct information
correction timestamp
reviewer
updated source
```

Never erase the audit trail.

---

# 61. Deletion

Deletion should be used only when appropriate.

Possible reasons:

```text
material factual error
legal requirement
platform violation
privacy issue
duplicate
security issue
```

Deletion itself must be audited.

---

# 62. Publication Analytics

Publisher records should feed analytics.

Minimum metrics:

```text
published
failed
retried
blocked
corrected
deleted
```

Platform analytics are handled by the analytics subsystem.

---

# 63. Analytics Relationship

Publishing:

```text
publication
 ↓
platform_post_id
 ↓
analytics collector
```

Analytics should never be required to determine whether publication succeeded.

---

# 64. Audit Logging

Every significant publishing action should be logged:

```text
content approved
publication scheduled
publication started
API request initiated
publication succeeded
publication failed
retry initiated
credential error
manual override
correction
deletion
```

Sensitive credentials must never enter the audit log.

---

# 65. Security

The publishing subsystem must enforce:

```text
least privilege
encrypted credentials
token isolation
HTTPS
audit logging
account-level authorization
role-based access
```

---

# 66. Secrets and Logs

Never log:

```text
access tokens
refresh tokens
API secrets
cookies
authorization headers
private media URLs containing credentials
```

Safe logging:

```text
platform=instagram
account_id=123
publication_id=456
status=FAILED
error_code=RATE_LIMIT
```

---

# 67. Admin Permissions

Recommended roles:

```text
ADMIN
EDITOR
REVIEWER
PUBLISHER
ANALYST
```

Publishing permission should be separate from ordinary content-edit permission.

---

# 68. Human Override

Authorized users may:

```text
pause publication
cancel publication
retry
reschedule
change account
approve
reject
```

Every override must be audited.

---

# 69. Emergency Stop

The system must support a global publishing kill switch.

```text
PUBLISHING
    ↓
GLOBAL PAUSE
    ↓
No new publication calls
```

Existing API calls may complete depending on implementation.

The switch should be available to authorized administrators.

---

# 70. Platform-Level Pause

Also support:

```text
pause Instagram
pause X
pause Facebook
pause Telegram
```

without stopping the rest of the system.

---

# 71. Account-Level Pause

Example:

```text
Instagram Account A → PAUSED
Instagram Account B → ACTIVE
```

Only account A publication jobs should stop.

---

# 72. Media Storage

Media assets should use object storage or an equivalent dedicated media layer.

Recommended abstraction:

```text
MediaStorage
├── upload
├── get_public_url
├── exists
├── delete
└── metadata
```

The publishing layer must not depend on local filesystem paths.

---

# 73. Media Validation

Before upload:

```text
file exists
MIME type correct
extension correct
dimensions valid
duration valid
size valid
content not corrupted
```

---

# 74. Media Hashing

Compute:

```text
SHA-256
```

or equivalent content hash.

Use it to detect:

```text
duplicate assets
duplicate uploads
corrupted replacements
```

---

# 75. Content Hashing

Generate a normalized content hash.

Example:

```text
story_id
platform
content_variant
normalized_content
media_hashes
```

This assists duplicate detection.

---

# 76. Platform Adapter Registry

Conceptual:

```python id="8c6b1f"
registry = {
    "instagram": InstagramAdapter,
    "x": XAdapter,
    "facebook": FacebookAdapter,
    "telegram": TelegramAdapter,
}
```

The actual implementation should use dependency injection rather than hard-coded global state where practical.

---

# 77. Publishing Service

Conceptual:

```text
PublishingService
├── validate()
├── authorize()
├── prepare()
├── schedule()
├── execute()
├── verify()
├── retry()
└── record()
```

The service coordinates.

Adapters communicate with platforms.

---

# 78. Renderer vs Adapter

Important separation:

```text
Renderer
→ decides platform presentation

Adapter
→ communicates with platform API
```

For example:

```text
InstagramRenderer
InstagramAdapter
```

must remain separate.

---

# 79. Example Instagram Flow

```text
Story
 ↓
Fact Sheet
 ↓
Instagram Carousel Content
 ↓
Human Approval
 ↓
InstagramRenderer
 ↓
Validate
 ↓
Upload Media
 ↓
Create Container
 ↓
Publish
 ↓
Verify
 ↓
publication = PUBLISHED
```

---

# 80. Example X Flow

```text
Story
 ↓
Fact Sheet
 ↓
X Thread
 ↓
Human Approval
 ↓
XRenderer
 ↓
Validate
 ↓
Upload Media
 ↓
Create Post 1
 ↓
Create Reply 2
 ↓
Create Reply 3
 ↓
Verify
 ↓
publication = PUBLISHED
```

---

# 81. Example Telegram Flow

```text
Story
 ↓
Fact Sheet
 ↓
Telegram Post
 ↓
Approval
 ↓
TelegramRenderer
 ↓
Bot API
 ↓
Verify
 ↓
Store Message ID
```

---

# 82. Example Multi-Platform Publication

One story:

```text
story_123
```

can produce:

```text
publication_1 → Instagram
publication_2 → X
publication_3 → Telegram
```

Each publication has independent state.

Instagram failure must not mark X as failed.

---

# 83. Partial Success

Example:

```text
Instagram → PUBLISHED
X → PUBLISHED
Telegram → FAILED
```

Overall campaign state:

```text
PARTIALLY_PUBLISHED
```

The failed platform can be retried independently.

---

# 84. Campaign Model

Optional higher-level grouping:

```text
PublicationCampaign
├── story_id
├── content_variants
├── publications[]
└── campaign_status
```

This is useful for multi-platform publishing.

---

# 85. Campaign State

Recommended:

```text
DRAFT
READY
PARTIALLY_PUBLISHED
PUBLISHED
FAILED
CANCELLED
```

Campaign state is derived from child publications.

---

# 86. Platform API Changes

External APIs change.

Therefore platform integrations must be isolated.

When an API changes:

```text
Platform Adapter
```

should absorb most changes without requiring modifications to:

```text
Fact Engine
Editorial Engine
AI Engine
Content Engine
```

---

# 87. API Versioning

Where platform APIs expose versions:

```text
platform API version
```

must be explicit in configuration.

Example:

```yaml id="n6e3z8"
platform: instagram
api_version: configurable
```

Avoid scattering API version strings through code.

---

# 88. API Documentation

Platform-specific implementation must be validated against current official documentation before production deployment.

Do not rely on old remembered limits or permissions.

---

# 89. Testing Strategy

Every adapter requires:

```text
unit tests
contract tests
mock API tests
error tests
retry tests
idempotency tests
integration tests
```

---

# 90. Adapter Contract Tests

Every adapter should pass common tests:

```text
validate valid content
reject invalid content
prepare media
publish
handle timeout
handle rate limit
handle auth failure
handle duplicate
verify publication
record platform ID
```

---

# 91. Mock Mode

Development must support:

```text
SOCIAL_MODE=MOCK
```

Mock mode should:

* validate payloads
* simulate API responses
* create fake platform IDs
* test retries
* test state transitions

It must never contact real platforms.

---

# 92. Local Integration Mode

Recommended:

```text
SOCIAL_MODE=SANDBOX
```

where supported.

Use real credentials only for controlled integration tests.

---

# 93. Production Mode

```text
SOCIAL_MODE=LIVE
```

must require:

```text
production credentials
explicit environment
publishing enabled
account active
quality gates passed
```

---

# 94. Test Safety

Automated tests must not accidentally publish real content.

Production credentials must never be available to ordinary test jobs.

---

# 95. Failure Scenarios

The system must test:

```text
network timeout
DNS failure
HTTP 429
HTTP 401
HTTP 403
HTTP 400
HTTP 404
platform outage
media URL unavailable
expired token
duplicate request
worker crash
scheduler duplication
partial thread publication
```

---

# 96. Observability

Metrics:

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

---

# 97. Alerts

Alert on:

```text
high failure rate
repeated authentication failures
persistent rate limiting
publisher queue growth
stuck PUBLISHING jobs
platform outage
global publishing failure
```

---

# 98. Health Check

Publisher health should report:

```text
publisher worker
queue connectivity
database connectivity
media storage
platform adapter availability
credential status
```

It should not expose credentials.

---

# 99. Server Health Integration

The broader server health monitor should eventually show:

```text
SOCIAL
├── Instagram
├── X
├── Facebook
└── Telegram

PUBLISHER
├── queue depth
├── running jobs
├── failed jobs
├── retrying jobs
└── stuck jobs
```

This integrates with the planned whole-server health command.

---

# 100. Operational Dashboard

Recommended dashboard:

```text
PUBLISHING

Queue
├── Pending: 8
├── Running: 2
├── Retrying: 1
├── Failed: 0
└── Scheduled: 14

Platforms
├── Instagram: Healthy
├── X: Healthy
├── Facebook: Healthy
└── Telegram: Healthy
```

---

# 101. Publication Detail

A publication detail screen should show:

```text
Story
Content Variant
Platform
Account
Approval
Scheduled Time
Attempts
Current Status
Platform ID
Platform URL
Errors
Audit History
```

---

# 102. Final Publishing Pipeline

Canonical production flow:

```text
VERIFIED STORY
      ↓
FACT SHEET
      ↓
CONTENT VARIANT
      ↓
QUALITY GATE
      ↓
HUMAN APPROVAL IF REQUIRED
      ↓
PUBLICATION RECORD
      ↓
SCHEDULER
      ↓
REDIS STREAM
      ↓
PUBLISHER WORKER
      ↓
PLATFORM RENDERER
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

# 103. Final Publishing Rules

```text
Never publish unapproved high-risk content.

Never store social credentials in plaintext.

Never put credentials in logs.

Never blindly retry an ambiguous API timeout.

Never create duplicate publications because of worker retries.

Never let one platform failure mark other platforms as failed.

Never allow platform-specific code to leak into the editorial engine.

Never let the publisher change factual claims.

Never assume platform limits remain unchanged.

Never delete publication history.

Always preserve platform IDs.

Always preserve publication attempts.

Always support an emergency publishing pause.

Always verify publication when practical.

Always audit manual overrides.
```

---

# 104. Source of Truth

This document is authoritative for:

```text
social platform architecture
platform adapters
renderers
publication state
scheduling
retry behavior
idempotency
media preparation
account management
credential handling
platform failures
publication verification
publishing observability
```

Conflicts with platform-specific implementation must be resolved here and in the relevant adapter configuration.

---

# 105. Final Architecture Relationship

The complete publishing boundary is:

```text
                 ┌──────────────────┐
                 │    FACT SHEET    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ CONTENT ENGINE   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │  QUALITY GATE    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ HUMAN APPROVAL   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ PUBLISHING CORE  │
                 └────────┬─────────┘
                          ↓
              ┌───────────┼───────────┐
              ↓           ↓           ↓
        Instagram         X       Telegram
              ↓           ↓           ↓
           Adapter      Adapter     Adapter
              ↓           ↓           ↓
           Platform APIs / Bots
              ↓
         Publication Record
              ↓
           Analytics
```

The publishing subsystem is therefore a **transport and execution boundary**, not an editorial decision-maker.
