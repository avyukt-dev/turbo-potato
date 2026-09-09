# News AI Social Media Manager

# ARCHITECTURE.md

**Status:** Canonical
**Document Role:** Source of truth for the overall system architecture, end-to-end boundaries, service decomposition, portability model, and implementation direction.
**Last updated:** 2026-09-09

Shared enums, configuration ownership, runtime portability, and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Product Definition

The News AI Social Media Manager is a human-reviewed AI newsroom and social publishing platform.

Its canonical flow is:

```text
DISCOVER
   ↓
NORMALIZE
   ↓
CLUSTER INTO STORIES
   ↓
EXTRACT CLAIMS
   ↓
RESEARCH / COLLECT EVIDENCE
   ↓
VERIFY
   ↓
BUILD FACT SHEET
   ↓
APPLY EDITORIAL INTERPRETATION
   ↓
GENERATE CONTENT
   ↓
QUALITY CHECK
   ↓
HUMAN APPROVAL
   ↓
PUBLISH
   ↓
MEASURE
```

The system is initially optimized for Indian political, governance, geopolitical, security, civilizational, historical, religious-rights, legal, demographic, and fact-checking coverage while remaining capable of covering global events.

---

# 2. Fundamental Architecture Rule

The system must preserve:

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
```

Editorial preference may determine what receives attention, what context is emphasized, tone, audience relevance, and format.

Editorial preference must not determine factual conclusions.

The prohibited workflow is:

```text
preferred conclusion
        ↓
search only for supporting evidence
        ↓
ignore contradiction
```

The required workflow is:

```text
editorial priority
        ↓
research
        ↓
collect evidence
        ↓
evaluate support + contradiction
        ↓
determine claim status
        ↓
construct Fact Sheet
        ↓
apply editorial framing
```

---

# 3. End-to-End Architecture

```text
NEWS / SOCIAL WEB
        ↓
COLLECTION
        ↓
NORMALIZATION
        ↓
DEDUPLICATION / STORY CLUSTERING
        ↓
CLAIM EXTRACTION
        ↓
RESEARCH + EVIDENCE ENGINE
        ↓
CLAIM VERIFICATION / FACT CHECKING
        ↓
FACT SHEET
        ↓
EDITORIAL ENGINE
        ↓
CONTENT ENGINE
        ↓
QUALITY GATE
        ↓
EXPLICIT HUMAN APPROVAL (MVP)
        ↓
SOCIAL PUBLISHER
        ↓
ANALYTICS
        ↓
FEEDBACK
```

For the MVP, every external social publication requires explicit human approval.

A future low-risk auto-approval mode may exist only under the conditions in `CANONICAL_CONTRACTS.md` and is disabled by default.

---

# 4. System Layers

## 4.1 Collection layer

Responsibilities:

```text
RSS / Atom
approved websites
search-based discovery
social discovery
feed/API polling
raw source acquisition
```

Outputs normalized article/source references rather than editorial conclusions.

## 4.2 Story intelligence layer

Responsibilities:

```text
canonical URL normalization
deduplication
near-duplicate detection
story clustering
language detection
entity extraction
initial classification
editorial relevance scoring
```

Article count must not be treated as confirmation count.

## 4.3 Claim and evidence layer

Responsibilities:

```text
atomic claim extraction
research planning
primary-source search
corroboration
source-lineage analysis
contradiction discovery
claim/evidence linkage
fact-check assistance
confidence assessment
```

Research is claim-driven, not article-summary-driven.

## 4.4 Fact Sheet layer

The Fact Sheet is the factual boundary between research and content generation.

Normal content generation must consume a Fact Sheet rather than a pile of raw articles.

## 4.5 Editorial layer

Responsibilities:

```text
story priority
audience relevance
editorial angle
context selection
tone
format
risk/sensitive-topic routing
```

The editorial layer must not change evidence or verification status.

## 4.6 Content layer

Produces platform-neutral and platform-specific content variants from the Fact Sheet plus Editorial Brief.

## 4.7 Quality layer

Checks for:

```text
factual drift
unsupported claims
citation mismatch
fabricated quotations
incorrect names/dates/numbers
missing material context
overstatement
defamation risk
sensitive-topic errors
```

Quality pass is not publication approval in the MVP.

## 4.8 Publishing layer

Transports approved content to configured social platforms using isolated platform adapters.

It must not rewrite factual claims.

## 4.9 Analytics layer

Collects post-publication performance and operational metrics without changing historical factual records.

---

# 5. Core Services

Initial logical services:

```text
news-api
news-collector
news-processor
news-ai-worker
news-research-worker
news-publisher
news-scheduler
news-media-worker
```

Infrastructure services:

```text
PostgreSQL
Redis
local AI service (llama.cpp)
media storage / delivery
```

Logical service boundaries do not require one operating-system process per component during the MVP.

Compatible workers may initially be consolidated while preserving domain/event boundaries.

---

# 6. Persistence and Event Architecture

Canonical rule:

```text
PostgreSQL = durable source of truth
Redis Streams = event/work transport
```

Important durable state includes:

```text
sources
articles
stories
claims
evidence
fact checks
fact sheets
content
reviews
jobs
publications
publication attempts
external platform IDs
audit history
```

Redis must never be the only location containing important business state.

Worker pattern:

```text
READ EVENT
   ↓
