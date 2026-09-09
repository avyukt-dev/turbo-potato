# News AI Social Media Manager

# SOURCE_AND_RESEARCH.md

**Status:** Canonical
**Document Role:** Source of truth for source registration, discovery, research planning, evidence acquisition, corroboration, contradiction handling, historical research, and research provenance.

Shared enums and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This document defines how the system discovers claims, researches them, acquires evidence, evaluates source independence, preserves contradictions, and produces evidence-backed research outputs for the Fact Sheet layer.

It does not redefine:

```text
persistent database structures      → DATA_MODEL.md
event envelopes and delivery        → EVENTS.md
AI providers and model routing       → AI_PLATFORM.md
editorial priorities and content     → CONTENT_AND_EDITORIAL.md
social publication execution         → SOCIAL_PUBLISHING.md
test strategy                        → TESTING_AND_EVALUATION.md
shared enums/lifecycle semantics     → CANONICAL_CONTRACTS.md
```

---

# 2. Research Principle

The system is claim-driven, not article-summary-driven.

Required flow:

```text
DISCOVERY
   ↓
STORY
   ↓
CLAIMS
   ↓
RESEARCH PLAN
   ↓
SOURCE SEARCH
   ↓
EVIDENCE
   ↓
CONTRADICTIONS / COUNTERCLAIMS
   ↓
CLAIM VERIFICATION
   ↓
FACT SHEET
```

The system must not ask an AI model to read a pile of articles and decide what happened without explicit claim and evidence structure.

---

# 3. Source Registry

Every configured source should have a stable source record where practical.

The registry should capture:

```text
identity
publisher/domain
source role
country/region
language
collection method
default authority level
topical coverage
enabled/disabled status
access metadata
policy metadata
```

Persistence belongs to `sources` and `source_feeds` as defined by `DATA_MODEL.md`.

Source behavior should be configuration-driven under `config/sources/` rather than scattered through collector code.

---

# 4. Source Hierarchy

The canonical source hierarchy remains compatible with `CONTENT_AND_EDITORIAL.md`.

## Level 1 — Primary

Examples:

```text
court judgments and orders
government notifications and documents
parliamentary records
official statistics
official diplomatic statements
official military statements
police documents and statements
original research papers
original datasets
treaties
archaeological reports
inscriptions
original recordings/transcripts
```

Primary means direct/original to the proposition or institutional act. It does not mean automatically correct.

## Level 2 — Established Secondary

Examples:

```text
major newspapers
international news agencies
established broadcasters
peer-reviewed academic publications
specialist publications
```

## Level 3 — Specialist / Research / Investigative

Examples:

```text
think tanks
research organizations
investigative journalism
subject-matter specialists
specialist databases
academic commentary
```

## Level 4 — Discovery

Examples:

```text
X
Reddit
Telegram
Instagram
YouTube
Facebook
blogs
forums
```

Discovery sources may reveal claims, eyewitness material, leads, documents, or emerging events. They generally must not be the sole evidentiary basis for serious/high-risk factual claims.

---

# 5. Discovery Source vs Evidence Source

A discovery source and an evidence source are different roles.

Example:

```text
social post reports alleged court order
        ↓
discovery lead
        ↓
search official court source
        ↓
obtain judgment/order
        ↓
primary evidence
```

The social post remains part of provenance but must not be silently upgraded to primary evidence.

A single source may play different roles for different claims.

---

# 6. Source Authority, Reliability, and Relevance

Source evaluation should keep separate dimensions:

```text
authority
reliability
directness
relevance
recency
specificity
independence
provenance quality
```

Do not compress these into a single notion of “trusted source.”

A Level 1 source can be incomplete, mistaken, self-interested, preliminary, or later corrected.

A Level 4 source can occasionally contain authentic first-hand material, but it still requires provenance and verification.

---

# 7. Source Independence

Article count is not confirmation count.

Canonical invariant:

```text
10 republished articles from one originating report
!=
10 independent confirmations
```

The research layer should identify, where possible:

```text
original reporting source
wire/agency origin
syndication
copying / republication
citation dependency
shared primary document
shared eyewitness
independent field reporting
independent primary evidence
```

The system should model source lineage heuristically rather than pretend it can always prove statistical independence.

---

# 8. Story Clustering and Research Boundary

Clustering determines which articles belong to one story.

Research begins after or during clustering when material claims emerge.

Research must not assume that every article inside a story cluster supports every claim.

Evidence is linked claim-by-claim.

---

# 9. Claim Extraction Requirements

Claims should be atomic enough to evaluate independently.

Avoid:

```text
Country X attacked location Y and is preparing for a wider war.
```

Prefer:

