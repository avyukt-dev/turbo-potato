# News AI Social Media Manager

# CONTENT_SCHEMAS.md

**Status:** Canonical
**Document Role:** Source of truth for application-layer Pydantic/JSON contracts exchanged between research, evidence, Fact Sheet, editorial, content, quality, media, review, and publication layers.

Shared enums and lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

Persistence details remain owned by `DATA_MODEL.md`.

---

# 1. Purpose

Canonical structured flow:

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
ReviewDecision
  ↓
PublicationRequest
```

Normal content generation must not bypass the Fact Sheet boundary.

---

# 2. Pydantic Example Conventions

Illustrative models assume Pydantic and use safe collection defaults.

Use:

```python
from pydantic import BaseModel, Field
```

For mutable collections prefer:

```python
items: list[str] = Field(default_factory=list)
metadata: dict[str, Any] = Field(default_factory=dict)
```

Do not use shared mutable defaults such as:

```python
items: list[str] = []
metadata: dict[str, Any] = {}
```

Even where a framework currently protects against shared-state behavior, canonical examples should follow ordinary safe Python/Pydantic practice.

---

# 3. Shared Enums

Do not define alternate enum vocabularies in application modules.

## ClaimVerificationStatus

```text
UNASSESSED
SUPPORTED
PARTIALLY_SUPPORTED
DISPUTED
UNVERIFIED
REFUTED
```

## FactCheckLabel

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

## RiskLevel

```text
LOW
MEDIUM
HIGH
CRITICAL
```

## ReviewState

```text
NOT_READY
READY_FOR_REVIEW
IN_REVIEW
APPROVED
REJECTED
CHANGES_REQUESTED
```

## PublicationStatus

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

Critical invariants:

```text
UNVERIFIED != REFUTED
UNVERIFIED != FALSE
```

---

# 4. Artifact Metadata

```python
class ArtifactMeta(BaseModel):
    schema_version: int = 1
    created_at: datetime
    updated_at: datetime | None = None
    correlation_id: UUID | None = None
```

Use UUIDs consistently for database-backed externally meaningful records.

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

`source_level` reflects the hierarchy in `SOURCE_AND_RESEARCH.md` and is a role/authority signal, not a truth guarantee.

Collection metadata comes from the source registry domain. Evidence-policy evaluation comes from the research domain.

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

`relation` maps to evidence relationships owned by `DATA_MODEL.md`.

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
    sensitive_topics: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    contradictory_evidence_ids: list[UUID] = Field(default_factory=list)
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    location_ids: list[UUID] = Field(default_factory=list)
```

Rules:

```text
Claim.status uses ClaimVerificationStatus only.
FALSE/SATIRE/PARTIALLY_TRUE do not belong in Claim.status.
Legacy partially_confirmed is invalid.
```

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
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    contradicting_evidence_ids: list[UUID] = Field(default_factory=list)
    review_required: bool
    review_state: ReviewState
```

A fact-check verdict is separate from claim verification state.

---

# 9. Timeline Event

```python
class TimelineEvent(BaseModel):
    event_id: UUID | None = None
    timestamp: datetime | None = None
    date_text: str | None = None
    precision: str | None = None
    description: str
    claim_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
```

If chronology is uncertain, preserve uncertainty rather than invent an exact timestamp.

---

# 10. Entity Reference

```python
class EntityRef(BaseModel):
    entity_id: UUID
    canonical_name: str
    entity_type: str
    role: str | None = None
```

Entity types use the vocabulary owned by `DATA_MODEL.md`.

---

# 11. Research Result

```python
class ResearchResult(BaseModel):
    story_id: UUID
    claim_ids: list[UUID]
    sources: list[SourceRef]
    evidence: list[EvidenceRef]
    contradictions: list[EvidenceRef]
    counterclaims: list[Claim] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    research_notes: list[str] = Field(default_factory=list)
    budget_exhausted: bool = False
```

`budget_exhausted = true` does not imply a claim is false or refuted.

---

# 12. Fact Sheet Contract

```python
class FactSheet(BaseModel):
    fact_sheet_id: UUID
    story_id: UUID
    version: int
    headline: str
    summary: str

    claims: list[Claim]
    fact_checks: list[FactCheck] = Field(default_factory=list)
    evidence: list[EvidenceRef]
    sources: list[SourceRef]

    timeline: list[TimelineEvent] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    counterclaims: list[Claim] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

    confidence_score: float | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = Field(default_factory=list)
    created_at: datetime
