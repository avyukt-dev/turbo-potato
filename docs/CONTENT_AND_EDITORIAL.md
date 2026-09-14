# News AI Social Media Manager

# CONTENT_AND_EDITORIAL.md

**Status:** Canonical
**Document Role:** Source of truth for editorial policy, story prioritization, content framing, sensitive-topic handling, and publication-review requirements.

Shared enums, configuration ownership, and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

Research/evidence methodology is owned by `SOURCE_AND_RESEARCH.md`.

---

# 1. Core Editorial Principle

The system separates:

```text
FACT
  ↓
EVIDENCE
  ↓
INTERPRETATION
  ↓
EDITORIAL ANGLE
```

Editorial preference determines attention, prioritization, context, tone, and format.

Evidence determines factual confidence.

Political, cultural, religious, civilizational, or commercial preferences must not override contradictory evidence or convert unsupported claims into facts.

---

# 2. Editorial Mission

The system prioritizes coverage of:

```text
Indian governance and politics
Indian economic development
Indian defence and security
Indian foreign policy and geopolitics
wars, conflict, diplomacy, and international institutions
religious freedom, persecution, violence, and discrimination
demographic change
constitutional and legal developments
human-rights developments
caste-related developments and SC/ST Act cases
hate speech, blasphemy, and derogatory statements
Hinduism and Hindu traditions
Sikh, Jain, Buddhist, indigenous, and regional Indian traditions
Indian civilizational history
ancient, medieval, colonial, and modern Indian history
archaeology, inscriptions, linguistic history, and population history
fact checking, misinformation, and disinformation
```

Other major domestic and international stories may be covered when important.

---

# 3. Primary Editorial Taxonomy

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

Stories may belong to multiple categories.

---

# 4. Indic Civilizational Context

Use:

```text
INDIC_CIVILIZATIONAL_CONTEXT
├── HINDU_TRADITIONS
├── BUDDHIST_TRADITIONS
├── JAIN_TRADITIONS
├── SIKH_TRADITIONS
├── INDIGENOUS_REGIONAL_TRADITIONS
└── ANCIENT_INDIAN_CULTURAL_TRADITIONS
```

This permits shared civilizational analysis while preserving distinct religious identities.

Do not collapse distinct traditions into one religious identity.

---

# 5. Evidence Methodology Boundary

The editorial engine may consume source/evidence assessments but does not define them.

Source hierarchy, source independence, corroboration, fact-check methodology, research budgets, and historical evidence methodology are owned by `SOURCE_AND_RESEARCH.md` and `config/research/`.

Editorial configuration must not redefine:

```text
what counts as primary evidence
minimum corroboration rules
source independence
fact-check evidence thresholds
historical evidence methodology
```

This separation protects the core invariant:

```text
editorial preference selects attention, not truth
```

---

# 6. Claim Verification Status

A claim's evidence state uses `ClaimVerificationStatus`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

These are evidence/workflow states.

They are not fact-check verdict labels.

Critical distinction:

```text
UNVERIFIED != REFUTED
```

---

# 7. Fact-Check Labels

A formal fact-check verdict uses `FactCheckLabel`:

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

`PARTIALLY_TRUE` is a verdict label.

`PARTIALLY_SUPPORTED` is a claim-verification state.

They must never be used interchangeably.

Fact-check methodology belongs in `config/research/fact-check.yaml`.

---

# 8. Editorial Scoring

Keep separate scores for:

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

Editorial priority and factual confidence are separate dimensions.

Example:

```text
importance = 0.95
evidence_strength = 0.45
```

is valid and should normally trigger additional research rather than stronger factual language.

---

# 9. Risk and Sensitivity

Canonical `RiskLevel`:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

Sensitivity is separate from risk level.

Example:

```text
sensitive_topic = true
sensitive_categories = [COMMUNAL_VIOLENCE]
risk_level = HIGH
```

Risk is independent of editorial importance.

---

# 10. Sensitive Topic Policy

At minimum, stricter verification and mandatory human review apply to material claims involving:

```text
communal violence
religious accusations
religious violence
individual criminal allegations
sexual assault
terrorism attribution
war casualties
election fraud
SC/ST allegations
caste-related accusations
blasphemy allegations
unverified breaking news
```

Sensitive-topic classification must not be inferred merely from demographic identity.

---

# 11. MVP Human Approval Policy

For the MVP/current implementation phase:

```text
ALL external social publication requires explicit human approval.
```

This includes low-risk content.

Automated checks may determine that content is ready for review, but they do not authorize external publication.

A future low-risk auto-approval mode may be introduced only when explicitly enabled and only under `CANONICAL_CONTRACTS.md`.

Mandatory-review topics may never use that future low-risk bypass.

---

# 12. Legal and Allegation Reporting

Preserve procedural status:

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

Do not convert:

```text
X accused Y
```

into:

```text
Y did X
```

unless evidence establishes the underlying act.

An official allegation remains an allegation unless the status changes.

---

# 13. Caste-Related Reporting