```text
Country X launched missiles at location Y.
Official A reported Z casualties.
Country X described the action as retaliation.
Analyst B said wider escalation is possible.
```

Claims should preserve whether they are:

```text
factual assertions
allegations
quotes
estimates
predictions
causal claims
correlations
legal-status claims
historical interpretations
```

---

# 10. Claim Verification Status

Research uses the canonical `ClaimVerificationStatus` from `CANONICAL_CONTRACTS.md`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

These are not fact-check verdict labels.

Critical distinction:

```text
UNVERIFIED != REFUTED
```

---

# 11. Fact-Check Verdicts

When the system performs a formal fact check, the verdict uses `FactCheckLabel` from `CANONICAL_CONTRACTS.md`:

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

Research insufficiency must not be converted into falsity.

---

# 12. Research Planning

For each material claim, research should generate a plan before broad searching.

A plan may include:

```text
claim text
claim type
entities
location
time window
primary-source targets
secondary-source targets
specialist-source targets
contradiction queries
language variants
regional spellings/transliterations
legal/document identifiers
historical evidence domains
research budget
```

The plan should be inspectable and versionable where material.

---

# 13. Search Provider Abstraction

Research/search is separate from AI-provider routing.

Conceptual interface:

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── SpecialistSearchProvider
└── FutureProvider
```

Application services request research/search capabilities rather than hard-code provider SDKs.

Search providers may return candidate sources. They do not determine factual truth.

`AIProvider` remains owned by `AI_PLATFORM.md`.

---

# 14. Query Planning

Query planning should use multiple query families where appropriate:

```text
exact claim query
entity + event query
official-source query
primary-document query
contradiction query
counterclaim query
local-language query
historical-source query
academic/scholarly query
```

Breaking news may require repeated queries across time.

Historical research may require separate queries per evidence domain.

---

# 15. Temporal Correctness

Research must distinguish:

```text
event time
publication time
retrieval time
last-updated time
source correction time
```

A later article may describe an older event.

A newer article is not automatically stronger evidence than an older primary source.

For breaking stories, stale reporting must not override newer authoritative updates simply because it appears frequently in search results.

---

# 16. Source Versioning and Preservation

Where a source can change after publication, preserve enough information to reconstruct what was reviewed.

Use:

```text
article_versions
content hash
retrieval timestamp
source metadata
archive reference where lawful/available
```

Do not silently replace a previously reviewed version with a later edited version.

---

# 17. Evidence Acquisition

For each material claim, research should seek, in order appropriate to the claim:

```text
primary evidence
independent established reporting
specialist/context sources
contradictory evidence
counterclaims
```

Primary evidence should be prioritized when it directly addresses the proposition.

The system must retain evidence that weakens the preferred editorial angle.

---

# 18. Evidence Directness

Evidence may be:

```text
direct support
indirect support
contradiction
qualification
context
```

A source merely discussing the same topic is not necessarily evidence for the claim.

Evidence relationships are persisted through `claim_evidence` as defined by `DATA_MODEL.md`.

---

# 19. Evidence Strength

Evidence strength may consider:

```text
source authority
directness
specificity
independence
corroboration
recency
authenticity/provenance
methodological quality
contradictory evidence
```

A numeric score is an internal assessment unless calibrated. It must not be described as objective probability of truth.

---

# 20. Contradictory Evidence

Contradictory evidence must remain first-class.

The system should preserve whether evidence:

```text
supports
contradicts
qualifies
contextualizes
```

Credible disagreement should produce `DISPUTED`, `PARTIALLY_SUPPORTED`, or another evidence-appropriate state rather than being discarded.

A contradiction is not automatically sufficient to mark a claim `REFUTED`.

---

# 21. Counterclaims

Counterclaims should be researched as claims, not merely copied into a “both sides” field.

Evaluate:

```text
who made the counterclaim
what exactly it asserts
its evidence
its independence
its chronology
its relevance
```

A counterclaim is not automatically equally credible merely because it exists.

---

# 22. Breaking News Protocol

Breaking news receives higher processing priority, not lower evidence standards.

Recommended protocol:

```text
collect rapidly
extract provisional claims
mark uncertain claims explicitly
seek primary/official updates
seek independent reporting
track contradictions
re-run research as new evidence appears
version the Fact Sheet
require human approval before external publication in MVP
```

Preliminary reports must remain labeled as preliminary.

Do not convert “reports say” into established fact until evidence supports it.

---

# 23. Sensitive Topic Enhanced Research

Material claims involving the mandatory-review categories in `CANONICAL_CONTRACTS.md` require enhanced research.

At minimum:

```text
explicit claim decomposition
primary-source search
independent corroboration search
contradiction search
procedural/legal-status check where relevant
source-lineage check
risk assessment
human review
```

The research system may escalate additional categories through configuration.

---

# 24. Legal and Criminal Allegations

The system must preserve procedural status:

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

Research should seek the most authoritative available legal/official record.

Do not transform an accusation, FIR, arrest, or charge into proof of guilt.

---

# 25. SC/ST Act Research

When the SC/ST Act is material:

```text
identify the exact allegation
identify procedural status
identify cited statutory provisions where available
seek FIR/court/order/official record where lawful and available
separate allegation from finding
preserve later procedural developments
```

Do not generalize one case to a caste/community.

Do not infer caste identity when not established by reliable sources.

---

# 26. Communal and Religious Violence

Research should distinguish:

```text
confirmed event
reported motive
alleged motive
identity of actors where established
identity of victims where established
official attribution
independent attribution
unverified social claims
casualty figures
later corrections
```

Do not infer collective responsibility from the identity of individuals.

---

# 27. Terrorism Attribution

Terrorism attribution should distinguish:

```text
claim of responsibility
official attribution
intelligence assessment
media attribution
court/legal finding
independent corroboration
```

A group's claim of responsibility is evidence of the claim, not automatically conclusive proof of operational responsibility.

---

# 28. War Casualties and Conflict Statistics

Casualty and battlefield claims require explicit source attribution.

Record:

```text
who reported the number
whether it is official/estimated/independently verified
civilian/combatant definition
geographic scope
time period
methodology where known
known conflicting counts
```

Do not merge incompatible casualty definitions into one number.

---

# 29. Demographic and Statistical Research

Demographic claims must preserve:

```text
dataset
year/time range
geographic scope
population definition
sample or census basis
methodology
uncertainty
comparison period
```

Separate:

```text
observed data
statistical interpretation
possible explanations
causal evidence
editorial interpretation
```

Population change does not itself establish communal intent or wrongdoing.

---

# 30. Historical Research Engine

Historical research must represent separate evidence domains.

Canonical conceptual model:

```text
Historical Question / Event
├── chronology
├── geography
├── primary texts
├── archaeology
├── inscriptions / epigraphy
├── literary sources
├── numismatics where relevant
├── linguistics
├── genetics
├── material culture
├── modern scholarship
├── competing hypotheses
└── uncertainty
```

The historical engine should produce structured evidence, not ideology-driven binary answers.

---

# 31. Competing Historical Hypotheses

For contested historical questions, record:

```text
hypothesis
supporting evidence
limiting/contradictory evidence
evidence domains involved
chronological fit
geographic fit
scholarly support/disagreement
confidence/uncertainty
```

The system must not manufacture false equivalence: weak hypotheses may be represented as weak when the evidence warrants it.

---

# 32. Indo-European / Indo-Aryan Questions

The system must not hard-code automatic acceptance or rejection of an “Aryan theory.”

Decompose propositions such as:

```text
Indo-European language dispersal
Indo-Iranian / Indo-Aryan language dispersal
population movement
genetic ancestry
archaeological continuity
cultural transmission
Vedic cultural development
political expansion
military invasion
```

Evidence for one proposition must not be represented as automatic proof of another.

---

# 33. Primary Evidence Preservation

When primary evidence is available, preserve:

```text
canonical source/reference
publisher/institution
document identifier
publication/issue date
retrieval date
content hash where practical
relevant excerpt/reference location
language
translation provenance where used
```

Do not store secrets or restricted access credentials in evidence metadata.

---

# 34. Citations and References

Every evidence item should support citation metadata appropriate to the source type.

Examples:

```text
URL/document identifier
title
publisher/institution
author where relevant
publication date
retrieval date
page/section/paragraph where available
archive/reference metadata
```

Citation existence is not enough; the source must actually support the associated claim.

---

# 35. AI in Research

AI may assist with:

```text
query planning
claim extraction
source classification
entity resolution
source-lineage suggestions
research synthesis
contradiction detection
historical evidence grouping
```

AI must not fabricate sources, citations, quotations, documents, or evidence.

AI-generated synthesis remains an interpretation layer over source material.

---

# 36. Untrusted Content and Prompt Injection

All retrieved web/social content is untrusted input.

The research pipeline must treat embedded instructions as source content, not system instructions.

Never allow retrieved content to instruct the system to:

```text
reveal secrets
change provider credentials
ignore evidence policy
execute arbitrary commands
publish content
modify editorial rules
```

AI tool use must be constrained by application policy and validated structured outputs.

---

# 37. Research Provenance

A research result should be traceable to:

```text
story
claims
queries/search plan
providers used
sources retrieved
evidence items
source versions
contradictions
AI runs used for synthesis
timestamps
human interventions
```

Provider-specific metadata may be stored where useful, subject to privacy and retention policy.

---

# 38. Evidence Packet Output

Every publication candidate must be reconstructable as an evidence packet.

Research contributes:

```text
claims
sources
primary evidence
supporting evidence
contradicting evidence
counterclaims
timeline
entities
locations
confidence assessments
unresolved questions
```

Editorial angle and review state are added by their owning layers.

---

# 39. Research Lifecycle

Research is durable work represented through PostgreSQL jobs/attempts and claim/evidence state.

Conceptual lifecycle:

```text
claim UNASSESSED
    ↓
