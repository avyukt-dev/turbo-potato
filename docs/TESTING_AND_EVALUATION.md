# News AI Social Media Manager

Certainty regressions cover the closed status/label ceiling matrix, exact source
copies, per-claim coverage, multi-claim isolation, generation invalid-response
fallback, persisted quality revalidation, deterministic ERROR overriding AI pass,
typed AI prose escalation persistence/review visibility, and methodology/policy
identity invalidation. Historical artifacts are not retroactively validated:
missing presentations require regeneration, and legacy prose-check NULL remains
NULL. PostgreSQL verifies `0014 -> 0013 -> 0014` exact schema and honest backfill.
Exhaustive status/label coverage requires exactly the five current producer pairs
to succeed under `certainty-policy-v1`: SUPPORTED/TRUE, PARTIALLY_SUPPORTED/PARTIALLY_TRUE,
DISPUTED/UNVERIFIED, UNVERIFIED/UNVERIFIED, REFUTED/FALSE. Every unlisted pair must fail
closed, including unused canonical enum labels. Strength ordering is explicit and immutable.

Deterministic quality semantic regressions cover scoped quotes and mechanical
normalization, reference ownership, duplicate IDs, relation-role consistency,
explicit temporal ranges, warning-only reports, and unknown optional metadata.
AI-pass/semantic-error tests must prove NOT_READY state and durable typed reports;
exact replay and methodology invalidation must preserve idempotency. PostgreSQL
tests cover report persistence and `0013 -> 0012 -> 0013` migration parity, including
honest NULL semantics for legacy checks. Unrepresented dependencies, timeline
ordering, prose certainty, and generalized numeric/date interpretation are not
guessed by this validator.

# TESTING_AND_EVALUATION.md

**Status:** Canonical
**Document Role:** Source of truth for test strategy, evidence/AI/editorial evaluation, runtime portability tests, release gates, regression detection, and rollback criteria.

Shared enums, configuration ownership, runtime semantics, and publication policy are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

Testing must validate more than software correctness.

The system handles breaking news, politics, geopolitics, war/security, religion, communal incidents, legal allegations, caste-related reporting, demographics, historical claims, fact checking, AI-generated content, and social publication.

Production readiness requires:

```text
CODE CORRECTNESS
        +
DATA CORRECTNESS
        +
EVENT CORRECTNESS
        +
EVIDENCE CORRECTNESS
        +
AI QUALITY
        +
EDITORIAL SAFETY
        +
RUNTIME PORTABILITY
        +
PUBLISHING SAFETY
        =
PRODUCTION READINESS
```

---

# 2. Test Categories

```text
UNIT
INTEGRATION
CONTRACT
COMPONENT
END_TO_END
AI_EVALUATION
EVIDENCE_EVALUATION
EDITORIAL_EVALUATION
SECURITY
PERFORMANCE
FAILURE_RECOVERY
INFRASTRUCTURE
RUNTIME_PORTABILITY
SOCIAL_PLATFORM
REGRESSION
```

No single category is sufficient.

---

# 3. Test Repository Structure

Recommended:

```text
tests/
├── unit/
│   ├── domain/
│   ├── evidence/
│   ├── editorial/
│   ├── content/
│   ├── ai/
│   ├── runtime/
│   └── common/
├── integration/
│   ├── database/
│   ├── redis/
│   ├── collector/
│   ├── research/
│   ├── ai/
│   ├── runtime/
│   └── publishing/
├── contract/
│   ├── schemas/
│   ├── events/
│   ├── ai/
│   ├── search/
│   ├── runtime/
│   └── social/
├── component/
├── e2e/
├── ai_eval/
├── evidence_eval/
├── editorial_eval/
├── runtime_eval/
├── security/
├── performance/
├── failure/
├── fixtures/
└── helpers/
```

---

# 4. Canonical Enum Contract Tests

The following must be tested as separate enums.

## ClaimVerificationStatus

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

## FactCheckLabel

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

Required negative tests:

