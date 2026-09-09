# News AI Social Media Manager

# ARCHITECTURE.md

**Status:** Canonical
**Document Role:** Source of truth for the overall system architecture, end-to-end boundaries, service decomposition, portability model, and implementation direction.
**Last updated:** 2026-09-09

Shared enums, configuration ownership, runtime portability, and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Product Definition

The News AI Social Media Manager is a human-reviewed AI newsroom and social publishing platform.

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
VERIFY / FACT CHECK
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

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
```

Editorial preference selects attention, context, tone, audience relevance, and format.

Editorial preference must not determine factual conclusions.

Prohibited:

```text
preferred conclusion
        ↓
search only for supporting material
        ↓
ignore contradiction
```

Required:

```text
editorial priority
        ↓
research
        ↓
collect support + contradiction
        ↓
determine claim status
        ↓
Fact Sheet
        ↓
editorial framing
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

---

# 4. Layer Responsibilities

## Collection

```text
RSS / Atom
approved websites
search-based discovery
social discovery
feed/API polling
raw source acquisition
```

## Story intelligence

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

## Research/evidence

```text
atomic claim extraction
research planning
primary-source search
corroboration
source-lineage analysis
contradiction discovery
claim/evidence linkage
confidence assessment
```

## Fact Sheet

Canonical factual boundary before normal content generation.

## Editorial

```text
story priority
audience relevance
editorial angle
context selection
tone
format
risk/sensitive-topic routing
```

Editorial logic must not mutate evidence or claim verification status.

## Content

Produces platform-neutral/platform-specific variants from the Fact Sheet plus editorial brief.

## Quality

Detects factual drift, unsupported claims, citation mismatch, fabricated quotations, wrong names/dates/numbers, missing context, overstatement, defamation risk, and sensitive-topic errors.

## Publishing

Transports approved content through platform adapters. It does not rewrite factual claims.

## Analytics

Collects post-publication performance/operational data without changing historical factual records.

---

# 5. Logical Services

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

Logical boundaries do not require one host process per component during the MVP.

---

# 6. Persistence and Events

```text
PostgreSQL = durable source of truth
Redis Streams = event/work transport
```

Important durable state includes stories, claims, evidence, Fact Sheets, content, reviews, jobs, publications, attempts, external IDs, and audit history.

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

Use a transactional outbox where database state and event intent must remain atomic.

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
analytics.requested / analytics.collected
```

`story.verified` means the configured verification stage completed; it does not mean every claim is true.

---

# 8. Core Persistence Family

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

Precise persistence semantics are owned by `DATA_MODEL.md`.

---

# 9. Claim and Fact-Check Semantics

Canonical claim example:

```json
{
  "claim": "X happened in Y on date Z",
  "status": "PARTIALLY_SUPPORTED",
  "confidence_score": 0.82,
  "source_ids": ["source-123", "source-456"],
  "evidence_ids": ["evidence-001", "evidence-002"]
}
```

`ClaimVerificationStatus`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

`FactCheckLabel`:

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

These enums are separate.

---

# 10. Fact Sheet

```text
FACT SHEET
├── headline
├── summary
├── claims with canonical status
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

The exact version used for publication must remain auditable.

---

# 11. AI and Search

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

AI may analyze/transform/generate. Search providers discover candidate sources. Neither substitutes for evidence validation.

---

# 12. Local AI

Initial local service:

```text
llama.cpp + GGUF
```

Initial local workloads favor language detection, classification, keyword/entity extraction, similarity, short summarization, structured transformation, and initial claim extraction.

Model sizes and acceleration must be benchmarked rather than assumed.

---

# 13. Historical Research

Preserve separate evidence domains:

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

Competing historical hypotheses are represented by evidence, not predetermined ideology.

---

# 14. Sensitive/Legal/Demographic Boundaries

Preserve legal procedural states such as allegation, FIR, investigation, arrest, charge, trial, court finding, conviction, acquittal, appeal, and final judgment.

Caste identity must not be used as a behavioral proxy or basis for collective guilt.

Demographic reporting separates observed data, statistical interpretation, possible explanations, causal evidence, and editorial interpretation.

Distinct Hindu, Buddhist, Jain, Sikh, indigenous/regional, and ancient Indian cultural identities remain distinct within `INDIC_CIVILIZATIONAL_CONTEXT`.

---

# 15. Social Publishing

Initial adapters:

```text
Instagram
X
Facebook
Telegram
```

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

Publishing is idempotent. Ambiguous external outcomes are verified before retry.

---

# 16. Media

