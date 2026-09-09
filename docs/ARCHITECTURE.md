# News AI Social Media Manager

## Consolidated Canonical Architecture & System Specification

**Status:** Active
**Version:** 0.1
**Purpose:** Canonical source of truth for architecture and implementation
**Deployment target:** Xiaomi POCO F1 (`beryllium`) running postmarketOS Linux
**Primary operator:** Human-reviewed AI newsroom
**Last updated:** 2026-09-09

---

# 1. Product & Editorial Mission

## 1.1 Product Definition

The **News AI Social Media Manager** is an AI-powered newsroom system for continuously:

```text
Discovering
    ↓
Researching
    ↓
Verifying
    ↓
Contextualizing
    ↓
Scoring
    ↓
Generating
    ↓
Reviewing
    ↓
Publishing
    ↓
Measuring
```

The system is initially optimized for Indian political, geopolitical, civilizational, historical, religious-rights and fact-checking coverage, while remaining capable of covering global events.

Primary areas include:

* Indian governance and politics
* Indian achievements and failures
* Defence and national security
* Indian geopolitics
* International diplomacy
* War and conflict
* Religious freedom
* Hinduism and Indian religious traditions
* Sikhism, Jainism and Buddhism
* Indigenous and regional Indian traditions
* Ancient, medieval, colonial and modern Indian history
* Archaeology and civilization
* Demographic change
* Human rights
* Caste-related incidents and legislation
* SC/ST Act cases
* Atrocities and communal violence
* Blasphemy and religiously derogatory remarks
* Political and geographical developments
* Fact checking and misinformation

---

## 1.2 Editorial Identity vs Factual Integrity

The system explicitly separates:

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
```

Editorial policy may determine:

* Which topics receive priority
* Which stories are investigated
* Which context is emphasized
* Which audience needs are served
* How verified information is presented

Editorial policy must **not** determine factual conclusions.

The prohibited workflow is:

```text
Preferred conclusion
        ↓
Find supporting evidence
        ↓
Ignore contradictory evidence
```

The required workflow is:

```text
Editorial priority
        ↓
Investigate
        ↓
Collect evidence
        ↓
Evaluate competing evidence
        ↓
Determine factual status
        ↓
Choose editorial framing
```

---

# 2. Core Principles & Safety Rules

## 2.1 Evidence Before Generation

The LLM must not receive many raw articles and immediately produce a social-media post.

Required pipeline:

```text
Articles
    ↓
Claims
    ↓
Evidence
    ↓
Fact Sheet
    ↓
Content
```

---

## 2.2 Story-Centric Processing

The system processes **stories**, not merely articles.

```text
30 articles
    ↓
ONE STORY
    ↓
Evidence aggregation
    ↓
Claims
    ↓
Fact Sheet
    ↓
Multiple platform outputs
```

Multiple articles reporting the same event must not automatically become multiple independent confirmations.

---

## 2.3 Evidence Independence

Ten articles repeating the same original report do not represent ten independent sources.

The system should track:

* Source lineage
* Original source
* Republished reports
* Independent confirmations
* Primary evidence
* Contradictory evidence

---

## 2.4 Human Control

The system is an **AI newsroom assistant**, not an autonomous propaganda or misinformation engine.

Sensitive stories require human review before publication.

---

## 2.5 Legal and Allegation Accuracy

The system must distinguish:

```text
Allegation
Complaint
FIR
Investigation
Arrest
Charge Sheet
Trial
Conviction
Acquittal
Appeal
```

For example:

```text
"Accused of X"
```

must not automatically become:

```text
"Committed X"
```

---

## 2.6 Group Generalization

The system must never turn an individual's alleged behavior into a claim about an entire:

* Religion
* Caste
* Ethnicity
* Nationality
* Political group
* Community

unless a genuinely collective action is supported by evidence.

---

## 2.7 Demographic Reporting

Demographic analysis must separate:

```text
Observed data
      ↓
Statistical interpretation
      ↓
Possible explanations
      ↓
Evidence for explanations
      ↓