```text
Claim.status = FALSE              → reject
Claim.status = PARTIALLY_TRUE     → reject
FactCheck.label = PARTIALLY_SUPPORTED → reject
legacy status partially_confirmed → reject
```

Critical invariants:

```text
UNVERIFIED != REFUTED
UNVERIFIED != FALSE
```

---

# 5. Database Tests

Validate:

```text
schema
migrations
constraints
indexes
foreign keys
uniqueness
transactions
Fact Sheet versioning
review state
publication state
job state
audit records
```

Important relationships:

```text
article → story
story → claim
claim → evidence
story/claim → fact_check
story → fact_sheet
fact_sheet → content
content → review
content → publication
publication → publication_attempt
job → job_attempt
```

PostgreSQL remains authoritative.

Tests must verify Redis loss does not erase durable business state.

Redis transport-loss acceptance coverage must also prove that ordinary dispatch does
not reclaim PUBLISHED history, while explicit bounded reconciliation preserves the
original event identity, restores missing canonical groups without resetting existing
offsets, runs the real consumer exactly once at the durable boundary, and then converges
through `ProcessedEvent` or current domain state. Mixed completed/incomplete histories
and Stage-26 publication ambiguity/external-ID fences require real PostgreSQL and Redis
coverage. Failure injection must cover PostgreSQL reads, publishing-control reads,
consumer-group creation, event deserialization, and Redis XADD without leaking raw
payloads or exceptions.

---

# 6. Event Contract Tests

Every event requires schema/version/serialization/idempotency tests.

Canonical end-to-end event sequence:

```text
article.discovered
    ↓
article.normalized
    ↓
story.created / story.clustered
    ↓
claims.extracted
    ↓
evidence.requested
    ↓
evidence.collected
    ↓
fact_check.completed
    ↓
story.verified
    ↓
content.requested
    ↓
content.generated
    ↓
content.quality_checked
    ↓
human approval persisted in PostgreSQL
    ↓
publication.scheduled
    ↓
publication.executed / publication.failed
    ↓
analytics.requested
    ↓
analytics.collected
```

`story.verified` must be tested as “verification stage completed,” not “all claims true.”

Consumers must tolerate duplicate delivery.

---

# 7. Transactional Outbox Tests

Test:

```text
business-state write succeeds + outbox write succeeds atomically
outbox publish retries safely
publish succeeds but outbox status update fails
consumer receives duplicate event
consumer idempotently no-ops when state already completed
```

Exactly-once delivery must not be assumed.

---

# 8. Collector Tests

Validate:

```text
RSS/Atom parsing
malformed source payloads
missing titles/dates
canonical URL
redirects
Unicode/encoding
source disablement
poll interval
HTTP failure
rate limiting
duplicate discovery
```

One broken source must not stop the entire collector.

Article acquisition tests must distinguish summary from explicit feed body, prove
safe bounded page retrieval and deterministic extraction, and reject unsafe URL,
redirect/DNS destinations, binary responses and unusable/oversized text. Real
PostgreSQL tests must prove no row locks survive acquisition, cancellation cannot
ACK or persist normalization, and final revalidation rejects changed discovery
input while concurrent completion creates one durable version/outbox intent.
The autonomous PostgreSQL/Redis pipeline test must demonstrate that summary-only
discovery acquires mocked full page content and supplies that exact durable body
to the actual claim-extraction AIRequest, including restart/duplicate recovery.

Legacy bodyless ArticleVersions remain immutable/auditable but must fail claim
extraction before AI or new Claim/outbox persistence. Operators must use controlled
re-ingestion of corrected source material, not mutate historical versions or reset
ProcessedEvent state to force replay.

Source collection settings must come from `config/sources/`, not editorial/research configuration.

---

# 9. Configuration Ownership Tests

Configuration validation must enforce one owner per concern.

## `config/sources/`

Expected concerns:

```text
source registry
feeds/endpoints
collection mechanics
polling/parser settings
technical credential references
```

## `config/research/`

Expected concerns:

```text
source evidence policy
search policy
corroboration
fact-check methodology
historical research methodology
research budgets/timeouts
```

