# News AI Social Media Manager

# CONTENT_SCHEMAS.md

**Status:** Canonical
**Document Role:** Source of truth for application-layer structured contracts exchanged between research, evidence, Fact Sheet, editorial, content, quality, media, and publication layers.

Shared enums and lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

Persistence details remain owned by `DATA_MODEL.md`.

---

# 1. Purpose

This document defines the canonical Pydantic/JSON contracts used by application services.

The structured flow is:

```text
Story
  ↓
Claim[]
  ↓
Evidence[]
  ↓
ResearchResult
  ↓
FactSheet
  ↓
EditorialBrief
  ↓
ContentVariant[]
  ↓
QualityCheck
  ↓
PublicationRequest
```

Normal content generation must not bypass the Fact Sheet boundary.

---

# 2. Schema Principles

All cross-service structured data must be:

```text
versioned where materially necessary
validated before use
traceable to source artifact IDs
explicit about uncertainty
explicit about claim status
explicit about risk/sensitivity
free of secrets
```

Raw AI output is untrusted until parsed and validated.

---

# 3. Shared Enums

Do not define alternate enum vocabularies in application modules.

Import/use the canonical values from `CANONICAL_CONTRACTS.md`.

## 3.1 ClaimVerificationStatus

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

## 3.2 FactCheckLabel

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

## 3.3 RiskLevel

```text
LOW
MEDIUM
HIGH
CRITICAL
```

## 3.4 ReviewState

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

## 3.5 PublicationStatus

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

---

# 4. Base Metadata

Common metadata should support provenance without requiring every schema to duplicate large context.

Conceptual model:

```python
class ArtifactMeta(BaseModel):
    schema_version: int = 1
    created_at: datetime
    updated_at: datetime | None = None
    correlation_id: UUID | None = None
```

Where database IDs exist, use UUIDs consistently.

---

# 5. Source Reference

```python
class SourceRef(BaseModel):
    source_id: UUID
    name: str
    source_level: int
    url: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    language: str | None = None
```

Rules:

- `source_level` uses the hierarchy from `SOURCE_AND_RESEARCH.md`.
- source level is a role/authority signal, not a guarantee of truth.
- secrets, cookies, authorization headers, and private credentials are prohibited.

---

# 6. Evidence Reference

```python
class EvidenceRef(BaseModel):
    evidence_id: UUID
    source_id: UUID | None = None
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    relation: str
    strength_score: float | None = None
    excerpt: str | None = None
    provenance_note: str | None = None
```

`relation` maps to the evidence relationships owned by `DATA_MODEL.md`, such as direct support, contradiction, qualification, or context.

A citation/reference must actually support the associated proposition.

---

# 7. Claim Contract

```python
class Claim(BaseModel):
    claim_id: UUID
    story_id: UUID
    claim_text: str
    claim_type: str
    status: ClaimVerificationStatus
    confidence_score: float | None = None
    importance_score: float | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = []
    evidence_ids: list[UUID] = []
    contradictory_evidence_ids: list[UUID] = []
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    location_ids: list[UUID] = []
```

Rules:

- claim text must be independently assessable where practical.
- `UNVERIFIED` must not be transformed into `REFUTED` without evidence.
- verdict labels such as `FALSE` and `SATIRE` do not belong in `Claim.status`.

---

# 8. Fact-Check Contract

```python
class FactCheck(BaseModel):
    fact_check_id: UUID
    story_id: UUID
    claim_id: UUID | None = None
    label: FactCheckLabel
    confidence_score: float | None = None
    summary: str
    supporting_evidence_ids: list[UUID] = []
    contradicting_evidence_ids: list[UUID] = []
    review_required: bool
    review_state: ReviewState
```

Critical invariant:

```text
UNVERIFIED != FALSE
```

A fact-check verdict is separate from `ClaimVerificationStatus`.

---

# 9. Timeline Event