Editorial interpretation
```

Population changes must not automatically be framed as communal blame.

---

## 2.8 Version Everything Important

Version:

* Editorial rules
* Source policies
* Fact-check rules
* Historical research methodology
* Content style
* Risk policy
* Publishing policy
* AI prompts
* AI models
* Evidence packets
* Generated content

---

# 3. Editorial Taxonomy & Research Policy

## 3.1 Top-Level Taxonomy

```text
INDIA
GEOPOLITICS
SECURITY
POLITICS
CIVILIZATION
RELIGION
HISTORY
LAW
RIGHTS
DEMOGRAPHICS
ECONOMY
SCIENCE_TECH
FACT_CHECK
```

---

## 3.2 India

```text
INDIA
├── Government
├── Parliament
├── Judiciary
├── Elections
├── Governance
├── Infrastructure
├── Economy
├── Defence
├── Space
├── Science
├── Technology
└── International Relations
```

---

## 3.3 Geopolitics

```text
GEOPOLITICS
├── Pakistan
├── China
├── United States
├── Russia
├── Middle East
├── Bangladesh
├── Nepal
├── Sri Lanka
├── Indian Ocean
├── Indo-Pacific
└── United Nations
```

---

## 3.4 Civilization

```text
CIVILIZATION
├── Ancient India
├── Indus Civilization
├── Saraswati-related research
├── Vedic Civilization
├── Classical India
├── Medieval India
├── Colonial India
├── Modern India
├── Sanskrit
├── Archaeology
├── Epigraphy
├── Philosophy
└── Cultural Heritage
```

---

## 3.5 Religion

```text
RELIGION
├── Hinduism
├── Sikhism
├── Jainism
├── Buddhism
├── Indigenous Traditions
├── Temples
├── Religious Sites
├── Religious Freedom
├── Religious Violence
├── Religious Discrimination
├── Conversion
└── Blasphemy
```

---

## 3.6 Law & Rights

```text
LAW
├── Supreme Court
├── High Courts
├── Constitutional Law
├── Criminal Law
├── SC/ST Act
├── Hate Speech
├── Religious Freedom
└── Civil Rights
```

---

## 3.7 Indian Civilizational Context

Indian traditions must not be artificially collapsed into a single identity.

Use:

```text
INDIC_CIVILIZATIONAL_CONTEXT
├── Hindu Traditions
├── Buddhist Traditions
├── Jain Traditions
├── Sikh Traditions
├── Indigenous / Regional Traditions
└── Ancient Indian Cultural Traditions
```

This allows shared civilizational analysis while preserving distinct identities.

---

## 3.8 Caste and Sensitive Social Issues

The system may report verified caste-related facts when relevant.

It must not:

* Manufacture stereotypes
* Infer group behavior from caste
* Attribute collective guilt
* Generalize individual crimes
* Use caste identity as a behavioral proxy

---

## 3.9 Historical Research

Historical controversies must be decomposed into evidence domains:

```text
Language
Population movement
Genetics
Archaeology
Material culture
Civilization
Political expansion
Military conflict
Migration
Cultural transmission
```

For controversial subjects such as Indo-European/Indo-Aryan origins, the system must represent competing hypotheses rather than forcing an ideological binary.

Historical research should include:

* Primary sources
* Archaeological evidence
* Inscriptions
* Literary sources
* Linguistic evidence
* Genetic evidence
* Modern scholarship
* Competing interpretations
* Chronological uncertainty

---

## 3.10 Historical Event Model

```text
HistoricalEvent
├── id
├── title
├── date_start
├── date_end
├── locations
├── people
├── organizations
├── primary_sources
├── archaeological_evidence
├── inscriptions
├── literary_sources
├── linguistic_evidence
├── genetic_evidence
├── scholarly_interpretations
├── competing_hypotheses
└── confidence
```

---

## 3.11 Source Hierarchy

### Level 1 — Primary

* Court judgments
* Government documents
* Parliament records
* Official statistics
* Police statements
* Military releases
* Treaties
* Official diplomatic statements
* Original research papers
* Inscriptions
* Archaeological evidence
* Original datasets

### Level 2 — Established Secondary

* Major newspapers
* International news agencies
* Academic publications
* Specialist publications

### Level 3 — Research & Analysis

* Think tanks
* Research organizations
* Investigative journalism
* Subject specialists
* Academic commentary

### Level 4 — Discovery

* X
* Reddit
* Telegram
* Instagram
* YouTube
* Facebook
* Blogs

Level 4 sources are useful for discovering stories and claims but should generally not be the sole evidence for serious claims.

---

## 3.12 Fact-Check Labels

```text
TRUE
MOSTLY_TRUE
PARTIALLY_TRUE
MISLEADING
UNVERIFIED
FALSE
FABRICATED
OUT_OF_CONTEXT
SATIRE
```

Critical rule:

```text
UNVERIFIED != FALSE
```

---

# 4. End-to-End System Architecture

## 4.1 Core Pipeline

```text
NEWS / SOCIAL WEB
        ↓