VALIDATE
   ↓
CHECK IDEMPOTENCY
   ↓
LOAD POSTGRESQL STATE
   ↓
PROCESS
   ↓
WRITE POSTGRESQL STATE
   ↓
EMIT NEXT EVENT
   ↓
ACK
```

Use a transactional outbox where state update and event emission must remain atomic.

---

# 7. Canonical Event Flow

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
human approval in PostgreSQL
    ↓
publication.scheduled
    ↓
publication.executed / publication.failed
    ↓
analytics.requested
    ↓
analytics.collected
```

`story.verified` means the configured verification stage completed. It does not mean every claim is true.

Exact event contracts are owned by `EVENTS.md`.

---

# 8. Data Model Overview

Canonical core table family:

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

The precise schema is owned by `DATA_MODEL.md`.

---

# 9. Claim Model

A claim is an independently assessable factual proposition.

Canonical example:

```json
{
  "claim": "X happened in Y on date Z",
  "status": "PARTIALLY_SUPPORTED",
  "confidence_score": 0.82,
  "source_ids": ["source-123", "source-456"],
  "evidence_ids": ["evidence-001", "evidence-002"]
}
```

`status` uses `ClaimVerificationStatus` only:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

Fact-check verdicts such as `FALSE`, `FABRICATED`, `SATIRE`, and `PARTIALLY_TRUE` belong to `FactCheckLabel`, not claim verification status.

---

# 10. Evidence Model

Every material claim should link to supporting, contradicting, qualifying, and contextual evidence as applicable.

Source hierarchy and evidence methodology are owned by `SOURCE_AND_RESEARCH.md`.

Important invariant:

```text
10 republished articles from one report
!=
10 independent confirmations
```

---

# 11. Fact Sheet

Canonical conceptual structure:

```text
FACT SHEET
├── headline
├── summary
├── claims with ClaimVerificationStatus
├── fact-check verdicts where applicable
├── evidence
├── sources
├── timeline
├── entities
├── locations
├── context
├── counterclaims
├── unresolved questions
├── confidence assessment
├── risk level
└── sensitive-topic metadata
```

The Fact Sheet version used for publication must remain auditable.

Material corrections create a new version.

---

# 12. AI Architecture

Application code calls provider-independent abstractions.

```text
AIProvider
├── LocalLlamaProvider
├── LocalQwenProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
└── FutureProvider
```