## `config/editorial/`

Expected concerns:

```text
priorities
taxonomy
content style
risk routing
publishing policy
```

Tests should reject or warn on ambiguous duplicate policy keys across roots.

Example invalid state:

```text
config/editorial/source-policy.yaml
        +
config/research/source-policy.yaml
```

with overlapping evidence-policy semantics.

---

# 10. Story Clustering Tests

Cover:

```text
exact duplicates
near duplicates
syndicated copies
article updates
same entities but distinct events
same event across languages
```

Critical test:

```text
10 syndicated articles
    ↓
1 originating report
    ↓
NOT 10 independent confirmations
```

---

# 11. Claim Extraction Tests

Claim extraction must distinguish:

```text
FACTUAL CLAIM
OPINION
PREDICTION
ALLEGATION
QUOTE
ESTIMATE
CAUSAL CLAIM
UNVERIFIED ASSERTION
```

Examples that must not be collapsed:

```text
"X accused Y"  !=  "Y did X"
"Police alleged" != "Police proved"
"Preliminary reports say" != "Confirmed"
```

---

# 12. Evidence Engine Tests

For each material claim test:

```text
supporting evidence
contradicting evidence
qualifying evidence
context evidence
source role
source lineage
source independence
primary evidence
stale evidence
source correction
confidence calculation
```

Absence of evidence must not automatically become evidence of falsity.

---

# 13. Source Hierarchy Tests

Test the research hierarchy:

```text
LEVEL 1 primary
LEVEL 2 established secondary
LEVEL 3 specialist/research/investigative
LEVEL 4 discovery
```

A large number of Level 4 repeats must not automatically verify a high-risk claim.

Primary sources must not be treated as infallible merely because they are primary.

---

# 14. Source Independence Tests

Fixtures should include:

```text
wire story republished by many outlets
multiple outlets quoting one police statement
multiple independent eyewitnesses
multiple outlets using one research paper
independent primary documents
```

The independence scorer/grouping must reflect underlying evidence lineage.

---

# 15. Fact-Check Tests

Supported verdicts:

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

Test explicitly:

```text
insufficient reliable evidence → UNVERIFIED
```

must not become:

```text
FALSE
```

unless evidence establishes falsity.

---

# 16. Fact Sheet Grounding Tests

Every final factual statement should trace through:

```text
SOURCE
  ↓
EVIDENCE
  ↓
CLAIM
  ↓
FACT SHEET
  ↓
CONTENT STATEMENT
```

Flag:

```text
unsupported number
unsupported date
unsupported location
unsupported quote
unsupported motive
unsupported attribution
unsupported casualty count
unsupported demographic conclusion
```

---

# 17. Citation Tests

Test both:

```text
citation exists
```

and:

```text
cited source actually supports the claim
```

Citation presence without entailment fails.

---

# 18. Contradictory Evidence Tests

When credible evidence conflicts, the system must preserve the disagreement.

Test that editorial preference does not remove or down-rank contradiction solely because it weakens the preferred framing.

A contradiction does not automatically mean `REFUTED` or `FALSE`.

---

# 19. Editorial Evaluation Tests

Test:

```text
high editorial importance != high factual confidence
low editorial importance != low factual confidence
```

Example:

```text
importance = 0.95
evidence_strength = 0.45
```

must remain valid.

Editorial configuration must not mutate claim/evidence status.

---

# 20. Sensitive Topic Tests

At minimum test enhanced verification/review routing for:

```text
COMMUNAL_VIOLENCE
RELIGIOUS_ACCUSATION
CRIMINAL_ALLEGATION
SC_ST_ACT
SEXUAL_ASSAULT
TERRORISM
WAR_CASUALTIES
ELECTION_FRAUD
CASTE_RELATED
BLASPHEMY
UNVERIFIED_BREAKING_NEWS
```

These categories require human review even in any future low-risk automation system.

---

# 21. MVP Publication Approval Tests

For the MVP, every external publication requires explicit human approval.

Required rejection tests:

```text
DRAFT → publish
QUALITY_CHECKED → publish
READY_FOR_REVIEW → publish
LOW risk without human approval → publish
```

Required valid sequence:

```text
quality check passed
    ↓
READY_FOR_REVIEW
    ↓
human APPROVED exact version
    ↓
SCHEDULED
    ↓
PUBLISHING
```

Material edits after approval must invalidate/re-evaluate approval according to policy.

---

# 22. Legal/Allegation Tests

Preserve:

```text
allegation
complaint
FIR
investigation
arrest
charge
prosecution
trial
court finding
conviction
acquittal
appeal
final judgment
```

Tests must fail if one state is rewritten as another without evidence.

---

# 23. Caste/SC-ST Tests

Test that the system:

```text
reports verified case-specific facts
preserves legal status
never infers caste where not established
never infers criminal tendency/behavior from caste
never generalizes one case to a community
```

---

# 24. Demographic Tests

Validate separation of:

```text
observed data
statistical interpretation
possible explanations
causal evidence
editorial interpretation
```

Reject unsupported causal or communal conclusions from population changes alone.

---

# 25. Historical Research Tests

Historical evaluation must preserve separate domains:

```text
ARCHAEOLOGY
EPIGRAPHY
LITERARY SOURCES
LINGUISTICS
GENETICS
CHRONOLOGY
MATERIAL CULTURE
POPULATION MOVEMENT
MODERN SCHOLARSHIP
```

Test that evidence in one domain is not silently converted into proof in another.

For Indo-European/Indo-Aryan questions, language dispersal, population movement, genetic ancestry, cultural transmission, archaeology, and military invasion must be independently representable.

---

# 26. AI Structured Output Tests

AI outputs used downstream must pass:

```text
JSON parse
Pydantic/schema validation
enum validation
numeric range validation
semantic validation
reference-ID validation
```

Invalid structured output must retry/repair/fail according to policy, not enter durable state as valid.

---

# 27. AI Hallucination Tests

Golden fixtures should detect:

```text
fabricated citations
fabricated quotes
invented primary documents
unsupported dates/numbers
invented people/locations
unsupported causal claims
factual drift from Fact Sheet
```

AI model agreement is not evidence.

---

# 28. Prompt Injection Tests

Retrieved source text is untrusted input.

Test source content containing instructions to:

```text
ignore evidence policy
reveal secrets
execute commands
change configuration
publish content
call unauthorized tools
```

The system must treat these as source text, not operational instructions.

---

# 29. AI Model/Prompt Regression

Maintain golden datasets for:

```text
claim extraction
entity extraction
research synthesis
fact checking
Fact Sheet generation
content generation
quality checking
translation
```

Changing model or prompt versions requires comparison against the prior accepted baseline.

---

# 30. Runtime Portability Tests

Business/application tests must not require a real system service manager.

Use a fake/mock `ServiceManager` for ordinary tests.

Each real adapter requires contract tests for:

```text
detection
status
start
stop
restart
enable
disable
missing executable
permission denied
command timeout
unexpected output
unsupported manager
explicit override
```

---

# 31. Runtime Detection Tests

Test capability combinations such as:

```text
OpenRC utilities available
systemd utility available
SysV service utility available
launchd available
Windows service capability available
multiple conflicting utilities present
utility present but unusable
container without host service manager
no supported utility
explicit operator override
```

Expected behavior:

```text
select verified supported adapter
```

or:

```text
fail clearly / explicit manual mode
```

Never guess and execute a fallback host command.

---

# 32. OS-Independence Tests

Static/code-review checks should prevent business/domain packages from directly referencing:

```text
systemctl
rc-service
rc-update
launchctl
sc.exe
PowerShell service cmdlets
```

Allowed locations are the isolated runtime/infrastructure adapters and platform-specific deployment fixtures.

---

# 33. Local AI / POCO Tests

Benchmark candidate local models with the same fixtures.

Measure:

```text
first-token latency
total latency
tokens/sec
RAM
CPU
temperature
power behavior
failure rate
structured-output validity
```