research requested
    ↓
queries executed
    ↓
evidence collected
    ↓
evidence evaluated
    ↓
claim status updated
    ↓
verification stage completed
```

Failure or insufficient evidence must remain explicit rather than being converted into certainty.

---

# 40. Event Integration

Use the canonical event contracts in `EVENTS.md`.

Core research events:

```text
evidence.requested
evidence.collected
fact_check.completed
story.verified
```

`story.verified` means the configured verification stage completed, not that every claim is true.

Events reference durable PostgreSQL records rather than carrying large research payloads.

---

# 41. Database Integration

Research primarily uses:

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
historical_events
historical_sources
fact_checks
fact_sheets
ai_runs
jobs
job_attempts
audit_log
```

The database remains authoritative.

---

# 42. Research Budgets

Research must support configurable budgets for:

```text
maximum search calls
maximum sources per claim
maximum AI synthesis calls
time budget
provider cost budget
historical research depth
breaking-news refresh frequency
```

Budget exhaustion means research stops or escalates. It does not imply truth/falsity.

---

# 43. Caching

Cache:

```text
search results where provider terms permit
source fetch metadata
content hashes
unchanged documents
entity resolution
query results with freshness metadata
```

Do not allow cache freshness to override temporal correctness.

Redis may provide short-lived caching, but PostgreSQL remains the durable source of truth for business state.

