# News AI Social Media Manager

# CANONICAL_CONTRACTS.md

**Status:** Canonical
**Document Role:** Cross-document source of truth for shared terms, state machines, invariants, ownership boundaries, and precedence.
**Last updated:** 2026-09-09

---

# 1. Purpose

This document prevents semantic drift between the domain documents in `docs/`.

Each domain document remains authoritative for its own subject area. This document is authoritative for concepts that are shared by more than one domain document.

When an explanatory example in another document conflicts with a shared contract defined here, this document wins until the affected domain documents are updated together.

---

# 2. Documentation Ownership

| Concern | Canonical owner |
| --- | --- |
| Overall system boundaries and end-to-end architecture | `ARCHITECTURE.md` |
| PostgreSQL entities, relationships, persistence, and durable state | `DATA_MODEL.md` |
| Redis Streams events, envelopes, delivery, retries, and consumer contracts | `EVENTS.md` |
| AI providers, routing, prompts, structured output execution, provenance, and model lifecycle | `AI_PLATFORM.md` |
| Editorial priorities, evidence-aware framing, sensitive-topic policy, and content rules | `CONTENT_AND_EDITORIAL.md` |
| Source registry/roles, discovery, research planning, evidence acquisition, corroboration, contradictions, and historical research methodology | `SOURCE_AND_RESEARCH.md` |
| Application-layer structured Pydantic/JSON contracts | `CONTENT_SCHEMAS.md` |
| Social adapters, publication state, scheduling, retries, and platform constraints | `SOCIAL_PUBLISHING.md` |
| FastAPI HTTP boundary, endpoint behavior, authorization expectations, and API-level state validation | `API_SPEC.md` |
| Infrastructure, POCO deployment, runtime, networking, storage, backups, and infrastructure design | `INFRASTRUCTURE_AND_DEPLOYMENT.md` |
| Day-to-day operations, health checks, deployment execution, incident response, and recovery procedures | `OPERATIONS_RUNBOOK.md` |
| Test strategy, evaluation, release gates, and rollback criteria | `TESTING_AND_EVALUATION.md` |
| Shared enums, cross-document state semantics, precedence, and invariants | `CANONICAL_CONTRACTS.md` |

A document may repeat a concept for context, but repetition outside the canonical owner is non-normative unless explicitly stated otherwise.

---

# 3. Fundamental Editorial Invariant

The system must preserve:

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
  ↓
CONTENT
```

Editorial preference may select attention, prioritization, context, tone, and format.

Editorial preference must not determine factual conclusions.

AI output is not evidence merely because one or more models agree with it.

---

# 4. Persistent-State Ownership

Canonical rule:

```text
PostgreSQL = durable source of truth
Redis Streams = event transport and work delivery
```

Redis must not be the only location containing durable business state.

The following remain authoritative in PostgreSQL:

```text
stories
claims
evidence
fact checks
fact sheets
review state
content
publications
publication attempts
jobs
job attempts
external post IDs
audit history
```

If Redis is lost and PostgreSQL survives, the system must retain durable business state and be recoverable.

---

# 5. Claim Verification vs Fact-Check Verdict

These are separate concepts and MUST NOT share one enum.

## 5.1 ClaimVerificationStatus

A claim's verification workflow status is:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

Meanings:

- `UNASSESSED` — evidence evaluation has not completed.
- `SUPPORTED` — the material proposition is supported by sufficient evidence under current policy.
- `PARTIALLY_SUPPORTED` — a material portion is supported, but part remains unsupported, qualified, or contradicted.
- `DISPUTED` — material credible evidence conflicts and the disagreement is unresolved.
- `UNVERIFIED` — current evidence is insufficient to establish or refute the proposition.
- `REFUTED` — sufficient evidence establishes that the proposition, as stated, is not supported.

`UNVERIFIED` is not `REFUTED`.

## 5.2 FactCheckLabel

A fact-check verdict is:

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

The critical invariant is:

```text
UNVERIFIED != FALSE
```

A fact-check label may be assigned to a checked claim, statement, post, media item, or composite assertion after evaluation. It is not the same thing as the claim-processing state.

## 5.3 Event Field Semantics

`fact_check.completed` MUST publish a `label` using `FactCheckLabel` when a verdict exists.

Example:

```json
{
  "story_id": "uuid",
  "fact_check_id": "uuid",
  "label": "PARTIALLY_TRUE",
  "confidence_score": 0.78,
  "review_required": true
}
```

It must not publish a `ClaimVerificationStatus` value in a field presented as the fact-check verdict.

---

# 6. Confidence

Confidence is an evidence assessment.

A value such as:

```text
0.82
```

must not be described as an objective 82% probability of truth unless the score has been explicitly calibrated and validated for that interpretation.

Model agreement alone does not increase factual confidence.

---

# 7. Risk and Sensitivity

Risk and topic sensitivity are separate dimensions.

## 7.1 RiskLevel

The canonical risk enum is:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

## 7.2 SensitiveTopic

Sensitivity is represented independently, for example:

```text
sensitive_topic = true
sensitive_categories = [COMMUNAL_VIOLENCE, CRIMINAL_ALLEGATION]
risk_level = HIGH
```

A sensitive category may affect minimum verification and review requirements.

Risk must not be inferred from editorial importance.

---

# 8. Publication Approval Policy

The publication system has an approval gate before external publication.

Canonical model:

```text
CONTENT
   ↓