```

Rules:

```text
Published Fact Sheet versions are immutable.
Material corrections create a new version.
Contradictions/unresolved questions are preserved.
SUPPORTED material may be treated as established fact.
DISPUTED/UNVERIFIED material requires attribution/uncertainty when discussed.
REFUTED material must not be presented as established fact.
```

Persistence may maintain convenience partitions while preserving each canonical claim status.

---

# 13. Editorial Brief

```python
class EditorialBrief(BaseModel):
    story_id: UUID
    fact_sheet_id: UUID
    priority_topics: list[str]
    editorial_angle: str
    key_points: list[str]
    exclusions: list[str] = Field(default_factory=list)
    tone: str
    audience_relevance: float | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = Field(default_factory=list)
    human_review_required: bool
```

Editorial Briefs select emphasis. They must not modify claim status, evidence, or fact-check verdicts.

For the MVP, any external-publication workflow requires human approval regardless of `risk_level`.

---

# 14. Content Draft

```python
class ContentDraft(BaseModel):
    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    editorial_brief_id: UUID | None = None
    risk_level: RiskLevel
    sensitive_topics: list[str] = Field(default_factory=list)
    review_state: ReviewState
    variant_ids: list[UUID] = Field(default_factory=list)
```

A draft cannot become publication-eligible without a valid Fact Sheet.

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
    slides: list[str] = Field(default_factory=list)
    thread: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    media_asset_ids: list[UUID] = Field(default_factory=list)

    claim_ids_used: list[UUID] = Field(default_factory=list)
    source_ids_used: list[UUID] = Field(default_factory=list)

    # Required on new content-generation v2 output; stored in structured_payload.
    claim_presentations: list[ClaimPresentation]

    risk_level: RiskLevel
    sensitive_topics: list[str] = Field(default_factory=list)
    review_state: ReviewState
    version: int
```

Every factual statement should map to the Fact Sheet/claim set.

A material edit after approval invalidates/re-evaluates approval according to policy.

---

# 16. Instagram Carousel

```python
class InstagramCarouselContent(BaseModel):
    headline: str
    slides: list[str]
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    media_asset_ids: list[UUID] = Field(default_factory=list)
    claim_ids_used: list[UUID] = Field(default_factory=list)
```

Logical slide patterns are presentation guidance, not factual-state transformations.

---

# 17. X Content

```python
class XPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = Field(default_factory=list)
    reply_to_variant_id: UUID | None = None
    claim_ids_used: list[UUID] = Field(default_factory=list)
```

A thread is an ordered list of `XPostContent` records.

Platform limits remain configuration/provider-defined.

---

# 18. Facebook Content

```python
class FacebookPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = Field(default_factory=list)
    claim_ids_used: list[UUID] = Field(default_factory=list)
```

---

# 19. Telegram Content

```python
class TelegramPostContent(BaseModel):
    text: str
    media_asset_ids: list[UUID] = Field(default_factory=list)
    claim_ids_used: list[UUID] = Field(default_factory=list)
```

---

# 20. YouTube Shorts Script

```python
class ShortsScript(BaseModel):
    hook: str
    narration: str
    on_screen_text: list[str] = Field(default_factory=list)
    closing: str
    claim_ids_used: list[UUID] = Field(default_factory=list)
    source_ids_used: list[UUID] = Field(default_factory=list)
    estimated_duration_seconds: int | None = None
```

The script must not introduce facts absent from the Fact Sheet.

---

# 21. Translation Result

```python
class TranslationResult(BaseModel):
    source_language: str
    target_language: str
    translated_text: str
    preserved_names: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    ai_run_id: UUID | None = None
```

Translation preserves factual meaning, legal status, uncertainty, and attribution.

---

# 22. Image Brief

```python
class ImageBrief(BaseModel):
    story_id: UUID
    fact_sheet_id: UUID
    purpose: str
    visual_subject: str
    factual_elements: list[str]
    prohibited_elements: list[str] = Field(default_factory=list)
    aspect_ratio: str
    text_overlay: str | None = None
    claim_ids_used: list[UUID] = Field(default_factory=list)
```

Synthetic visuals must not independently invent details that could be mistaken for documentary evidence.

---

# 23. Media Asset

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

Local persistence and public delivery remain separate concerns.

---

# 24. Quality Check

```python
class QualityCheck(BaseModel):
    content_variant_id: UUID
    factual_accuracy_passed: bool
    source_alignment_passed: bool
    citation_alignment_passed: bool
    style_passed: bool

    unsupported_claims: list[str] = Field(default_factory=list)
    fabricated_quotes: list[str] = Field(default_factory=list)
    incorrect_names: list[str] = Field(default_factory=list)
    incorrect_dates: list[str] = Field(default_factory=list)
    incorrect_numbers: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)

    defamation_risk: bool = False
    sensitive_topic_error: bool = False
    passed: bool
    review_required: bool
    notes: list[str] = Field(default_factory=list)
    certainty_escalations: list[CertaintyEscalation] | None = None
```