Local persistence and public delivery are separate.

If a platform requires a fetchable HTTPS URL, selected media is exposed through a deliberate media-delivery layer/object storage only.

Do not expose arbitrary filesystem paths or internal services.

---

# 17. Platform-Independent Runtime

Business/domain/application code must not directly execute host service utilities such as:

```text
rc-service
rc-update
systemctl
service
launchctl
Windows service commands
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

Detection considers runtime capabilities including:

```text
platform.system()
platform.machine()
os.name
/etc/os-release where available
executable discovery
container/runtime metadata
explicit operator override
```

The current POCO/OpenRC combination is a runtime profile, not a business-code branch.

---

# 18. Repository Structure

```text
news-ai/
├── apps/
│   ├── api/
│   ├── collector/
│   ├── processor/
│   ├── ai-worker/
│   ├── research-worker/
│   ├── publisher/
│   ├── scheduler/
│   └── media-worker/
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
├── infra/
│   ├── postgres/
│   ├── redis/
│   └── runtime/
│       ├── adapters/
│       ├── templates/
│       └── profiles/
├── config/
│   ├── sources/
│   ├── research/
│   ├── editorial/
│   ├── models/
│   ├── prompts/
│   └── platforms/
├── migrations/
├── tests/
├── docs/
└── scripts/
```

Runtime profiles are data/configuration, for example a POCO profile describing architecture and expected capabilities. They are not OS-specific business code.

---

# 19. Configuration Ownership

```text
config/sources/
    registry.yaml
    feeds.yaml
    collection.yaml

config/research/
    source-policy.yaml
    search-policy.yaml
    corroboration.yaml
    fact-check.yaml
    historical-research.yaml

config/editorial/
    priorities.yaml
    taxonomy.yaml
    content-style.yaml
    risk-policy.yaml
    publishing-policy.yaml

config/models/      AI provider/model routing
config/prompts/     versioned prompts
config/platforms/   social constraints/capabilities
```

One concern has one canonical owner.

---

# 20. API Boundary

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

Long-running research, inference, media generation, and publishing are asynchronous.

Host-native service control is not embedded in normal API routes.

---

# 21. Deployment Portability

Current physical target:

```text
Xiaomi POCO F1 / beryllium
aarch64
postmarketOS
headless
currently OpenRC
```

This host is represented by a runtime profile. Application code remains portable to other Linux systems, ARM/x86 servers, containers, macOS/Windows development hosts, and future compute nodes.

Containers are optional, not required.

---

# 22. MVP

```text
RSS/news collection
    ↓
story clustering
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

Do not start with Kubernetes, Kafka, multi-region deployment, giant local models, a large vector database, or autonomous high-risk publishing.

---

# 23. Build Order

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
17 SearchProvider/research abstraction
18 Evidence engine
19 Fact-check/verification engine
20 Fact-sheet generator
21 Content engine
22 Quality gate
23 Human review workflow
24 Instagram adapter
25 Scheduler
26 Publication tracking/idempotency
27 Health/monitoring/newsctl
```

---

# 24. Documentation Boundaries

```text
CANONICAL_CONTRACTS.md             shared invariants
ARCHITECTURE.md                    end-to-end architecture
DATA_MODEL.md                      persistence
EVENTS.md                          event contracts
AI_PLATFORM.md                     AI architecture
SOURCE_AND_RESEARCH.md             research/evidence
CONTENT_AND_EDITORIAL.md           editorial/content policy
CONTENT_SCHEMAS.md                 structured contracts
INFRASTRUCTURE_AND_DEPLOYMENT.md   runtime/deployment
SOCIAL_PUBLISHING.md               social execution
API_SPEC.md                        HTTP boundary
TESTING_AND_EVALUATION.md          testing/release gates
OPERATIONS_RUNBOOK.md              operations
```

---

# 25. Final Architecture Rules

```text
PostgreSQL is durable truth.
Redis Streams is event/work transport.
AI is not evidence.
Editorial preference selects attention, not truth.
ClaimVerificationStatus != FactCheckLabel.
UNVERIFIED != REFUTED and UNVERIFIED != FALSE.
Fact Sheet is the normal factual boundary.
All external MVP publication requires explicit human approval.
Publishing is idempotent and ambiguity-safe.
Runtime behavior is capability-detected behind adapters.
Business code contains no OS/service-manager-specific commands.
Current device/OS details live in runtime profiles, not OS-named code paths.
Source collection, research policy, and editorial policy have separate owners.
Current docs use current examples; stale illustrative terminology is not retained intentionally.
```