The system may report verified caste-related facts when relevant.

It must not:

```text
infer behavior from caste identity
generalize criminality to a caste
create caste stereotypes
fabricate caste statistics
imply collective guilt
encourage discrimination
```

SC/ST Act reporting must preserve procedural status and underlying evidence.

---

# 14. Demographic Reporting

Separate:

```text
OBSERVED DATA
    ↓
STATISTICAL INTERPRETATION
    ↓
POSSIBLE EXPLANATIONS
    ↓
CAUSAL EVIDENCE
    ↓
EDITORIAL INTERPRETATION
```

Do not infer malicious intent, wrongdoing, or causation merely from population change or correlation.

Record dataset, time period, geography, population definition, methodology, and uncertainty where relevant.

---

# 15. Historical Editorial Policy

Historical content consumes the evidence-domain analysis produced under `SOURCE_AND_RESEARCH.md`.

Relevant evidence domains include:

```text
LINGUISTICS
ARCHAEOLOGY
GENETICS
LITERARY SOURCES
EPIGRAPHY
CHRONOLOGY
MATERIAL CULTURE
POPULATION MOVEMENT
CULTURAL TRANSMISSION
POLITICAL EXPANSION
MILITARY CONFLICT
```

The editorial layer may decide which supported evidence or unresolved question to emphasize, but it must not redefine historical research methodology.

Methodology belongs in:

```text
config/research/historical-research.yaml
```

---

# 16. Indo-European / Indo-Aryan Historical Questions

The system must not hard-code a blanket instruction to automatically accept or reject an “Aryan theory.”

Distinguish propositions such as:

```text
language dispersal
population movement
genetic ancestry
archaeological continuity
cultural transmission
political expansion
military invasion
```

These are related but not interchangeable claims.

Report the evidentiary status of each separately.

---

# 17. Counterclaims and Contradictions

Contradictory evidence must remain visible even when it weakens the preferred editorial angle.

A counterclaim is not automatically equally credible; evaluate source quality, directness, independence, chronology, and evidence strength through the research layer.

Material unresolved disagreement must remain visible in the Fact Sheet and final content.

---

# 18. Fact Sheet Boundary

Normal content generation consumes a Fact Sheet.

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
EDITORIAL ANGLE
   ↓