For the MVP, `review_required` remains true for any content intended for external publication. A quality pass does not authorize publication.

Application-owned deterministic quality uses frozen `SemanticFinding` and
`SemanticValidationReport` contracts in `news_ai_quality.semantic`. Findings have
closed code/category/severity enums, bounded diagnostic locations and canonical
claim/evidence/source IDs; arbitrary provider metadata is not accepted. Categories
are QUOTE_INTEGRITY, REFERENCE_INTEGRITY, STANCE_CONSISTENCY, CHRONOLOGY, DEPENDENCY,
DUPLICATE, CATEGORICAL_ASSERTION, and CERTAINTY. ERROR findings fail semantic validation;
warnings are auditable without independently failing otherwise-valid quality.
Stable ordering and deduplication make report serialization deterministic.

Quote matches record generated offsets and mechanically normalized source offsets
with claim/evidence/source identity, without copying source text into diagnostics.
Dependency checks remain empty until a canonical explicit dependency input exists.

Content-generation v2 requires frozen `ClaimPresentation` records, exactly one per
`claim_ids_used`: claim_id, source_status, source_fact_check_label,
assertion_strength (HIGH/MEDIUM/LOW/NONE), frame
(DIRECT/QUALIFIED/DISPUTED/UNCERTAIN/REFUTATION). They are stored inside the immutable
ContentVariant structured_payload and therefore the reviewed artifact hash.
The application validates exact source copies and `certainty-policy-v1` ceilings.
The only authorized pairs are SUPPORTED/TRUE, PARTIALLY_SUPPORTED/PARTIALLY_TRUE,
DISPUTED/UNVERIFIED, UNVERIFIED/UNVERIFIED, REFUTED/FALSE. All other pairs fail closed;
enum membership alone does not authorize downstream certainty semantics.
Quality revalidates these records independently. Its CERTAINTY findings have closed
status/label/ceiling/frame/missing/invalid-source-combination codes.
Quality AI output requires `certainty_escalations`, bounded frozen entries with
claim_id, artifact_path (title/caption/zero-based slide heading or body), and closed
reason_code. They flag prose mismatch, never change factual status. The application
rejects unknown claim/location references and fails quality on any escalation.

---

# 25. Review Decision

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

Review action outcomes normally use:

```text
APPROVED
REJECTED
CHANGES_REQUESTED
```

The record identifies the exact reviewed version.

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

For the MVP, external execution is invalid unless the relevant content/version has explicit human `APPROVED` state.

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

An ambiguous platform timeout must not be represented as definitive failure if external state is unknown.

---

# 28. AI Request/Response Relationship

`AI_PLATFORM.md` owns `AIRequest`, `AIResponse`, model routing, and AI-run provenance.

Content schemas may reference `ai_run_id` but must not redefine provider-specific formats.

---

# 29. Runtime Relationship

Structured content/domain contracts remain platform-independent.

No content/research/editorial schema should include native service-manager commands or OS-specific process-control details.

Runtime status/control contracts, if later needed, belong to the runtime infrastructure package rather than these content schemas.

---

# 30. Configuration Relationship

Schemas may reference effective configuration by version/hash/ID, but they do not redefine configuration ownership.

Canonical roots remain:

```text
config/sources/   collection mechanics
config/research/  evidence/research methodology
config/editorial/ prioritization/content/review policy
config/models/    AI routing
config/prompts/   prompt versions
config/platforms/ social platform constraints
```

---

# 31. Validation Pipeline

Every AI-produced structured artifact follows:

```text
raw model output
    ↓
parse
    ↓
Pydantic validation
    ↓
semantic/domain validation
    ↓
reference validation
    ↓
persist
```

Malformed output is never accepted merely because it appears semantically plausible.

---

# 32. Versioning and Immutability

Version:

```text
Fact Sheets
content variants
reviewed artifacts
material research outputs where required
prompts/models through AI provenance
```

Published content remains traceable to the exact Fact Sheet/content/review version used.

---

# 33. Final Schema Rules

```text
Use canonical enums only.
Use Field(default_factory=...) for mutable example defaults.
Fact Sheet is the factual boundary.
ClaimVerificationStatus != FactCheckLabel.
UNVERIFIED != REFUTED.
UNVERIFIED != FALSE.
Content variants cannot change factual status.
Quality pass != publication approval.
All external MVP publication requires explicit human approval.
Schemas remain OS/platform/service-manager independent.
```