Separately:

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── SpecialistSearchProvider
└── FutureProvider
```

Search/research providers are not AI model providers.

AI may assist classification, extraction, synthesis, generation, and quality checking.

AI is not itself evidence.

---

# 13. Local AI

Initial local inference service:

```text
llama.cpp + GGUF
```

Initial target workload:

```text
language detection
classification
keyword/entity extraction
story similarity
spam filtering
short summarization
structured transformation
initial claim extraction
```

Practical candidate model sizes on the current POCO should be measured rather than assumed.

Guidance:

```text
0.5B–3B    primary candidate range
4B         possible if measured viable
7B–8B Q4   heavy / experimental
13B+       not an initial POCO target
```

CPU/GPU/NPU capability must be benchmarked on the actual deployed runtime. NPU acceleration must never be assumed.

---

# 14. Historical Research Architecture

Historical research must preserve separate evidence domains:

```text
chronology
archaeology
epigraphy
literary sources
linguistics
genetics
material culture
population movement
cultural transmission
political expansion
military conflict
modern scholarship
```

For contested questions, the system records competing hypotheses and their evidence rather than forcing a predetermined ideological binary.

Language dispersal, population movement, genetic ancestry, archaeological continuity, cultural transmission, and military invasion are related but distinct claims.

---

# 15. Legal, Caste, Religious, and Demographic Safety Boundaries

The system preserves procedural legal status, including:

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

An accusation is not a conviction.

Caste-related facts may be reported where relevant and verified, but caste must not be used as a behavioral proxy or basis for collective guilt.

Demographic reporting separates observed data, statistical interpretation, possible explanation, causal evidence, and editorial interpretation.

Religious/civilizational analysis may use `INDIC_CIVILIZATIONAL_CONTEXT` while preserving distinct Hindu, Buddhist, Jain, Sikh, indigenous/regional, and ancient Indian cultural identities.

---

# 16. Social Publishing Architecture

Initial adapters:

```text
Instagram
X
Facebook
Telegram
```

Canonical flow:

```text
approved ContentVariant
        ↓
Renderer
        ↓
Media Preparation
        ↓
Platform Adapter
        ↓
External API
        ↓
Verification
        ↓
Publication Record
```

Publishing must be idempotent.

An ambiguous timeout must not trigger blind retry.

---

# 17. Media Architecture

Media persistence and public delivery are separate concerns.

Initial local persistence may use the POCO filesystem.

If a social platform requires a fetchable HTTPS URL, selected media is exposed only through a deliberate media-delivery layer or object storage.

Do not expose the admin API, database, backups, credentials, or arbitrary filesystem paths to satisfy platform ingestion.

---

# 18. Platform-Independent Runtime Architecture

The application must remain OS/platform/service-manager independent.

Business and domain code must not directly execute:

```text
rc-service
rc-update
systemctl
service
launchctl
sc.exe
PowerShell service commands
```

Instead:

```text
newsctl / RuntimeController
        ↓
RuntimeDetector
        ↓
ServiceManager interface
        ↓
capability-selected adapter
```

Detection uses available capabilities and runtime metadata, for example:

```text
Python platform/os information
/etc/os-release where available
shutil.which(...) or equivalent executable discovery
container/runtime metadata
explicit operator override
```

Possible infrastructure adapters include OpenRC, systemd, SysV, launchd, Windows service management, and an explicit unsupported/manual mode.

The current POCO uses OpenRC, but that is only the current deployment profile.

OS-specific adapter implementation is isolated inside the runtime/infrastructure layer and must not leak into application/domain code.

---

# 19. Repository Structure

Recommended implementation layout:

```text
news-ai/
│
├── apps/
│   ├── api/
│   ├── collector/
│   ├── processor/
│   ├── ai-worker/
│   ├── research-worker/
│   ├── publisher/
│   ├── scheduler/
│   └── media-worker/
│
├── packages/
│   ├── domain/
│   ├── database/
│   ├── events/
│   ├── ai/
│   ├── evidence/
│   ├── editorial/
│   ├── content/
│   ├── social/
│   ├── runtime/
│   └── common/
│
├── infra/
│   ├── postgres/
│   ├── redis/
│   ├── runtime/
│   │   ├── adapters/
│   │   └── templates/
│   └── postmarketos/
│
├── config/
│   ├── sources/
│   ├── research/
│   ├── editorial/
│   ├── models/
│   ├── prompts/
│   └── platforms/
│
├── migrations/
├── tests/
├── docs/
└── scripts/
```

There is no architecture-level dependency on `systemd/`, OpenRC, Docker, or any other one runtime technology.

---

# 20. Configuration Ownership

## `config/sources/`

Source identity and collection mechanics:

```text
registry.yaml
feeds.yaml
collection.yaml
```

## `config/research/`

Evidence/research methodology:

```text
source-policy.yaml
search-policy.yaml
corroboration.yaml
fact-check.yaml
historical-research.yaml
```

## `config/editorial/`

Editorial preference and publication policy:

```text
priorities.yaml
taxonomy.yaml
content-style.yaml
risk-policy.yaml
publishing-policy.yaml
```

## Other roots

```text
config/models/      → model/provider routing
config/prompts/     → versioned prompts
config/platforms/   → social-platform constraints/capabilities
```

The same policy must not be defined in multiple roots.

---

# 21. API Architecture

FastAPI is the HTTP/orchestration boundary.

```text
HTTP
 ↓