COLLECTION
        ↓
NORMALIZATION
        ↓
DEDUPLICATION
        ↓
STORY CLUSTERING
        ↓
CLAIM EXTRACTION
        ↓
EVIDENCE ENGINE
        +
EDITORIAL ENGINE
        ↓
AI ORCHESTRATOR
   ├── Local LLM
   ├── Cloud LLM
   └── Search / Research
        ↓
FACT SHEET
        ↓
CONTENT ENGINE
        ↓
QUALITY GATE
        ↓
HUMAN REVIEW
        ↓
SOCIAL PUBLISHER
        ↓
ANALYTICS
        ↓
FEEDBACK
```

---

## 4.2 Core Services

Initial services:

```text
postgres
redis
news-api
news-collector
news-processor
news-ai-worker
news-publisher
news-scheduler
```

All may initially run on the same POCO.

---

## 4.3 Repository

```text
news-ai/
│
├── apps/
│   ├── api/
│   ├── collector/
│   ├── processor/
│   ├── ai-worker/
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
│   └── common/
│
├── infra/
│   ├── postgres/
│   ├── redis/
│   ├── systemd/
│   └── postmarketos/
│
├── config/
│   ├── editorial/
│   ├── sources/
│   ├── models/
│   ├── platforms/
│   └── prompts/
│
├── migrations/
├── tests/
├── docs/
└── scripts/
```

The project remains a monorepo initially.

---

## 4.4 Technology Stack

| Component             | Technology                         |
| --------------------- | ---------------------------------- |
| Backend               | Python                             |
| API                   | FastAPI                            |
| ORM                   | SQLAlchemy                         |
| Validation            | Pydantic                           |
| Database              | PostgreSQL                         |
| Event bus             | Redis Streams                      |
| Local AI              | llama.cpp + GGUF                   |
| Search                | Pluggable research/search provider |
| Scheduler             | Python worker / APScheduler        |
| Frontend              | React / Next.js                    |
| Deployment            | Docker where practical             |
| Remote administration | Tailscale                          |

---

# 5. Data Model & Provenance

## 5.1 Database

PostgreSQL is the source of truth.

Initial tables:

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

Use `jsonb` selectively for evolving AI output and source metadata.

---

## 5.2 Data Lineage

The minimum provenance chain is:

```text
SOURCE
  ↓
ARTICLE
  ↓
STORY
  ↓
CLAIM
  ↓
EVIDENCE
  ↓
FACT SHEET
  ↓
CONTENT
  ↓
REVIEW
  ↓
PUBLICATION
```

Every published item must be traceable through this chain.

---

## 5.3 Story Model

```text
Story
├── id
├── title
├── claims[]
├── sources[]
├── entities[]
├── timeline[]
├── locations[]
├── evidence[]
├── counterclaims[]
├── editorial_scores
├── confidence
├── risk_level
└── status
```

---

## 5.4 Claim Model

```json
{
  "claim": "X happened in Y on date Z",
  "status": "partially_confirmed",
  "confidence": 0.82,
  "sources": [
    "source-123",
    "source-456"
  ],
  "primary_evidence": [
    "court-document",
    "police-statement"
  ]
}
```

Claims must be independently assessable.

---

## 5.5 Evidence Packet

Every published story requires:

```text
EvidencePacket
├── story_id
├── claims
├── sources
├── primary_evidence
├── supporting_evidence
├── contradictory_evidence
├── unresolved_questions
├── confidence_scores
├── editorial_angle
├── fact_check_result
├── AI_models_used
├── prompts_used
├── reviewer
├── review_status
└── timestamps
```

This is the publication audit trail.

---

## 5.6 Entities

First-class entities:

```text
Person
Organization
Government
PoliticalParty
Country
City
Region
ReligiousTradition
HistoricalFigure
MilitaryUnit
Court
Law
Event
```

Entity mentions connect source material to canonical entities.

---

# 6. News Collection & Story Intelligence

## 6.1 Collector Architecture

```text
Collector
├── RSSCollector
├── NewsWebsiteCollector
├── SearchCollector
├── XCollector
├── RedditCollector
├── YouTubeCollector
└── FutureCollector
```

Collectors produce normalized records.

---

## 6.2 Normalized Article

```json
{
  "source_id": "source-123",
  "url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "title": "Example headline",
  "author": "Author",
  "published_at": "2026-09-09T10:00:00Z",
  "language": "en",
  "content": "...",
  "source_type": "news",
  "metadata": {}
}
```

---

## 6.3 Deduplication & Clustering

Use:

```text
URL canonicalization
+
Title similarity
+
Named entities
+
Timestamp
+
Location
+
Semantic similarity
+
Source lineage
```

Output:

```text
Articles
   ↓