```python
class TimelineEvent(BaseModel):
    event_id: UUID | None = None
    timestamp: datetime | None = None
    date_text: str | None = None
    precision: str | None = None
    description: str
    claim_ids: list[UUID] = []
    evidence_ids: list[UUID] = []
```

If chronology is uncertain, preserve uncertainty through `date_text`/`precision` rather than inventing an exact timestamp.

---

# 10. Entity Reference

```python
class EntityRef(BaseModel):
    entity_id: UUID
    canonical_name: str
    entity_type: str
    role: str | None = None
```

Entity types should use the vocabulary owned by `DATA_MODEL.md`.

---

# 11. Research Result

Research workers should return structured results.

```python
class ResearchResult(BaseModel):
    story_id: UUID
    claim_ids: list[UUID]
    sources: list[SourceRef]
    evidence: list[EvidenceRef]
    contradictions: list[EvidenceRef]
    counterclaims: list[Claim] = []
    timeline: list[TimelineEvent] = []
    entities: list[EntityRef] = []
    unresolved_questions: list[str] = []
    research_notes: list[str] = []
    budget_exhausted: bool = False
```

`budget_exhausted = true` does not imply a claim is false.

---

# 12. Fact Sheet Contract

The Fact Sheet is the canonical factual intermediate representation for content generation.

```python
class FactSheet(BaseModel):
    fact_sheet_id: UUID
    story_id: UUID
    version: int
    headline: str
    summary: str

    claims: list[Claim]
    fact_checks: list[FactCheck] = []
    evidence: list[EvidenceRef]
    sources: list[SourceRef]

    timeline: list[TimelineEvent] = []
    entities: list[EntityRef] = []
    locations: list[str] = []
    context: list[str] = []
    counterclaims: list[Claim] = []
    unresolved_questions: list[str] = []

    confidence_score: float | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = []
    created_at: datetime
```

Persistence may maintain convenience partitions such as supported/disputed/unverified claim JSONB fields as described by `DATA_MODEL.md`; the application contract above preserves the canonical status on every claim.

Rules:

- published Fact Sheet versions are immutable.
- material corrections create a new version.
- contradictions and unresolved questions must not be silently dropped.
- content generation may only treat `SUPPORTED` material as established fact unless policy/context explicitly allows attributed discussion of disputed/unverified claims.

---

# 13. Editorial Brief

```python
class EditorialBrief(BaseModel):
    story_id: UUID
    fact_sheet_id: UUID
    priority_topics: list[str]
    editorial_angle: str
    key_points: list[str]
    exclusions: list[str] = []
    tone: str
    audience_relevance: float | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = []
    human_review_required: bool
```

Editorial Briefs may select emphasis but must not change claim statuses, evidence, or fact-check labels.

For the MVP, any brief intended for external social publication results in a workflow that requires human approval.

---

# 14. Platform-Neutral Content Draft

```python
class ContentDraft(BaseModel):
    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    editorial_brief_id: UUID | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = []
    review_state: ReviewState
    variant_ids: list[UUID] = []
```

A draft cannot be publication-eligible without a valid Fact Sheet.

---

# 15. Content Variant

```python
class ContentVariant(BaseModel):
    content_variant_id: UUID
    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int

    platform: str
    format: str
    language: str

    title: str | None = None
    body: str | None = None
    caption: str | None = None
    slides: list[str] = []
    thread: list[str] = []
    hashtags: list[str] = []
    media_asset_ids: list[UUID] = []

    claim_ids_used: list[UUID] = []
    source_ids_used: list[UUID] = []

    risk_level: RiskLevel
    sensitive_topics: list[str] = []
    review_state: ReviewState
    version: int
```

Rules:

- every factual statement should map to the Fact Sheet/claim set.
- platform transformation may change length/presentation, not factual status.
- a material content edit after approval invalidates prior approval as defined by policy.

---

# 16. Instagram Carousel Schema

```python
class InstagramCarouselContent(BaseModel):
    headline: str
    slides: list[str]
    caption: str
    hashtags: list[str] = []
    media_asset_ids: list[UUID] = []
    claim_ids_used: list[UUID] = []
```

