# News AI Social Media Manager

# SOURCE_AND_RESEARCH.md

**Status:** Canonical
**Document Role:** Source of truth for source registration, research planning, evidence acquisition, corroboration, contradiction handling, historical research, source independence, and research provenance.

Shared enums, configuration ownership, and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This document defines how the system discovers claims, researches them, acquires evidence, evaluates source independence, preserves contradictions, and produces evidence-backed research outputs for the Fact Sheet layer.

It does not redefine:

```text
persistent database structures      → DATA_MODEL.md
event envelopes and delivery        → EVENTS.md
AI providers and model routing      → AI_PLATFORM.md
editorial priorities and content    → CONTENT_AND_EDITORIAL.md
social publication execution        → SOCIAL_PUBLISHING.md
test strategy                        → TESTING_AND_EVALUATION.md
shared enums/lifecycle semantics     → CANONICAL_CONTRACTS.md
```

---

# 2. Research Principle

The system is claim-driven, not article-summary-driven.

```text
DISCOVERY
   ↓
STORY
   ↓
CLAIMS
   ↓
RESEARCH PLAN
   ↓
SEARCH / SOURCE ACQUISITION
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

# 3. Source Registry vs Source Policy

Source identity/collection and evidence policy are deliberately separated.

## 3.1 Source registry and collection mechanics

Owned by:

```text
config/sources/
├── registry.yaml
├── feeds.yaml
└── collection.yaml
```

This configuration describes:

```text
source identity
publisher/domain
country/region
language
feed/API endpoints
collection method
poll interval
parser/collector settings
enabled/disabled state
technical credential references
technical rate-limit settings
```

It answers:

```text
Where and how do we collect this source?
```

It does NOT answer:

```text
Is this source sufficient evidence for a claim?
```

## 3.2 Research/evidence policy

Owned by:

```text
config/research/
├── source-policy.yaml
├── search-policy.yaml
├── corroboration.yaml
├── fact-check.yaml
└── historical-research.yaml
```

This configuration describes:

```text
source hierarchy and role rules
minimum corroboration expectations
primary-source requirements
source-independence evaluation
contradiction-search requirements
research budgets/timeouts
fact-check methodology
historical evidence-domain methodology
```

It answers:

```text
How do we evaluate and research evidence?
```

## 3.3 Editorial configuration

`config/editorial/` does not own evidence methodology.

Editorial configuration may choose which stories receive attention and which content/review policies apply, but it must not redefine source truth rules.

---

# 4. Source Hierarchy

## Level 1 — Primary

Examples:

```text
court judgments and orders
government notifications/documents
parliamentary records
official statistics
official diplomatic statements
official military statements
police documents/statements
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

# 6. Source Evaluation Dimensions

Keep separate:

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

Do not compress these into one simplistic “trusted source” flag.

A Level 1 source can still be preliminary, self-interested, incomplete, mistaken, or later corrected.

A Level 4 source can contain authentic first-hand material, but that material still requires provenance and verification.

---

# 7. Source Independence

Canonical invariant:

```text
10 republished articles from one originating report
!=
10 independent confirmations
```

Research should identify, where possible:

```text
original reporting source
wire/agency origin
syndication
copying/republication
citation dependency
shared primary document
shared eyewitness
independent field reporting
independent primary evidence
```

Source independence is often heuristic. The system must not claim statistical independence where it has only inferred lineage.

---

# 8. Claim Extraction Requirements

Claim extraction prompt v3 / `claim-extraction-methodology-v3` classifies each atomic
proposition with closed ClaimSemanticType and ClaimSemanticState (see canonical
contracts), never verification status or a verdict. Extraction operation identity
binds exact source/story context, methodology, prompt ID/version/checksum and
`claim-semantics-policy-v1` and `value-integrity-policy-v1`. Historical v1/v2 completion
cannot satisfy current v3 extraction.
New classification retains the extraction AIRun. Reused unclassified claims may
receive semantics only from actual current extraction output; matching classifications
are reused, and conflicts fail closed (`CLAIM_SEMANTICS_CONFLICT`). Verification
status and historical legacy claim_type are not rewritten. Fact Sheet generation
copies current durable classification, including policy and AIRun provenance;
missing classification fails closed (`CLAIM_SEMANTICS_MISSING`), never inferred.
Source authority, independence and FactCheck truth rules are unchanged.

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

# 9. Claim Verification Status

Use only canonical `ClaimVerificationStatus`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

Critical distinction:

```text
UNVERIFIED != REFUTED
```

Fact-check verdict labels do not belong in this status.

---

# 10. Fact-Check Verdicts