Story Cluster
   ↓
Evidence aggregation
```

---

## 6.4 Editorial Scoring

Scores remain independent:

```text
importance
india_relevance
geopolitical_relevance
civilizational_relevance
religious_relevance
historical_relevance
fact_check_value
breaking_news_score
audience_interest
evidence_strength
controversy
publication_risk
```

Important:

```text
Editorial priority ≠ Evidence strength
```

---

## 6.5 Source Health

Track:

```text
last_success
last_failure
failure_count
articles_collected
average_latency
HTTP errors
parsing errors
```

Broken or degraded sources must be visible to administrators.

---

## 6.6 Redis Events

Use Redis Streams for asynchronous processing.

Initial events:

```text
article.discovered
article.normalized
story.created
story.clustered
claims.extracted
evidence.requested
evidence.collected
story.verified
content.requested
content.generated
content.quality_checked
publication.scheduled
publication.executed
analytics.collected
```

Workers should be idempotent.

---

# 7. Evidence, Fact Checking & Historical Research

## 7.1 Research Pipeline

```text
Claim
  ↓
Primary Evidence
  ↓
Independent Sources
  ↓
Supporting Evidence
  ↓
Contradictory Evidence
  ↓
Context
  ↓
Confidence
  ↓
Fact Sheet
```

Research must actively search for contradictory evidence.

---

## 7.2 Research Depth

### Low Risk

```text
Basic verification
```

### Medium Risk

```text
Multiple independent sources
```

### High Risk

```text
Primary evidence
+
Multiple independent sources
+
Contradiction search
+
Human review
```

---

## 7.3 Contradiction Model

Every important claim should support:

```text
Claim
├── Supporting Evidence
├── Contradictory Evidence
└── Unresolved Evidence
```

---

## 7.4 Confidence

Confidence should consider:

```text
Primary source availability
Source independence
Source reliability
Corroboration
Contradictory evidence
Recency
Specificity
Directness of evidence
```

Article count alone must not determine confidence.

---

## 7.5 Sensitive Topics

Stricter review applies to:

```text
Communal violence
Criminal allegations
Religious accusations
SC/ST allegations
Sexual assault
Terrorism attribution
War casualty claims
Election fraud
Religious conversion claims
Breaking news without primary confirmation
```

Default:

```text
AI Research
    ↓
Evidence Packet
    ↓
Human Review
    ↓
Publication
```

---

## 7.6 Breaking News

Use confidence states:

```text
LOW
MEDIUM
HIGH
CONFIRMED
```

Example:

```text
BREAKING
CONFIDENCE: LOW

Sources:
1 social source
0 primary sources
0 independent confirmations
```

As evidence improves, confidence can be updated.

---

## 7.7 Corrections

If a claim changes:

```text
Error detected
    ↓
Claim corrected
    ↓
Evidence updated
    ↓
Fact Sheet regenerated
    ↓
Affected content identified
    ↓
Correction workflow
```

Previous versions must remain available for audit.

---

# 8. AI Platform & Model Routing

## 8.1 Provider Abstraction

```text
AIProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
├── LocalLlamaProvider
├── LocalQwenProvider
└── FutureProvider
```

Application code uses:

```python
result = ai.generate(...)
```

and must not contain provider-specific implementation throughout the codebase.

---

## 8.2 Task Routing

| Task                     | Preferred            |
| ------------------------ | -------------------- |
| Language detection       | Local                |
| Classification           | Local                |
| Keyword extraction       | Local                |
| Entity extraction        | Local                |
| Basic summarization      | Local                |
| Story similarity         | Local                |
| Spam filtering           | Local                |
| Initial claim extraction | Local                |
| JSON transformation      | Local                |
| Complex research         | Cloud                |
| Deep fact checking       | Cloud + Search       |
| Historical research      | Cloud + Search       |
| Final writing            | Best available       |
| Image generation         | External image model |

---

## 8.3 POCO F1

Target device:

```text
Device: Xiaomi POCO F1
Codename: beryllium
OS: postmarketOS
Architecture: aarch64
RAM: ~5.5 GB usable
```

Primary local-AI workload:

```text
Classification
Language detection
Keyword extraction
Entity extraction
Basic summarization
Semantic similarity
Priority scoring
Spam filtering
Initial claim extraction
JSON transformation
```

---

## 8.4 Local Model Strategy

Initial model classes:

```text
~0.5B–1.5B
~2B–3B
~4B
```

A 7B–8B quantized model may be possible but is not the default target.

13B+ models are not a practical target for the POCO.

The project must benchmark actual models on the current Linux environment.

Potential stack:

```text
Python
    ↓
