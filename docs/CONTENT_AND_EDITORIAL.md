# News AI Social Media Manager

# CONTENT_AND_EDITORIAL.md

**Status:** Canonical
**Document Role:** Source of truth for editorial policy, story prioritization, fact-checking, content generation, sensitive-topic handling, and publication-review requirements.

Shared enums and cross-document semantics are defined by `CANONICAL_CONTRACTS.md`.

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

# 3. Primary Taxonomy

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

The system may analyze shared Indian civilizational context while preserving distinct identities.

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

Do not collapse distinct traditions into one religious identity.

---

# 5. Evidence Hierarchy

## Level 1 — Primary

Examples:

```text
court judgments
government documents
parliamentary records
official statistics
official diplomatic or military statements
police documents
original research papers
archaeological reports
inscriptions
treaties
original recordings
official datasets
```

## Level 2 — Established Secondary

```text
major newspapers
international news agencies
established broadcasters
peer-reviewed academic publications
specialist publications
```

## Level 3 — Specialist / Investigative

```text
think tanks
research organizations
investigative journalism
subject-matter experts
specialist databases
```

## Level 4 — Discovery

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

Level 4 sources may discover a claim but should generally not be the sole evidence for high-risk factual claims.

---

# 6. Source Independence

Article count is not independent-confirmation count.

```text
10 republished articles from one originating report
!=
10 independent confirmations
```

The evidence engine should track source origin, lineage, syndication, citation dependency, and independent confirmation.

---

# 7. Claim Verification Status

A claim's evidence state uses `ClaimVerificationStatus`:

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

These are processing/evidence states.

They are not fact-check verdict labels.

Critical distinction:

```text
UNVERIFIED != REFUTED
```

---

# 8. Fact-Check Labels

A fact-check verdict uses `FactCheckLabel`:

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

Lack of evidence is not evidence of falsity.

A fact-check verdict may be applied to a checked claim, statement, post, media item, or composite assertion after evaluation.

---

# 9. Fact-Check Logic

Conceptually:

```text
Claim / assertion
 ↓
Can it be checked?
 ↓
Primary evidence?
 ↓
Independent corroboration?
 ↓
Contradictory evidence?
 ↓
Context complete?
 ↓
FactCheckLabel
```

`PARTIALLY_TRUE` is a verdict label.

`PARTIALLY_SUPPORTED` is a claim-verification status.

They must never be used interchangeably.

---

# 10. Confidence

Confidence is evidence-based.

It may consider:

```text
source quality
source independence
primary evidence
corroboration
contradiction
claim specificity
data quality
temporal/geographic reliability
```

A score such as `0.91` must not be described as an objective 91% probability of truth unless the score is explicitly calibrated for that interpretation.

AI model agreement alone is not evidence.

---

# 11. Editorial Scoring

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

A high-priority but weakly evidenced story should receive more research, not premature publication.

---

# 12. Risk and Sensitivity

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

# 13. Sensitive Topic Policy

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

# 14. MVP Human Approval Policy

For the MVP/current brainstorming implementation phase:

```text
ALL external social publication requires explicit human approval.
```

This includes low-risk content.

Automated checks may decide that content is ready for review, but they do not authorize external publication.

A future low-risk auto-approval mode may be introduced only when explicitly enabled and only under `CANONICAL_CONTRACTS.md`.

Mandatory-review topics may never use that future low-risk bypass.

---

# 15. Legal and Allegation Reporting

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

# 16. Caste-Related Reporting

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

# 17. Demographic Reporting

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

# 18. Historical Research Policy

Historical questions are evidence problems, not ideological switches.

Relevant domains include:

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

Evidence in one domain must not automatically be represented as proof in another.

For contested questions, record competing hypotheses, supporting evidence, limiting/contradicting evidence, and uncertainty.

---

# 19. Indo-European / Indo-Aryan Historical Questions

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

# 20. Counterclaims and Contradictions

Contradictory evidence must be retained even when it weakens the preferred editorial angle.

A counterclaim is not automatically equally credible; evaluate source quality, directness, independence, and evidence strength.

Material unresolved disagreement must remain visible in the Fact Sheet and final content.

---

# 21. Fact Sheet Boundary

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

# 22. Fact Sheet Content

Canonical conceptual structure:

```text
FACT SHEET
├── headline
├── summary
├── verified/supported claims
├── disputed claims
├── unverified claims
├── refuted claims where material
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

The Fact Sheet must preserve material contradictions and uncertainty.

---

# 23. Editorial Angle

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
```

---

# 24. Content Style

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

# 25. Headline Rules

Headlines must not materially overstate the strongest supported claim.

For allegations, attribute the allegation.

For uncertain breaking news, reflect uncertainty.

For fact checks, distinguish the claim being checked from the system's verdict.

---

# 26. Quotations

Quotes must be traceable to a source and preserved accurately.

Never fabricate quotation marks around paraphrases.

If translation is used, preserve the source language where practical in provenance and identify material translation uncertainty.

---

# 27. Numbers and Statistics

Numbers require source alignment.

Preserve units, period, geography, denominators, and whether a number is official, estimated, alleged, or independently verified.

---

# 28. Images and Visuals

Image briefs must derive from verified Fact Sheet information.

AI-generated visuals must not be presented in a way that could reasonably be mistaken for authentic documentary evidence when they are synthetic.

Do not invent uniforms, insignia, people, documents, locations, weapons, casualty scenes, or events as factual evidence.

---

# 29. Platform Transformation

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

# 30. Quality Gate

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

---

# 31. Review Integrity

Human review must inspect the content together with its evidence packet.

Review state is version-specific.

Material changes after approval may invalidate approval and require re-review.

---

# 32. Prohibited Inferences

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

# 33. Configuration

Editorial policy belongs under:

```text
config/editorial/
├── priorities.yaml
├── source-policy.yaml
├── fact-check.yaml
├── historical-research.yaml
├── content-style.yaml
├── risk-policy.yaml
└── publishing-policy.yaml
```

Do not scatter editorial identity across unrelated prompts or code paths.

---

# 34. Final Editorial Rules

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
Historical controversies must preserve separate evidence domains.
All external publication requires explicit human approval in the MVP.
Sensitive/mandatory-review content always requires human review.
```

---

# 35. Documentation Relationship

This document owns editorial behavior and content-policy decisions.

`CANONICAL_CONTRACTS.md` owns shared enums and cross-document lifecycle semantics.

`DATA_MODEL.md` owns persistence.

`EVENTS.md` owns event contracts.

`AI_PLATFORM.md` owns provider/model/prompt architecture.

`SOCIAL_PUBLISHING.md` owns platform execution.

No document should redefine another document's owned concepts with alternate semantics.