QUALITY CHECK
   ↓
APPROVAL GATE
   ↓
SCHEDULE / PUBLISH
```

## 8.1 MVP policy

For the MVP and brainstorming implementation phase:

```text
ALL external social publication requires explicit human approval.
```

This applies even to low-risk content.

Quality checks may pass automatically, but passing quality checks does not itself authorize publication.

## 8.2 Future low-risk automation

A future release MAY allow automatic approval for low-risk content only if all of the following are true:

```text
explicit feature flag enabled
publishing policy permits it
risk = LOW
topic is not subject to mandatory human review
quality checks pass
fact sheet is eligible
account policy permits it
full audit trail is retained
```

This future capability is disabled by default and is not part of the MVP publication policy.

## 8.3 Mandatory human-review categories

Sensitive/high-risk categories defined by editorial and risk policy must never use low-risk auto-approval.

Examples include, at minimum, material claims involving:

```text
communal violence
religious accusations
individual criminal allegations
sexual assault
terrorism attribution
war casualty claims
election fraud
SC/ST allegations
caste-related accusations
blasphemy allegations
unverified breaking news
```

---

# 9. Review State vs Publication State

Review state and publication state are separate state machines.

## 9.1 ReviewState

Canonical review states:

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

Approval must identify the reviewed artifact/version.

A later factual change that materially alters the artifact invalidates the previous approval and requires a new review under policy.

## 9.2 PublicationStatus

Canonical publication states:

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

Post-publication metadata may additionally describe:

```text
UPDATED
CORRECTED
ARCHIVED
```

These post-publication descriptors do not erase the original publication audit trail.

---

# 10. Story Verification Semantics

`story.verified` means:

```text
the configured verification stage completed for the story
```

It does NOT mean:

```text
every claim in the story is true
```

Each claim retains its own `ClaimVerificationStatus`.

A verified story may therefore contain:

```text
SUPPORTED claims
PARTIALLY_SUPPORTED claims
DISPUTED claims
UNVERIFIED claims
REFUTED claims
```

The Fact Sheet must preserve material uncertainty and contradiction.

---

# 11. Fact Sheet Boundary

The Fact Sheet is the canonical factual intermediate representation used for normal content generation.

Normal path:

```text
RAW SOURCES
    ↓
STORY
    ↓
CLAIMS
    ↓
EVIDENCE
    ↓
FACT SHEET
    ↓
EDITORIAL BRIEF / ANGLE
    ↓