llama.cpp
    ↓
GGUF
    ↓
CPU / supported accelerator
```

Snapdragon/Hexagon/NPU acceleration must be experimentally validated before being considered production functionality.

---

## 8.5 AI Router

Routing considers:

```text
task_type
complexity
latency_requirement
cost
privacy
context_length
model_availability
confidence_requirement
```

Fallback:

```text
Local LLM
    ↓
Overloaded / Failed
    ↓
Cloud Provider
```

---

## 8.6 AI Resource Controls

Local inference supports:

```text
Maximum concurrency
Maximum context size
Timeout
Memory limits
Queue priority
Cancellation
Fallback provider
```

---

## 8.7 AI Evaluation

Models are evaluated using fixed test datasets.

Measure:

```text
Classification accuracy
Entity extraction accuracy
Claim extraction accuracy
JSON validity
Hallucination rate
Source preservation
Summary fidelity
Fact-sheet fidelity
Content-policy compliance
Latency
Cost
```

A model must not be promoted merely because its writing sounds better.

---

## 8.8 Prompt Management

Prompts live under:

```text
config/prompts/
├── classify_story.v1.txt
├── extract_claims.v1.txt
├── summarize.v1.txt
├── research.v1.txt
├── fact_sheet.v1.txt
├── instagram.v1.txt
└── x_post.v1.txt
```

AI runs record:

```text
prompt_version
model
provider
generation_settings
input_hash
output
timestamp
```

---

# 9. Content & Media Generation

## 9.1 Fact Sheet

The fact sheet is the central content-generation interface:

```text
FACT SHEET
├── headline
├── summary
├── verified_claims
├── disputed_claims
├── unverified_claims
├── evidence
├── timeline
├── entities
├── locations
├── context
├── counterclaims
├── confidence
├── risk
└── sources
```

All social content is generated from this representation.

---

## 9.2 Content Pipeline

```text
FACT SHEET
    │
    ├── Instagram Carousel
    ├── Instagram Caption
    ├── X Post
    ├── X Thread
    ├── Facebook
    ├── Telegram
    └── YouTube Shorts Script
```

Facts remain consistent across platforms.

Only presentation changes.

---

## 9.3 Content Rules

Generated content must:

* Preserve verified facts
* Preserve uncertainty
* Avoid invented quotations
* Avoid invented statistics
* Avoid invented sources
* Preserve legal status
* Distinguish allegations from findings
* Avoid converting speculation into fact
* Avoid removing essential context solely for virality

---

## 9.4 Media

```text
MediaAsset
├── id
├── type
├── source
├── storage_location
├── public_url
├── checksum
├── dimensions
├── duration
├── generation_model
├── generation_prompt_version
├── review_status
└── metadata
```

---

## 9.5 Image Generation

```text
Research
    ↓
Fact Sheet
    ↓
Image Brief
    ↓
AI Image Generation
    ↓
Visual Review
    ↓
Publication
```

Generated visuals must not present speculation as authentic evidence.

---

# 10. Social Publishing Platform

## 10.1 Platform Adapter Architecture

Every platform uses an adapter.

```text
SocialAdapter
├── InstagramAdapter
├── XAdapter
├── FacebookAdapter
├── TelegramAdapter
└── FutureAdapter
```

Application code should not contain platform-specific publishing logic outside adapters.

---

## 10.2 Instagram

```text
Content
    ↓
InstagramRenderer
    ↓
Media Preparation
    ↓
Public Media Hosting
    ↓
Instagram Container
    ↓
Publish
    ↓
Verify
    ↓
Store Publication ID
```

Support:

* Images
* Videos
* Reels
* Carousels
* Captions
* Publication status
* Retry handling
* Error handling

Platform limits and permissions must remain configurable.

---

## 10.3 X

```text
Content
    ↓