FastAPI route
 ↓
application service
 ↓
domain service
 ↓
repository / event / provider abstraction
```

Long-running research, inference, media generation, and publishing execute asynchronously through durable jobs/events.

Route handlers must not directly contain provider SDK calls, complex SQL, platform retry logic, or long-running model inference.

---

# 22. Runtime Deployment

Initial target host:

```text
Xiaomi POCO F1 / beryllium
postmarketOS
aarch64
headless
Tailscale-managed
currently OpenRC
```

Deployment principle:

```text
native service management selected by runtime adapter = default on POCO
containers = optional
```

The application must remain portable to other Linux distributions, future ARM/x86 servers, containers, macOS development environments, and Windows development environments without rewriting business logic.

---

# 23. MVP Scope

```text
RSS/news collection
    ↓
deduplication/story clustering
    ↓
classification/editorial priority
    ↓
claim extraction
    ↓
research/evidence
    ↓
Fact Sheet
    ↓
Instagram content
    ↓
quality check
    ↓
human approval
    ↓
publish
```

Initial production philosophy:

```text
ONE POCO
ONE POSTGRESQL
ONE REDIS
ONE LOCAL LLM
ONE CLOUD LLM ROUTE
NEWS COLLECTION
FACT ENGINE
INSTAGRAM
```

Do not start with Kubernetes, Kafka, multi-region deployment, a large vector database, giant local models, or autonomous high-risk publishing.

---

# 24. Build Order

Recommended implementation order:

```text
01 Repository skeleton
02 Configuration loader + validation
03 Runtime/platform abstraction
04 PostgreSQL schema + migrations
05 SQLAlchemy models/repositories
06 Redis Streams + outbox
07 FastAPI service
08 Collector framework
09 RSS collector
10 Article normalizer
11 Story clustering
12 Editorial taxonomy
13 AI provider abstraction
14 llama.cpp integration
15 AI router
16 Claim extraction
17 Research/SearchProvider abstraction
18 Evidence engine
19 Fact-check engine
20 Fact-sheet generator
21 Content engine
22 Quality gate
23 Human review workflow
24 Instagram adapter
25 Scheduler
26 Publication tracking/idempotency
27 Health/monitoring/newsctl
```

Then add historical-research specialization, additional social platforms, advanced analytics, richer media generation, and broader automation.

---

# 25. Documentation Boundaries

```text
CANONICAL_CONTRACTS.md             shared invariants/precedence
ARCHITECTURE.md                    end-to-end architecture
DATA_MODEL.md                      persistence
EVENTS.md                          event contracts
AI_PLATFORM.md                     AI provider/model architecture
SOURCE_AND_RESEARCH.md             research/evidence architecture
CONTENT_AND_EDITORIAL.md           editorial/content policy
CONTENT_SCHEMAS.md                 structured application contracts
INFRASTRUCTURE_AND_DEPLOYMENT.md   runtime/deployment architecture
SOCIAL_PUBLISHING.md               social execution
API_SPEC.md                        HTTP boundary
TESTING_AND_EVALUATION.md          testing/release gates
OPERATIONS_RUNBOOK.md              day-to-day operations
```

No domain document should redefine another document's owned concept with a different enum, path, state machine, or runtime assumption.

---

# 26. Final Architecture Rules

```text
PostgreSQL is durable truth.
Redis Streams is event/work transport.
AI is not evidence.
Editorial preference selects attention, not truth.
Claim verification status is not a fact-check verdict.
UNVERIFIED is not FALSE.
Fact Sheet is the normal factual boundary before content generation.
All external publication requires explicit human approval in the MVP.
Publishing is idempotent and ambiguity-safe.
Runtime/service-manager behavior is capability-detected behind an abstraction.
Business code contains no OS/service-manager-specific commands.
Source collection config, research policy, and editorial policy have separate owners.
Current docs use current enums and examples; stale illustrative terminology is not retained intentionally.
```