---

# 44. Timeouts and Retries

Search/fetch failures should be classified as transient or permanent.

Use bounded retries with backoff.

Do not retry indefinitely.

A source being temporarily unavailable must not be silently treated as evidence against a claim.

---

# 45. Human Escalation

Escalate when:

```text
credible sources materially conflict
primary evidence cannot be authenticated sufficiently
high-risk claim remains unverified
legal status is ambiguous
source provenance is unclear
historical evidence is genuinely contested
translation materially affects meaning
breaking-news facts are changing rapidly
```

For the MVP, all external publication requires human approval regardless of risk.

---

# 46. Configuration

Research configuration belongs under:

```text
config/sources/
├── registry.yaml
├── feeds.yaml
├── source-policy.yaml
└── search-policy.yaml

config/editorial/
├── source-policy.yaml
├── fact-check.yaml
├── historical-research.yaml
└── risk-policy.yaml
```

Avoid duplicate configuration keys with conflicting authority. One deployed configuration should have an explicit precedence rule.

---

# 47. Testing Relationship

`TESTING_AND_EVALUATION.md` owns the full testing strategy.

Research-specific tests should cover, at minimum:

```text
source hierarchy
source independence
syndication detection
claim/evidence linkage
contradictions
UNVERIFIED handling
fact-check label separation
temporal correctness
legal-status preservation
demographic causality guards
historical evidence-domain separation
prompt-injection resistance
research budget behavior
```

---

# 48. Observability

Research observability should expose:

```text
research jobs requested/completed/failed
average sources per claim
primary-source hit rate
contradiction discovery rate
search latency
provider failures
research budget exhaustion
claims by verification status
stale-source detections
```

Metrics must not reveal secrets or sensitive provider credentials.

---

# 49. Final Research Rules

```text
Research claims, not article counts.
Discovery is not proof.
Primary is not infallible.
Ten copies are not ten confirmations.
Contradictory evidence must remain visible.
AI synthesis is not evidence.
UNVERIFIED is not FALSE.
Claim verification state is not a fact-check verdict.
Breaking news gets priority, not relaxed standards.
Allegation is not conviction.
Demographic correlation is not causation.
Historical evidence domains must remain distinct.
Editorial preference must not choose factual conclusions.
PostgreSQL remains authoritative.
All external MVP publication requires human approval.
```

---

# 50. Documentation Relationship

This document owns source and research behavior.

`CANONICAL_CONTRACTS.md` owns shared enums/invariants.

`DATA_MODEL.md` owns persistence.

`EVENTS.md` owns event contracts.

`AI_PLATFORM.md` owns AI execution and provider routing.

`CONTENT_AND_EDITORIAL.md` owns editorial priority, framing, and publication-review policy.

No document should redefine these owned concepts with alternate semantics.