Formal fact checking uses `FactCheckLabel`:

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

Methodology and thresholds belong in `config/research/fact-check.yaml`.

---

# 11. Research Planning

For each material claim, generate a research plan before broad searching.

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

# 12. Search Provider Abstraction

Search is separate from AI-provider routing.

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── SpecialistSearchProvider
└── FutureProvider
```

Application services request search capabilities rather than hard-code provider SDKs.

Search providers return candidate sources. They do not determine factual truth.

`AIProvider` remains owned by `AI_PLATFORM.md`.

---

# 13. Query Planning

Useful query families include:

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

# 14. Temporal Correctness

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

For breaking stories, stale reporting must not override newer authoritative updates merely because it appears frequently in search results.

---

# 15. Source Versioning and Preservation

Where source material can change, preserve enough information to reconstruct what was reviewed.

Use:

```text
article_versions
content hash
retrieval timestamp
source metadata
archive reference where lawful/available
```

Do not silently replace a previously reviewed source version with a later edit.

---

# 16. Evidence Acquisition

For each material claim, seek the sources appropriate to the proposition:

```text
primary evidence
independent established reporting
specialist/context sources
contradictory evidence
counterclaims
```

Primary evidence should be prioritized when it directly addresses the claim.

The system must retain evidence that weakens the preferred editorial angle.

---

# 17. Evidence Relationships

Evidence may:

```text
DIRECT_SUPPORT
INDIRECT_SUPPORT
CONTRADICTS
QUALIFIES
CONTEXT
PRIMARY_EVIDENCE
SECONDARY_EVIDENCE
```

A source merely discussing the same topic is not necessarily evidence for the claim.

Persistence belongs to `claim_evidence` as defined by `DATA_MODEL.md`.

---

# 18. Evidence Strength

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

# 19. Contradictory Evidence

Contradictory evidence remains first-class.

Credible disagreement may produce:

```text
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

according to the evidence.

A contradiction is not automatically sufficient to mark a claim `REFUTED`.

---

# 20. Counterclaims

Counterclaims should be researched as claims, not merely copied into a “both sides” field.

Evaluate:

```text
who made it
what exactly it asserts
its evidence
its independence
its chronology
its relevance
```

A counterclaim is not automatically equally credible merely because it exists.

---

# 21. Corroboration Policy

Corroboration rules belong in:

```text
config/research/corroboration.yaml
```

The policy may vary by claim/risk type, but it must preserve these rules:

```text
source count != independent source count
primary evidence can outweigh numerous derivative reports
high-risk claims receive stricter minimum evidence
lack of corroboration may mean UNVERIFIED rather than FALSE
```

Research budgets must never manufacture certainty merely because the configured budget is exhausted.

---

# 22. Breaking News Protocol

Breaking news receives higher processing priority, not lower factual standards.

```text
collect rapidly
extract provisional claims
mark uncertainty explicitly
seek primary/official updates
seek independent reporting
track contradictions
re-run research as evidence changes
version the Fact Sheet
require human approval before external publication in MVP
```

Preliminary reports remain preliminary.

---

# 23. Sensitive Topic Enhanced Research

Material claims involving mandatory-review categories require enhanced research.

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

---

# 24. Legal and Criminal Allegations

Preserve:

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
identify exact allegation
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

Distinguish:

```text
confirmed event
reported motive
alleged motive
actor identity where established
victim identity where established
official attribution
independent attribution
unverified social claims
casualty figures
later corrections
```

Do not infer collective responsibility from the identity of individuals.

---

# 27. Terrorism Attribution

Distinguish:

```text
claim of responsibility
official attribution
intelligence assessment
media attribution
court/legal finding
independent corroboration
```

A group's claim of responsibility is evidence of that claim, not automatically conclusive proof of operational responsibility.

---

# 28. War Casualties and Conflict Statistics

Record:

```text
who reported the number
official/estimated/independently verified status
civilian/combatant definition
geographic scope
time period
methodology where known
conflicting counts
```

Do not merge incompatible casualty definitions into one number.

---

# 29. Demographic and Statistical Research

Preserve:

```text
dataset
year/time range
geographic scope
population definition
sample/census basis
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

Methodology belongs in:

```text
config/research/historical-research.yaml
```

The historical engine produces structured evidence, not ideology-driven binary answers.

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

The system must not manufacture false equivalence. Weak hypotheses may be represented as weak where evidence warrants it.

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

Do not store secrets or restricted-access credentials in evidence metadata.

---

# 34. Citation and Reference Integrity

Evidence items should preserve appropriate citation metadata:

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

Citation existence is insufficient; the source must actually support the associated proposition.

---

# 35. AI in Research

AI may assist:

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

AI-generated synthesis remains interpretation over source material.

---

# 36. Untrusted Content and Prompt Injection

All retrieved web/social content is untrusted input.

Embedded instructions in source material are source content, not system instructions.

Retrieved content must never be allowed to instruct the system to:

```text
reveal secrets
change credentials
ignore evidence policy
execute arbitrary host commands
publish content
modify editorial/research rules
```

Tool use must remain constrained by the application, not delegated to source text.

---

# 37. Research State

Conceptual research lifecycle:

```text
NOT_REQUESTED
PLANNED
SEARCHING
COLLECTING
EVALUATING
COMPLETED
INCOMPLETE
FAILED
```

This lifecycle must not replace durable job state owned by `DATA_MODEL.md`.

`INCOMPLETE` or budget exhaustion must not be converted into factual certainty.

---

# 38. Event Integration

Core events:

```text
claims.extracted
    ↓
evidence.requested
    ↓
evidence.collected
    ↓
fact_check.completed
    ↓
story.verified
```

Exact schemas and retry behavior are owned by `EVENTS.md`.

---

# 39. Evidence Packet Output

Every publication candidate should be reconstructable with:

```text
story
claims
sources
supporting evidence
contradictory evidence
primary evidence
counterclaims
timeline
entities
locations
confidence assessments
risk/sensitivity
editorial angle
AI provenance
human review
```

PostgreSQL remains the durable source of truth for these records.

New article acquisition records safe typed provenance in
`ArticleVersion.version_metadata.content_acquisition`: feed/page origin, exact
SHA-256 of the stored normalized body, aware retrieval time, content type/byte
count, extractor version, redirect count and final host. No raw HTML, response
headers or URL query credentials belong in acquisition provenance. For acquired
versions the metadata URL is query/fragment-free; the Article's canonical identity
and the existing normalized article content-hash algorithm remain unchanged.
Acquisition is source-material retrieval only, not evidence assessment or proof of
truth, authority or independent corroboration.

---

# 40. Research Budgets and Caching

Research should support configurable:

```text
maximum query count
maximum source fetch count
provider budget
per-source timeout
overall job timeout
cache TTL
re-query interval
breaking-news refresh interval
```

These belong under `config/research/`, not editorial configuration.

Caching must not prevent discovery of newer evidence in time-sensitive stories.

---

# 41. Human Escalation

Escalate when:

```text
material claims remain DISPUTED/UNVERIFIED
primary evidence is inaccessible or ambiguous
credible sources conflict materially
legal status cannot be established
source authenticity is uncertain
high-risk claim lacks sufficient corroboration
historical evidence is materially contested
```

Research completion does not by itself authorize publication.

---

# 42. Testing and Observability

Detailed test strategy belongs to `TESTING_AND_EVALUATION.md`.

Research-specific observability should track:

```text
queries issued
sources fetched
primary sources found
independent evidence groups
contradictions found
research latency
provider failures
budget exhaustion
claim status distribution
```

---

# 43. Final Rules

```text
Discovery source != evidence source.
Article count != independent confirmation count.
Primary source != automatically true.
AI synthesis != evidence.
UNVERIFIED != REFUTED.
UNVERIFIED != FALSE.
Contradictory evidence must remain visible.
Research methodology lives in config/research/.
Source collection mechanics live in config/sources/.
Editorial preference must not redefine source/evidence policy.
Breaking news gets higher priority, not lower standards.
Historical questions preserve separate evidence domains.
Research exhaustion must not manufacture certainty.
```

---

## Current value annotation boundary

Claim extraction prompt v3 / claim-extraction-methodology-v3 requires value_candidates
on every atomic claim ([] is an evaluated empty set; missing is invalid).
Each bounded source_text must occur mechanically in claim_text and canonical annotations
must agree with the supported exact expression grammar. Unsupported expressions require
EXACT_COPY_ONLY, never guessed semantics. AI annotates propositions, not truth/evidence.
The application assigns deterministic UUID anchors from Claim identity and ordered material.
Extraction identity binds exact source context, both semantic/value policies, methodology,
and prompt ID/version/checksum. A PR3-only v2 result cannot complete current extraction.
Actual v3 extraction may attach values to unclassified reused Claims, preserving existing
verification and PR3 provenance. Equivalent canonical value multisets reuse existing anchors;
conflicting current values fail closed without overwrite.
Current Fact Sheet generation copies complete evaluated values and AIRun provenance,
including []; it neither classifies, calculates nor changes historical snapshots.