XRenderer
    ↓
Media Upload
    ↓
Create Post
    ↓
Verify
    ↓
Store Post ID
```

Support:

* Posts
* Threads
* Replies
* Media
* Verification
* Rate-limit handling
* Retry handling

---

## 10.4 Publication Model

```text
Publication
├── id
├── story_id
├── content_variant_id
├── platform
├── account
├── scheduled_at
├── published_at
├── external_id
├── status
├── error
└── metadata
```

Statuses:

```text
DRAFT
PENDING_REVIEW
APPROVED
SCHEDULED
PUBLISHING
PUBLISHED
FAILED
RETRYING
CANCELLED
```

---

## 10.5 Media Hosting

Internal POCO storage is not sufficient as a public media endpoint.

Architecture:

```text
POCO
├── Database
├── Processing
├── AI
└── Internal Media

External Media/Object Storage
└── Public Publishing URLs
```

---

# 11. Human Review & Quality Gates

## 11.1 Quality Gate

```text
CONTENT
   │
   ├── FACT CHECK
   ├── SOURCE CHECK
   └── STYLE CHECK
          ↓
     SENSITIVE?
       │
    ┌──┴───┐
   YES     NO
    │       │
 HUMAN    AUTO
 REVIEW   REVIEW
    │       │
    └──┬────┘
       ↓
    PUBLISH
```

---

## 11.2 Quality Checks

Before publication:

```text
Facts correct?
Sources present?
Claims supported?
Legal status correct?
Quotes verified?
Numbers verified?
Date verified?
Location verified?
No hallucinated evidence?
No fabricated quotation?
No unsupported attribution?
No accidental group generalization?
Style compliant?
Risk acceptable?
```

---

## 11.3 Review Queue

Example:

```text
REVIEW QUEUE

HIGH RISK
India / Religious violence

Confidence: 71%
Sources: 7
Primary: 2
Contradictory evidence: 1

[Research]
[Edit]
[Approve]
[Reject]
```

Reviewers must see the evidence packet.

---

## 11.4 Review Decisions

Record:

```text
reviewer
decision
reason
timestamp
edited_fields
evidence_changes
risk_override
```

Possible decisions:

```text
APPROVE
REJECT
REQUEST_MORE_RESEARCH
REQUEST_EDIT
HOLD
```

---

## 11.5 Review Priority

Prioritize using:

```text
publication_risk
importance
confidence
controversy
sensitivity
breaking_news_score
```

High risk + low confidence should receive the highest review priority.

---

## 11.6 Autonomous Publishing

Autonomous publishing may eventually be permitted for narrowly defined low-risk content.

Potential low-risk examples:

```text
Weather
Official schedules
Verified routine data
Routine official announcements
```

Human review remains mandatory for high-risk categories:

```text
War
Religion
Communal violence
Criminal allegations
Election fraud
Terrorism
Historical controversy
SC/ST allegations
Major political accusations
```

Urgency must not bypass review.

---

# 12. Infrastructure, Deployment & Operations

## 12.1 Deployment

Initial deployment:

```text
POCO F1
    ↓
postmarketOS
    ↓
Docker / Native Services
    ↓
PostgreSQL
Redis
FastAPI
Workers
llama.cpp
```

Deployment should remain simple enough to debug over SSH.

---

## 12.2 Remote Administration

Use:

```text
Tailscale
```

Administrative interfaces should not be unnecessarily exposed to the public internet.

---

## 12.3 Resource Priority

Recommended POCO resource priority:

```text
1. PostgreSQL
2. Redis
3. API
4. Collector
5. Processor
6. Local AI
7. Publisher
8. Analytics
```

AI must not starve core infrastructure.

---

## 12.4 Jobs

Long-running work uses jobs:

```text
Job
├── id
├── type
├── payload
├── status
├── attempts
├── max_attempts
├── started_at
├── completed_at
├── error
└── metadata
```

Workers must support retries and recovery.

---

## 12.5 Idempotency

Examples:

```text
Same article received 10 times
        ↓
One normalized article
```

and:

```text
Publisher timeout
        ↓
Retry
        ↓
No accidental duplicate publication
```

Use idempotency keys where supported.

---

## 12.6 Failure Handling

External operations must handle:

```text
Timeout
Rate limit
Authentication failure
Network failure
Malformed response
Provider outage
Duplicate response
Partial success
```

Use exponential backoff where appropriate.

---

## 12.7 Configuration

Separate:

```text
development
testing
production
```

Production secrets must never be committed.

---

## 12.8 Source Configuration

Example:

```yaml
source:
  id: example-news
  type: rss
  url: "..."
  tier: 2
  reliability: 0.82
  enabled: true
  categories:
    - geopolitics
    - india