A useful logical structure may be:

```text
hook/headline
what happened
verified facts
context/timeline
material qualification or contradiction
why it matters
sources/attribution
```

This is a content pattern, not a mandatory fixed slide count.

---

# 17. X Content Schema

```python
class XPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = []
    reply_to_variant_id: UUID | None = None
    claim_ids_used: list[UUID] = []
```

A thread is an ordered list of `XPostContent` records.

Platform limits remain configuration/provider-defined and are not hard-coded in this schema document.

---

# 18. Facebook Content Schema

```python
class FacebookPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = []
    claim_ids_used: list[UUID] = []
```

---

# 19. Telegram Content Schema

```python
class TelegramPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = []
    claim_ids_used: list[UUID] = []
```

---

# 20. YouTube Shorts Script

```python
class ShortsScript(BaseModel):
    hook: str
    narration: str
    on_screen_text: list[str] = []
    closing: str
    claim_ids_used: list[UUID] = []
    source_ids_used: list[UUID] = []
    estimated_duration_seconds: int | None = None
```

The script must not introduce facts absent from the Fact Sheet.

---

# 21. Translation Contract

```python
class TranslationResult(BaseModel):
    source_language: str
    target_language: str
    translated_text: str
    preserved_names: list[str] = []
    uncertainty_notes: list[str] = []
    ai_run_id: UUID | None = None
```

Translation must preserve factual meaning, legal status, uncertainty, and attribution.

Material translation uncertainty must be surfaced.

---

# 22. Image Brief

```python
class ImageBrief(BaseModel):
    story_id: UUID
    fact_sheet_id: UUID
    purpose: str
    visual_subject: str
    factual_elements: list[str]
    prohibited_elements: list[str] = []
    aspect_ratio: str
    text_overlay: str | None = None
    claim_ids_used: list[UUID] = []
```

AI image prompts must derive factual elements from verified structured context.

Synthetic visuals must not independently invent details that could be mistaken for documentary evidence.

---

# 23. Media Asset Contract

```python
class MediaAsset(BaseModel):
    media_asset_id: UUID
    asset_type: str
    storage_provider: str
    storage_key: str
    public_url: str | None = None
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    file_hash: str
    visual_check_status: str | None = None
```

Local persistence and public delivery are separate concerns as defined by `CANONICAL_CONTRACTS.md` and `SOCIAL_PUBLISHING.md`.

---

# 24. Quality Check Contract

```python
class QualityCheck(BaseModel):
    content_variant_id: UUID
    factual_accuracy_passed: bool
    source_alignment_passed: bool
    citation_alignment_passed: bool
    style_passed: bool

    unsupported_claims: list[str] = []
    fabricated_quotes: list[str] = []
    incorrect_names: list[str] = []
    incorrect_dates: list[str] = []
    incorrect_numbers: list[str] = []
    missing_context: list[str] = []

    defamation_risk: bool = False
    sensitive_topic_error: bool = False
    passed: bool
    review_required: bool
    notes: list[str] = []
```

For the MVP, a passing quality check does not authorize external publication. `review_required` remains true for externally publishable content.

---

# 25. Review Decision Contract

```python
class ReviewDecision(BaseModel):
    artifact_type: str
    artifact_id: UUID
    artifact_version: int
    decision: ReviewState
    reviewer_id: UUID
    reason: str | None = None
    decided_at: datetime
```

Allowed decision outcomes for a review action are normally:

```text
APPROVED
REJECTED
CHANGES_REQUESTED
```

Review records must identify the exact version reviewed.

---

# 26. Publication Request

```python
class PublicationRequest(BaseModel):
    publication_id: UUID | None = None
    content_variant_id: UUID
    social_account_id: UUID
    platform: str
    scheduled_at: datetime | None = None
    idempotency_key: str
```

Eligibility rules are owned by `SOCIAL_PUBLISHING.md`.

