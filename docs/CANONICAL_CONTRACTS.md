# News AI Social Media Manager

# CANONICAL_CONTRACTS.md

**Status:** Canonical
**Document Role:** Cross-document source of truth for shared terms, state machines, invariants, ownership boundaries, configuration ownership, runtime portability, and precedence.
**Last updated:** 2026-09-09

---

# 1. Purpose

This document prevents semantic drift between the domain documents in `docs/`.

Each domain document remains authoritative for its own subject area. This document is authoritative for concepts shared by more than one domain document.

If an illustrative example in another document conflicts with a shared contract here, this document wins. Domain documents should nevertheless be updated promptly so stale examples do not remain in the repository.

---

# 2. Documentation Ownership

| Concern | Canonical owner |
| --- | --- |
| Overall system boundaries and end-to-end architecture | `ARCHITECTURE.md` |
| PostgreSQL entities, relationships, persistence, and durable state | `DATA_MODEL.md` |
| Redis Streams events, envelopes, delivery, retries, and consumer contracts | `EVENTS.md` |
| AI providers, routing, prompts, structured output, provenance, and model lifecycle | `AI_PLATFORM.md` |
| Source registry, research workflow, evidence acquisition, corroboration, source independence, and research provenance | `SOURCE_AND_RESEARCH.md` |
| Editorial priorities, evidence-aware framing, sensitive-topic policy, and content rules | `CONTENT_AND_EDITORIAL.md` |
| Cross-service Pydantic/JSON artifact contracts | `CONTENT_SCHEMAS.md` |
| Infrastructure, runtime portability, deployment, networking, storage, backups, and platform adapters | `INFRASTRUCTURE_AND_DEPLOYMENT.md` |
| Social adapters, publication state, scheduling, retries, and platform constraints | `SOCIAL_PUBLISHING.md` |
| HTTP API boundary and asynchronous orchestration | `API_SPEC.md` |
| Test strategy, evaluation, release gates, and rollback criteria | `TESTING_AND_EVALUATION.md` |
| Day-to-day operation, health, incidents, deployment execution, and recovery | `OPERATIONS_RUNBOOK.md` |
| Shared enums, cross-document state semantics, precedence, configuration ownership, and invariants | `CANONICAL_CONTRACTS.md` |

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
- `DISPUTED` — material credible evidence conflicts and the disagreement remains unresolved.
- `UNVERIFIED` — current evidence is insufficient to establish or refute the proposition.
- `REFUTED` — sufficient evidence establishes that the proposition, as stated, is not supported.

Critical invariant:

```text
UNVERIFIED != REFUTED
```

Legacy labels such as `partially_confirmed` are prohibited in current schemas and examples.

## 5.2 FactCheckLabel

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

A fact-check label may be assigned to a checked claim, statement, post, media item, or composite assertion after evaluation. It is not the same concept as the claim-processing state.

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

A value such as `0.82` must not be described as an objective 82% probability of truth unless the scoring system has been explicitly calibrated and validated for that interpretation.

Model agreement alone does not increase factual confidence.

---

# 7. Risk and Sensitivity

Risk and topic sensitivity are separate dimensions.

## 7.1 RiskLevel

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

For the MVP and current implementation phase:

```text
ALL external social publication requires explicit human approval.
```

This applies even to low-risk content.

Passing automated quality checks does not authorize publication.

Quality includes application-owned deterministic semantic validation over exact
persisted content and Fact Sheet artifacts. AI cannot override a deterministic
ERROR finding. WARNING findings alone do not fail quality. This validation never
changes factual status or grants AI evidence authority. Unknown or unrepresented
semantics are not guessed.

Downstream certainty must never exceed upstream supported certainty. Versioned
`certainty-policy-v1` derives typed claim-presentation ceilings from the immutable
Fact Sheet status and FactCheck label. Content must copy those inputs exactly.
UNVERIFIED is not FALSE or REFUTED; DISPUTED remains disputed; REFUTED claims
cannot be affirmatively presented. Deterministic metadata validation and AI prose
certainty assessment are separate: neither can raise certainty or change truth.
Only current deterministic FactCheckEngine pairs are authorized: SUPPORTED/TRUE,
PARTIALLY_SUPPORTED/PARTIALLY_TRUE, DISPUTED/UNVERIFIED, UNVERIFIED/UNVERIFIED,
REFUTED/FALSE. Every other pair fails closed under `certainty-policy-v1`; additional
canonical enum values require an explicit future versioned producer/policy change.

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

This capability is disabled by default and is not part of the MVP.

## 8.3 Mandatory human-review categories

Sensitive/high-risk categories defined by editorial and risk policy must never use low-risk auto-approval.

Examples include material claims involving:

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

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

Approval must identify the exact reviewed artifact/version.

A material factual/content change invalidates previous approval when policy requires it.

## 9.2 PublicationStatus

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

These descriptors do not erase the original publication audit trail.

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

A verification-completed story may contain:

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

The content engine must not normally bypass the Fact Sheet and ask a model to infer the final post directly from raw articles.

---

# 12. AI Provider vs Search Provider

AI model providers and research/search providers are separate abstractions.