CONTENT
```

The content engine must not normally bypass the Fact Sheet and ask a model to infer the final post directly from a pile of raw articles.

---

# 12. AI Provider vs Search Provider

AI model providers and research/search providers are separate abstractions.

Canonical boundaries:

```text
AIProvider
├── LocalLlamaProvider
├── LocalQwenProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
└── FutureProvider
```

and:

```text
SearchProvider
├── configured web/search provider
├── specialist research provider
└── future provider
```

An orchestrator may use both, but a search provider is not an `AIProvider` merely because AI may be used during research.

---

# 13. Source Independence

Article count is not independent-confirmation count.

```text
10 republished articles from one originating report
!=
10 independent confirmations
```

Source lineage, syndication, shared primary evidence, and independent reporting must be considered when assessing evidence strength.

---

# 14. Historical Research Invariant

Contested historical questions must be decomposed into evidence domains rather than forced into predetermined ideological binaries.

Relevant domains include:

```text
linguistics
archaeology
genetics
literary evidence
epigraphy
chronology
population movement
material culture
cultural transmission
political expansion
military conflict
```

Evidence in one domain must not automatically be represented as proof in another.

---

# 15. Religious and Civilizational Taxonomy

The shared umbrella is:

```text
INDIC_CIVILIZATIONAL_CONTEXT
├── Hindu Traditions
├── Buddhist Traditions
├── Jain Traditions
├── Sikh Traditions
├── Indigenous / Regional Traditions
└── Ancient Indian Cultural Traditions
```

This enables shared civilizational analysis without collapsing distinct religious identities into one category.

---

# 16. Allegation and Legal Status

The system must preserve material procedural distinctions such as:

```text
allegation
complaint
FIR
investigation
arrest
charge / charge sheet
prosecution
trial
court finding
conviction
acquittal
appeal
final judgment
```

An allegation must not be rewritten as an established act merely because an official accusation exists.

---

# 17. Demographic and Caste Invariants

Demographic analysis must distinguish:

```text
observed data
statistical interpretation
possible explanation
causal evidence
editorial interpretation
```

Population change does not itself establish communal intent, wrongdoing, or causation.

Caste-related facts may be reported when relevant and verified, but caste identity must not be used as a behavioral proxy or basis for collective guilt.

---

# 18. Media Storage vs Public Delivery

Media persistence and public delivery are separate concerns.

The MVP may store media locally on the POCO:

```text
/opt/news-ai/media/
```

If a platform requires a public URL, the selected asset must be made available through a deliberately exposed HTTPS media-delivery mechanism or object storage.

The application must never expose the database, admin API, internal service ports, backups, credentials, or arbitrary filesystem paths merely to make social media ingestion work.

---

# 19. POCO Deployment Model

For the initial POCO production deployment:

```text
native/OpenRC-managed services = default
Docker/containers = optional where practical
```

Docker is not an MVP requirement on the POCO.

Development environments may use containers when useful.

Application architecture must remain portable across both deployment styles.

---

# 20. Event and Database Relationship

Every worker should follow the durable-state pattern:

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

The transactional outbox pattern should be used where atomic state transition plus event emission is required.

---

# 21. Publication Idempotency

The social publishing layer must assume retries and ambiguous failures occur.

A timeout after a publish request must not trigger a blind duplicate post.

Canonical behavior:

```text
ambiguous platform response
        ↓
verify external state where possible
        ↓
resume / mark published / block
        ↓
retry only when safe
```

---

# 22. Structured Contract Boundary

`CONTENT_SCHEMAS.md` owns application-layer Pydantic/JSON contracts.

Schemas must use the shared enums in this document and must not redefine business truth, persistence ownership, or publication policy.

`API_SPEC.md` may reference these schemas but must not create incompatible payload vocabularies.

---

# 23. Research Boundary

`SOURCE_AND_RESEARCH.md` owns source roles, research planning, search-provider abstraction, evidence acquisition, source independence, contradiction handling, and historical research methodology.

Research output must preserve the shared evidence/editorial separation and must not treat AI synthesis as evidence.

---

# 24. API Boundary

`API_SPEC.md` owns HTTP endpoint semantics and API-level state validation.

The API orchestrates domain services and asynchronous work. It does not become an alternate source of business-state truth or bypass the Fact Sheet/review/publication gates.

---

# 25. Operations Boundary

`OPERATIONS_RUNBOOK.md` owns operator procedures.

It may describe how to inspect, pause, recover, deploy, or roll back the system, but it must not redefine infrastructure architecture or domain lifecycle semantics.

---

# 26. Cross-Document Change Rule

Any change to a shared concept must update this file first or in the same change set.

Shared concepts include:

```text
claim verification states
fact-check labels
risk levels
review semantics
publication approval semantics
publication states
story verification semantics
PostgreSQL/Redis ownership
Fact Sheet boundary
AI/Search provider boundary
```

A new domain document must reference these contracts rather than inventing alternate enums or lifecycle meanings.

---

# 27. Final Invariant

The system must remain implementable as:

```text
DISCOVERY
    ↓
RESEARCH
    ↓
CLAIMS
    ↓
EVIDENCE
    ↓
VERIFICATION
    ↓
FACT SHEET
    ↓
EDITORIAL INTERPRETATION
    ↓
CONTENT
    ↓
QUALITY CHECK
    ↓
APPROVAL
    ↓
PUBLICATION
    ↓
ANALYTICS
```

No subsystem may bypass the evidence boundary or publication approval policy by redefining a shared term locally.