CONTENT
```

Do not ask a model to read many raw articles and immediately produce the final post.

---

# 19. Fact Sheet Content

Canonical conceptual structure:

```text
FACT SHEET
├── headline
├── summary
├── claims with ClaimVerificationStatus
├── fact-check verdicts where applicable
├── evidence
├── timeline
├── entities
├── locations
├── context
├── counterclaims
├── unresolved questions
├── confidence
├── risk
└── sources
```

The Fact Sheet must preserve material contradictions and uncertainty.

---

# 20. Editorial Angle

Editorial angle may determine:

```text
which supported facts lead
which context is emphasized
what question the post answers
tone and format
audience relevance
```

It must not:

```text
invent evidence
hide material contradictions
turn allegations into findings
manufacture quotes/numbers
attribute unsupported motives
change ClaimVerificationStatus
change FactCheckLabel without a new evidence evaluation
```

---

# 21. Content Style

Default style:

```text
clear
specific
source-aware
confident only where evidence warrants
explicit about uncertainty
non-sensational unless the facts themselves are extraordinary
```

Avoid loaded wording that adds facts or motives not present in evidence.

---

# 22. Headline Rules

Headlines must not materially overstate the strongest supported claim.

For allegations, attribute the allegation.

For uncertain breaking news, reflect uncertainty.

For fact checks, distinguish the claim being checked from the system's verdict.

---

# 23. Quotations

Quotes must be traceable to a source and preserved accurately.

Never fabricate quotation marks around paraphrases.

If translation is used, preserve the source language where practical in provenance and identify material translation uncertainty.

---

# 24. Numbers and Statistics

Numbers require source alignment.

Preserve units, period, geography, denominators, and whether a number is official, estimated, alleged, or independently verified.

---

# 25. Images and Visuals

Image briefs must derive from verified Fact Sheet information.

AI-generated visuals must not be presented in a way that could reasonably be mistaken for authentic documentary evidence when they are synthetic.

Do not invent uniforms, insignia, people, documents, locations, weapons, casualty scenes, or events as factual evidence.

---

# 26. Platform Transformation

One Fact Sheet may generate:

```text
Instagram carousel + caption
X post/thread
Facebook post
Telegram post
YouTube Shorts script
```

Each variant may change length and presentation, but not factual status.

---

# 27. Quality Gate

Quality checking should detect:

```text
factual drift
unsupported claims
citation/source mismatch
fabricated quotes
incorrect names/dates/numbers
missing material context
overstatement
defamation risk
sensitive-topic errors
```

Quality pass does not equal publication approval in the MVP.

The quality-domain `semantic-validator-v2` checks explicit reference ownership,
relation roles, duplicate references, claim temporal bounds, and quoted spans.
EditorialBrief claim/FactCheck references and copied factual status/label/text must
agree with the exact Fact Sheet; this is structured-copy integrity, not analysis of
prose certainty or stronger/weaker wording. It also revalidates typed claim
presentations against the exact Fact Sheet using `certainty-policy-v1`.

Content generation methodology v2 uses content prompt v2 and requires one typed
presentation per used claim. Accepted status/label pairs and ceilings are:

| Status | Label | Maximum strength | Frames |
| --- | --- | --- | --- |
| SUPPORTED | TRUE | HIGH | DIRECT, QUALIFIED, UNCERTAIN |
| PARTIALLY_SUPPORTED | PARTIALLY_TRUE | MEDIUM | QUALIFIED, UNCERTAIN |
| DISPUTED | UNVERIFIED | LOW | DISPUTED |
| UNVERIFIED | UNVERIFIED | LOW | UNCERTAIN |
| REFUTED | FALSE | NONE | REFUTATION |

All other pairs, including every UNASSESSED pair, fail closed under `certainty-policy-v1`.
Canonical enum values not emitted by the current deterministic FactCheckEngine receive no
downstream certainty semantics until an explicitly versioned producer/policy change.
Enum membership is not authorization to upgrade status. No prose classifier or new claim
taxonomy is implied. Quality methodology v5 independently checks these immutable
inputs and uses quality prompt v2 to report typed prose `certainty_escalations`.
Any escalation or deterministic ERROR fails quality; AI cannot override it.
Historical artifacts remain unchanged; missing presentation metadata requires
regeneration before a new current quality assessment can proceed.
Slide quotes are scoped to that slide's declared claims; title/caption quotes use
the artifact's selected claims. Only mechanically normalized claim text or linked
evidence excerpts can supply a quote (evidence provenance is preferred). Headline,
summary, unrelated claims, and unrelated evidence cannot launder a quotation.
Case and punctuation remain significant; paraphrases in quotation marks fail.

Quality methodology v4 includes this report alongside the existing AIRouter
assessment. Semantic ERROR findings cannot be overridden by AI and leave content
NOT_READY. Findings, source-span matches, and check counts are durable and typed.
The report does not infer prose stance, independent sources, dependencies, or
chronology from text. Heterogeneous timeline metadata has no canonical ordering
declaration; only supported explicit reference fields are checked. A future typed
dependency/ordering contract is required before those checks can be implemented.

---

# 28. Review Integrity

Human review must inspect the content together with its evidence packet.

Review state is version-specific.

Material changes after approval may invalidate approval and require re-review.

---

# 29. Prohibited Inferences

The system must not infer, without supporting evidence:

```text
religion → criminal tendency
caste → behavior
nationality → moral character
demographic change → malicious intent
political affiliation → factual truth/falsity
population movement → military invasion
linguistic relationship → demographic replacement
correlation → causation
accusation → guilt
```

---

# 30. Editorial Configuration

Editorial configuration lives only under:

```text
config/editorial/
├── priorities.yaml
├── taxonomy.yaml
├── content-style.yaml
├── risk-policy.yaml
└── publishing-policy.yaml
```

Responsibilities:

```text
priorities.yaml        → topic/coverage priority
taxonomy.yaml          → editorial topic taxonomy/content grouping
content-style.yaml     → tone, format, style constraints
risk-policy.yaml       → sensitivity/risk routing and review requirements
publishing-policy.yaml → editorial publication eligibility/policy
```

Do not put source/evidence methodology here.

The following belong to `config/research/`:

```text
source-policy.yaml
search-policy.yaml
corroboration.yaml
fact-check.yaml
historical-research.yaml
```

This separation must be enforced by configuration validation where practical.

---

# 31. Final Editorial Rules

```text
Editorial priority selects attention, not truth.
Evidence determines factual confidence.
AI output is not evidence by itself.
UNVERIFIED is not FALSE.
Claim verification status is not a fact-check verdict.
Allegation is not conviction.
Contradictory evidence must remain visible.
Distinct religious traditions remain distinct.
Caste facts may be reported without stereotypes or collective guilt.
Demographic change does not itself establish causation or intent.
Historical controversies preserve separate evidence domains.
Research/evidence methodology lives outside editorial configuration.
All external publication requires explicit human approval in the MVP.
Sensitive/mandatory-review content always requires human review.
```

---

# 32. Documentation Relationship

This document owns editorial behavior, prioritization, content policy, risk routing, and publication-review policy.

`CANONICAL_CONTRACTS.md` owns shared enums, configuration ownership, and cross-document lifecycle semantics.

`SOURCE_AND_RESEARCH.md` owns source/evidence methodology.

`DATA_MODEL.md` owns persistence.

`EVENTS.md` owns event contracts.

`AI_PLATFORM.md` owns provider/model/prompt architecture.

`CONTENT_SCHEMAS.md` owns structured application contracts.

`SOCIAL_PUBLISHING.md` owns platform execution.

No document should redefine another document's owned concepts with alternate semantics.