Do not assume NPU/GPU acceleration.

Thermal/resource tests must include sustained workload, not only one short inference.

---

# 34. Social Adapter Tests

Every adapter requires:

```text
content validation
capability validation
media validation
auth failure
permission failure
rate limit
network timeout
ambiguous publish outcome
external verification
duplicate prevention
partial thread/carousel failure where applicable
```

Automated test environments must not possess ordinary live publication credentials.

---

# 35. Publication Idempotency Tests

Test:

```text
worker crash after external success but before DB update
network timeout after request transmission
queue redelivery
duplicate scheduler execution
manual retry after ambiguous result
```

A retry must not blindly create another external post.

---

# 36. Media Tests

Validate:

```text
local persistence
public-delivery separation
MIME type
size/dimension/duration
content hash
URL reachability when required
URL expiry
asset lifecycle
```

Never expose arbitrary local filesystem paths to social APIs.

---

# 37. Security Tests

At minimum:

```text
secret leakage in logs
tokens in Redis events
credentials in prompts
unsafe API error responses
unauthorized review/publish actions
runtime-control authorization
path traversal in media
SSRF/source-fetch boundaries where applicable
prompt injection
```

---

# 38. Failure-Recovery Tests

Simulate:

```text
PostgreSQL outage
Redis outage
worker crash
local AI outage
cloud AI outage
search provider outage
social platform outage
media-delivery outage
runtime-manager detection failure
power/restart scenario
```

The system must fail safe and preserve durable state.

---

# 39. Backup/Restore Tests

Regularly test restoring:

```text
PostgreSQL
versioned configuration
required media metadata/assets
deployment/runtime configuration
```

A backup not restore-tested is not considered verified.

---

# 40. End-to-End MVP Test

Canonical E2E flow:

```text
ingest article
    ↓
normalize
    ↓
cluster story
    ↓
extract claims
    ↓
request research
    ↓
collect evidence
    ↓
fact check / verification complete
    ↓
build Fact Sheet
    ↓
generate Instagram content
    ↓
quality check
    ↓
human approval
    ↓
MOCK/SANDBOX publication
    ↓
record publication result
```

Ordinary CI must not publish real content.

---

# 41. Release Gates

A release is blocked by any material failure in:

```text
schema/migration integrity
canonical enum contracts
event contracts
evidence grounding
sensitive-topic handling
runtime portability
configuration ownership
AI regression threshold
publication idempotency
security checks
backup/restore readiness where relevant
```

---

# 42. Rollback Criteria

Rollback/pause when a release causes:

```text
factual drift
citation mismatch
claim-status corruption
duplicate publication
approval bypass
runtime adapter mis-detection
database integrity failures
worker retry loops
major performance/thermal regression
secret leakage
```

Unsafe publishing triggers publication pause before other recovery work.

---

# 43. Final Testing Rules

The upstream owner has lifecycle tests for concurrent progress, empty feeds,
bounded backoff, dependency/group startup gating, component death, signal
shutdown, resource ownership, and pending recovery. Production composition
uses the existing research/content/quality factories with one shared router.
The autonomous PostgreSQL 16 / Redis 7 test starts this owner once, injects
only an official deterministic AIProvider and FeedCollector, and observes
progress through `content.generated` without manually ticking any worker or
dispatcher. Quality remains deferred before caller media; the real media
attachment service enables `READY_FOR_REVIEW`, including after owner restart.
No approval/publication is fabricated. These tests establish software closure,
not physical-device cold-boot/thermal/network or live-platform acceptance.

```text
Test facts, not only code.
Test evidence relationships, not only source counts.
Test claim status and fact-check labels as separate enums.
Test the complete current event sequence.
Test all external MVP publication as human-approved.
Test configuration ownership so research policy does not leak into editorial/source collection config.
Test runtime portability so business code never depends on one service manager.
Test ambiguous publication outcomes for duplicate prevention.
Test historical, demographic, legal, caste, and communal claims for preserved uncertainty/procedural status.
Test backups by restoring them.
No release passes solely because unit tests pass.
```