```text
AIProvider
├── LocalLlamaProvider
├── LocalQwenProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
└── FutureProvider
```

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── SpecialistSearchProvider
└── FutureProvider
```

An orchestrator may use both. A search provider is not an `AIProvider` merely because AI may assist the research process.

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

The MVP may store media locally on the POCO, for example:

```text
/opt/news-ai/media/
```

If a platform requires a public URL, the selected asset must be made available through a deliberately exposed HTTPS media-delivery mechanism or object storage.

The application must never expose the database, admin API, internal service ports, backups, credentials, or arbitrary filesystem paths merely to satisfy social-platform media ingestion.

---

# 19. Runtime and Platform Independence

The application architecture is platform-independent.

Business/domain/application code MUST NOT directly invoke or depend on host-specific service utilities such as:

```text
rc-service
rc-update
systemctl
service
launchctl
sc.exe
PowerShell service cmdlets
```

Instead, runtime management uses a capability-based abstraction:

```text
newsctl / RuntimeController
        ↓
RuntimeDetector
        ↓
ServiceManager interface
        ↓
selected adapter based on available host capability
```

Conceptual adapters may include:

```text
OpenRCServiceManager
SystemdServiceManager
SysVServiceManager
LaunchdServiceManager
WindowsServiceManager
UnsupportedServiceManager
```

Platform-specific commands are isolated inside infrastructure/runtime adapters. They must not leak into collectors, processors, AI workers, editorial logic, publishing logic, domain models, or API routes.

Detection should use capabilities and runtime metadata rather than assuming a service manager solely from an OS name. Useful signals include:

```text
Python platform/os information
/etc/os-release where available
executable discovery such as shutil.which(...)
container/runtime metadata
explicit operator override
```

If no supported manager is detected, fail clearly or use an explicitly configured unmanaged/manual mode. Never guess and execute an arbitrary system command.

The current POCO happens to use OpenRC. That is a deployment profile, not an application architecture assumption.

---

# 20. Deployment Portability

For the initial POCO deployment:

```text
native services selected through runtime adapter = default
containers = optional where useful
```

Docker is not an MVP requirement and must not become an application dependency.

The same application must remain portable to other Linux distributions, macOS development hosts, Windows development hosts, containers, or future servers without rewriting business logic.

---

# 21. Configuration Ownership

Configuration is divided by responsibility. The same policy must not be defined in multiple directories.

## 21.1 `config/sources/` — source registry and collection mechanics

Owns descriptive and operational collection configuration such as:

```text
registry.yaml
feeds.yaml
collection.yaml
```

Typical fields:

```text
source identity
feed/API endpoints
enabled state
poll interval
collection method
parser/collector settings
transport/auth credential references
technical rate-limit handling
```

It does NOT determine whether a source is sufficient evidence for a claim.

## 21.2 `config/research/` — research and evidence methodology

Owns evidence/research policy such as:

```text
source-policy.yaml
search-policy.yaml
corroboration.yaml
fact-check.yaml
historical-research.yaml
```

Typical concerns:

```text
source hierarchy and role rules
minimum corroboration rules
source-independence evaluation
primary-source requirements
contradiction search requirements
fact-check methodology
historical evidence-domain methodology
research budgets/timeouts
```

These rules determine evidence methodology, not editorial preference.

## 21.3 `config/editorial/` — editorial preference and publication policy

Owns:

```text
priorities.yaml
taxonomy.yaml
content-style.yaml
risk-policy.yaml
publishing-policy.yaml
```

Typical concerns:

```text
coverage priority
editorial relevance
content tone/style
sensitive-topic routing
publication/review policy
```

Editorial configuration must not redefine source truth/evidence methodology.

## 21.4 Other configuration roots

```text
config/models/      → AI model/provider routing
config/prompts/     → versioned AI prompts
config/platforms/   → social-platform capabilities/constraints
```

Secrets remain outside ordinary versioned configuration and are referenced securely.

---

# 22. Configuration Precedence

Recommended effective configuration precedence:

```text
code defaults
  ↓
versioned configuration
  ↓
environment-specific overrides
  ↓
secure secret references
  ↓
explicit audited runtime administrative override where allowed
```

Two different files must not independently define the same canonical policy key.

A startup/config-validation check should reject ambiguous duplicate ownership where practical.

---

# 23. Event and Database Relationship

Every worker should follow:

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

Use a transactional outbox where atomic state transition plus event emission is required.

---

# 24. Publication Idempotency

The social publishing layer must assume retries and ambiguous failures occur.

A timeout after a publish request must not trigger a blind duplicate post.

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

# 25. Cross-Document Change Rule

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
runtime/platform abstraction
configuration ownership
```

A domain document must reference these contracts rather than inventing alternate enums, lifecycle meanings, service-manager assumptions, or duplicate configuration owners.

---

# 26. No-Legacy-Example Rule

Canonical documentation should not intentionally retain outdated illustrative examples after a contradiction has been identified.

Examples must use current enums, current directory layout, current event sequence, current approval semantics, and current runtime abstraction.

Historical Git commits provide the record of earlier designs; current docs should describe the current design only.

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

No subsystem may bypass the evidence boundary, publication approval policy, persistence boundary, configuration ownership, or runtime abstraction by redefining a shared term locally.