```

---

# 13. Security, Privacy & Reliability

## 13.1 Secrets

Secrets must never appear in:

* Git
* Plaintext database fields
* Logs
* Prompts
* Frontend bundles
* Source code

Use:

```text
Environment secrets
Encrypted credential storage
Token rotation
Least privilege
Secure sessions
Audit logs
```

---

## 13.2 Privacy

Classify AI tasks:

```text
PUBLIC
INTERNAL
SENSITIVE
```

Sensitive information should not be sent to cloud providers unnecessarily.

---

## 13.3 Security Architecture

Primary principle:

```text
Internet
   X
   │
Tailscale / controlled ingress
   ↓
Admin / Services
   ↓
POCO
```

Only required public endpoints should be exposed.

---

## 13.4 Audit Log

Record:

```text
article_created
story_clustered
claim_modified
evidence_added
fact_check_changed
content_generated
content_edited
content_approved
content_rejected
publication_attempted
publication_succeeded
publication_failed
editorial_rule_changed
```

---

## 13.5 Data Retention

Retain enough information to reproduce publication decisions:

```text
Original source metadata
Article version
Story cluster
Claims
Evidence
Fact Sheet
AI run
Generated content
Human edits
Approval
Publication result
```

---

# 14. Testing, Evaluation & Observability

## 14.1 Testing Layers

### Unit

```text
URL normalization
Deduplication
Scoring
Claim parsing
Evidence scoring
Risk classification
Content validation
Platform formatting
```

### Integration

```text
PostgreSQL
Redis
Collector
AI provider
Research engine
Publisher
```

### End-to-End

```text
RSS article
    ↓
Story
    ↓
Claims
    ↓
Evidence
    ↓
Fact Sheet
    ↓
Content
    ↓
Review
    ↓
Approval
    ↓
Mock publication
```

External social APIs should normally be mocked or staged during automated tests.

---

## 14.2 Structured Logging

Important operations record:

```text
timestamp
service
job_id
story_id
article_id
model
provider
duration
status
error
```

---

## 14.3 Metrics

Track:

```text
articles_per_hour
stories_created
claims_extracted
research_jobs
research_failures
average_inference_latency
queue_depth
publication_success_rate
publication_failure_rate
fact_check_confidence
human_rejection_rate
```

---

## 14.4 Operational Health

The existing POCO `health` command should eventually become:

```text
NEWS AI SERVER HEALTH

SYSTEM
├── CPU
├── RAM
├── Disk
├── Temperature
└── Uptime

NETWORK
├── Wi-Fi
├── Internet
└── Tailscale

SERVICES
├── PostgreSQL
├── Redis
├── API
├── Collector
├── Processor
├── AI Worker
├── Publisher
└── Scheduler

AI
├── Local Model
├── Inference Latency
├── Queue Depth
└── Failures

JOBS
├── Pending
├── Running
├── Failed
└── Retrying

SOCIAL
├── Instagram
├── X
└── Other Adapters
```

---

# 15. MVP, Roadmap & Source of Truth

## 15.1 MVP

The first usable system is:

```text
RSS / News
    ↓
Collect
    ↓
Normalize
    ↓
Deduplicate
    ↓
Classify
    ↓
Score
    ↓
Research
    ↓
Evidence
    ↓
Fact Sheet
    ↓
Instagram Content
    ↓
Quality Check
    ↓
Human Approval
    ↓
Publish
```

---

## 15.2 MVP Infrastructure

```text
ONE POCO
+
ONE DATABASE
+
ONE QUEUE
+
ONE LOCAL LLM
+
ONE CLOUD LLM
+
NEWS COLLECTION
+
FACT ENGINE
+
INSTAGRAM
+
HUMAN REVIEW
```

---

## 15.3 Implementation Order

### Phase 1 — Foundation

```text
01 Repository skeleton
02 Configuration system
03 PostgreSQL schema
04 SQLAlchemy models
05 Redis Streams
06 FastAPI
```

### Phase 2 — News Intelligence

```text
07 Collector framework
08 RSS collector
09 Article normalizer
10 Story clustering
11 Editorial taxonomy
```

### Phase 3 — AI

```text
12 Local AI abstraction
13 llama.cpp integration
14 AI router
15 Claim extraction
16 Evidence model
```

### Phase 4 — Research

```text
17 Research worker
18 Fact-check engine
19 Fact-sheet generator
```

### Phase 5 — Publishing

```text
20 Content engine
21 Quality gate
22 Instagram adapter
23 Scheduler
24 Publication tracking
```

### Phase 6 — Operations

```text
25 Health monitoring
26 Observability
27 Reliability improvements
28 Security hardening
```

---

## 15.4 Post-MVP

After the core vertical slice works:

```text
Historical Research Engine
        ↓