For the MVP, a request must not enter external execution unless the relevant content/review state is explicitly `APPROVED` by a human.

---

# 27. Publication Result

```python
class PublicationResult(BaseModel):
    publication_id: UUID
    status: PublicationStatus
    attempt_id: UUID | None = None
    external_post_id: str | None = None
    external_url: str | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    retryable: bool | None = None
```

Ambiguous platform timeouts must not be represented as definitive failure if external state is unknown.

---

# 28. AI Request/Response Relationship

`AI_PLATFORM.md` owns `AIRequest`, `AIResponse`, model routing, and AI-run provenance.

Content schemas may reference `ai_run_id` but must not redefine provider-specific response formats.

---

# 29. Structured Output Validation

Canonical flow:

```text
AI output
   ↓
parse
   ↓
Pydantic validation
   ↓
semantic validation
   ↓
persistence / next stage
```

If validation fails:

```text
retry/repair according to AI policy
    ↓
fallback where allowed
    ↓
fail/escalate if still invalid
```

Never persist malformed output merely because it appears plausible.

---

# 30. Semantic Validation

Schema validity is necessary but insufficient.

Validate:

```text
claim IDs exist
source/evidence IDs exist
claims belong to story
Fact Sheet version matches referenced artifact
content statements map to claims
review applies to exact version
publication references approved content
numeric ranges are sane
dates/locations are compatible with source context
```

---

# 31. Versioning

Material structured contracts should include `schema_version` where backward compatibility matters.

Breaking schema changes require explicit version changes and contract tests.

Artifact content versions are separate from schema versions.

Example:

```text
FactSheet schema_version = 1
FactSheet content version = 4
```

---

# 32. Immutability

Publication-relevant historical artifacts must remain auditable.

Do not silently mutate:

```text
published Fact Sheets
approved content versions
completed review decisions
publication attempts
published external IDs
```

Create new versions/correction records where required.

---

# 33. Sensitive Data

Structured schemas must not contain:

```text
API keys
access tokens
refresh tokens
passwords
private keys
session cookies
authorization headers
```

Use secure credential references where needed.

---

# 34. Evidence Packet Schema

A reviewer-facing evidence packet may be assembled as:

```python
class EvidencePacket(BaseModel):
    story_id: UUID
    fact_sheet: FactSheet
    supporting_evidence: list[EvidenceRef]
    contradicting_evidence: list[EvidenceRef]
    unresolved_questions: list[str] = []
    editorial_brief: EditorialBrief | None = None
    content_variant: ContentVariant | None = None
    quality_check: QualityCheck | None = None
    review_state: ReviewState
```

The packet is a view over canonical records; it does not have to be stored as one giant database object.

---

# 35. Contract Testing

`TESTING_AND_EVALUATION.md` owns the testing strategy.

Every schema should have tests for:

```text
valid payload
missing required field
invalid enum
invalid UUID
out-of-range score
unknown reference
schema version mismatch
claim/fact-check enum confusion
publication without approval
unsupported content claim
```

---

# 36. Final Schema Rules

```text
Schemas represent structured facts and workflow state; they do not invent truth.
Claim verification status is not a fact-check verdict.
UNVERIFIED is not FALSE.
Fact Sheet is the normal factual boundary before content generation.
Every content variant identifies the claims it uses.
Every publication references an approved content variant in MVP.
Quality pass is not publication approval.
Material changes create new versions.
PostgreSQL remains authoritative for durable state.
Provider-specific details stay behind adapters.
```

---

# 37. Documentation Relationship

This document owns application-layer structured contracts.

`CANONICAL_CONTRACTS.md` owns shared enums and lifecycle meanings.

`DATA_MODEL.md` owns persistence.

`SOURCE_AND_RESEARCH.md` owns source/research methodology.

`AI_PLATFORM.md` owns AI request/response execution semantics.

`CONTENT_AND_EDITORIAL.md` owns editorial/content policy.

`SOCIAL_PUBLISHING.md` owns publication eligibility and external platform execution.