X Adapter
        ↓
Additional Collectors
        ↓
Telegram
        ↓
Facebook
        ↓
YouTube
        ↓
Automated Image Generation
        ↓
Video / Shorts
        ↓
Analytics
        ↓
Audience Optimization
        ↓
Knowledge Graph
```

---

## 15.5 Deliberately Deferred

Do not initially build:

```text
Kubernetes
Kafka
Multi-region deployment
20+ microservices
Complex vector database
Large autonomous local models
Autonomous sensitive-news publishing
Dozens of social platforms
Complex recommendation engine
Large-scale analytics infrastructure
```

---

## 15.6 Development Philosophy

Build vertically.

Preferred:

```text
RSS
 ↓
Article
 ↓
Story
 ↓
Claims
 ↓
Evidence
 ↓
Fact Sheet
 ↓
Social Content
 ↓
Human Review
 ↓
Publish
```

Do not build the entire database, AI system, frontend, publisher and analytics platform independently and integrate them only at the end.

---

## 15.7 Success Criteria

The MVP is successful when it can reliably:

1. Discover a news story.
2. Normalize the source.
3. Detect duplicates.
4. Cluster related coverage.
5. Extract claims.
6. Research evidence.
7. Detect contradictory evidence.
8. Produce a fact sheet.
9. Assign confidence.
10. Generate platform-specific content.
11. Run quality checks.
12. Route sensitive content to human review.
13. Publish approved content.
14. Record publication status.
15. Preserve complete evidence lineage.
16. Correct previously published information.

---

## 15.8 Canonical Source-of-Truth Rule

This document is the **top-level architecture source of truth**.

When an architectural decision changes:

```text
Decision
    ↓
Update ARCHITECTURE.md
    ↓
Update implementation
    ↓
Update tests
    ↓
Record decision if significant
```

Do not maintain conflicting architecture documents.

Recommended location:

```text
docs/ARCHITECTURE.md
```

Supporting specifications:

```text
docs/
├── ARCHITECTURE.md
├── DATA_MODEL.md
├── AI_ARCHITECTURE.md
├── EDITORIAL_POLICY.md
├── FACT_CHECKING.md
├── HISTORICAL_RESEARCH.md
├── SOCIAL_PUBLISHING.md
├── DEPLOYMENT.md
└── OPERATIONS.md
```

`ARCHITECTURE.md` remains the top-level source of truth.

---

# 16. Final Architecture Summary

```text
                         ┌────────────────────┐
                         │   NEWS / SOCIAL    │
                         │       SOURCES      │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │    COLLECTION      │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │ NORMALIZATION /    │
                         │ DEDUPLICATION      │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │   STORY ENGINE     │
                         └─────────┬──────────┘
                                   ↓
                    ┌──────────────┴──────────────┐
                    ↓                             ↓
          ┌──────────────────┐          ┌──────────────────┐
          │ EVIDENCE ENGINE  │          │ EDITORIAL ENGINE │
          └────────┬─────────┘          └────────┬─────────┘
                   └──────────────┬──────────────┘
                                  ↓
                         ┌────────────────────┐
                         │  AI ORCHESTRATOR   │
                         │                    │
                         │ Local / Cloud /    │
                         │ Search             │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │    FACT SHEET      │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │  CONTENT ENGINE    │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │   QUALITY GATE     │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │   HUMAN REVIEW     │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │ SOCIAL PUBLISHERS  │
                         └─────────┬──────────┘
                                   ↓
                         ┌────────────────────┐
                         │     ANALYTICS      │
                         └────────────────────┘

                PostgreSQL = Source of Truth
                Redis = Event Bus
                POCO = Initial Compute Platform
                Evidence = Factual Authority
                Human = Final Sensitive-Content Gate
```

---

# END OF CANONICAL ARCHITECTURE
