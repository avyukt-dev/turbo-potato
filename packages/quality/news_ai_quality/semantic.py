"""Pure, claim-scoped validation of supplied artifacts, never factual inference."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from uuid import UUID

from news_ai_content import ContentGenerationOutput, EditorialBrief
from news_ai_evidence import FactSheetArtifact
from news_ai_evidence.engine import EvidenceRelation
from pydantic import BaseModel, ConfigDict, Field, model_validator

SEMANTIC_METHODOLOGY_VERSION = "semantic-validator-v1"
_QUOTES = re.compile(r'"([^"]+)"|“([^”]+)”|«([^»]+)»|„([^“]+)“')


class SemanticFindingSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class SemanticFindingCategory(StrEnum):
    QUOTE_INTEGRITY = "QUOTE_INTEGRITY"
    REFERENCE_INTEGRITY = "REFERENCE_INTEGRITY"
    STANCE_CONSISTENCY = "STANCE_CONSISTENCY"
    CHRONOLOGY = "CHRONOLOGY"
    DEPENDENCY = "DEPENDENCY"
    DUPLICATE = "DUPLICATE"
    CATEGORICAL_ASSERTION = "CATEGORICAL_ASSERTION"


class SemanticFindingCode(StrEnum):
    UNMATCHED_QUOTE = "UNMATCHED_QUOTE"
    DANGLING_REFERENCE = "DANGLING_REFERENCE"
    WRONG_REFERENCE_OWNER = "WRONG_REFERENCE_OWNER"
    SOURCE_PROVENANCE_MISMATCH = "SOURCE_PROVENANCE_MISMATCH"
    RELATION_ROLE_MISMATCH = "RELATION_ROLE_MISMATCH"
    INVALID_EVIDENCE_RELATION = "INVALID_EVIDENCE_RELATION"
    DUPLICATE_REFERENCE = "DUPLICATE_REFERENCE"
    IMPOSSIBLE_TEMPORAL_RANGE = "IMPOSSIBLE_TEMPORAL_RANGE"
    TEMPORAL_PRECISION_UNRESOLVED = "TEMPORAL_PRECISION_UNRESOLVED"
    FACTUAL_IDENTITY_MISMATCH = "FACTUAL_IDENTITY_MISMATCH"
    EVIDENCE_IDENTITY_CONFLICT = "EVIDENCE_IDENTITY_CONFLICT"
    BRIEF_FACTUAL_METADATA_MISMATCH = "BRIEF_FACTUAL_METADATA_MISMATCH"


class SemanticFinding(BaseModel):
    """No untrusted text/metadata is copied into machine diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: SemanticFindingCode
    category: SemanticFindingCategory
    severity: SemanticFindingSeverity = SemanticFindingSeverity.ERROR
    message: str = Field(min_length=1, max_length=300)
    artifact_path: str = Field(min_length=1, max_length=200)
    claim_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    source_ids: tuple[UUID, ...] = Field(default=(), max_length=100)


class QuoteSpanMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_path: str = Field(min_length=1, max_length=200)
    generated_start: int = Field(ge=0)
    generated_end: int = Field(ge=0)
    claim_id: UUID
    evidence_id: UUID | None = None
    source_id: UUID | None = None
    normalized_source_start: int = Field(ge=0)
    normalized_source_end: int = Field(ge=0)


class SemanticCheckCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    quotations: int = Field(default=0, ge=0)
    references: int = Field(default=0, ge=0)
    temporal_ranges: int = Field(default=0, ge=0)
    dependencies: int = Field(default=0, ge=0)


class SemanticValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    methodology_version: str = Field(default=SEMANTIC_METHODOLOGY_VERSION, max_length=64)
    passed: bool
    findings: tuple[SemanticFinding, ...] = ()
    quote_matches: tuple[QuoteSpanMatch, ...] = ()
    check_counts: SemanticCheckCounts = Field(default_factory=SemanticCheckCounts)

    @model_validator(mode="after")
    def consistent(self) -> SemanticValidationReport:
        keys = tuple(item.model_dump_json() for item in self.findings)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("semantic findings must be unique and canonically ordered")
        if self.passed != (
            not any(f.severity == SemanticFindingSeverity.ERROR for f in self.findings)
        ):
            raise ValueError("semantic pass must reflect error findings")
        return self


def mechanical_normalize(value: str) -> str:
    """Preserve case and punctuation; NFC does not fold compatibility characters."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def _span_offset(span: str, quote: str) -> int:
    if not quote:
        return -1
    offset = span.find(quote)
    while offset >= 0:
        end = offset + len(quote)
        if not (
            (quote[0].isalnum() and offset > 0 and span[offset - 1].isalnum())
            or (quote[-1].isalnum() and end < len(span) and span[end].isalnum())
        ):
            return offset
        offset = span.find(quote, offset + 1)
    return -1


def quoted_spans(content: ContentGenerationOutput):
    fields = [("title", content.title, content.claim_ids_used)]
    for index, slide in enumerate(content.slides):
        fields.extend(
            (
                (f"slides[{index}].heading", slide.heading, slide.claim_ids),
                (f"slides[{index}].body", slide.body, slide.claim_ids),
            )
        )
    fields.append(("caption", content.caption, content.claim_ids_used))
    for path, text, scope in fields:
        for match in _QUOTES.finditer(text):
            group = next(i for i in range(1, 5) if match.group(i) is not None)
            yield path, match.group(group), scope, match.start(group), match.end(group)


def fabricated_quotes(
    content: ContentGenerationOutput, report: SemanticValidationReport
) -> tuple[str, ...]:
    failed = {
        f.artifact_path for f in report.findings if f.code == SemanticFindingCode.UNMATCHED_QUOTE
    }
    return tuple(
        dict.fromkeys(
            quote
            for path, quote, _, start, _ in quoted_spans(content)
            if f"{path}.quotes[{start}]" in failed
        )
    )


class SemanticValidator:
    """Validate explicit graph semantics. No dependency graph is currently supplied.

    Timeline entries are heterogeneous and do not declare ordering dependencies.
    Publication/retrieval time is not assumed to be an impossible ordering (scheduled
    documents and metadata corrections exist). Neither is inferred from prose.
    """

    methodology_version = SEMANTIC_METHODOLOGY_VERSION

    def validate(
        self,
        sheet: FactSheetArtifact,
        content: ContentGenerationOutput,
        *,
        source_ids_used: tuple[UUID, ...],
        editorial_brief: EditorialBrief | None = None,
    ) -> SemanticValidationReport:
        findings: dict[str, SemanticFinding] = {}
        references = 0

        def add(code, category, path, *, claim=None, evidence=None, source=None, warning=False):
            finding = SemanticFinding(
                code=code,
                category=category,
                artifact_path=path,
                message=code.value.replace("_", " ").lower(),
                severity=SemanticFindingSeverity.WARNING
                if warning
                else SemanticFindingSeverity.ERROR,
                claim_ids=(claim,) if claim else (),
                evidence_ids=(evidence,) if evidence else (),
                source_ids=(source,) if source else (),
            )
            findings[finding.model_dump_json()] = finding

        def index(items, attr, path):
            result = {}
            for item in items:
                identity = (
                    tuple(getattr(item, field) for field in attr)
                    if isinstance(attr, tuple)
                    else getattr(item, attr)
                )
                if identity in result:
                    add(
                        SemanticFindingCode.DUPLICATE_REFERENCE,
                        SemanticFindingCategory.DUPLICATE,
                        path,
                    )
                else:
                    result[identity] = item
            return result

        claims = index(sheet.claims, "claim_id", "fact_sheet.claims")
        # ClaimEvidence is many-to-many, with a relation owned by the pair.
        evidence_links = index(sheet.evidence, ("claim_id", "evidence_id"), "fact_sheet.evidence")
        evidence = {item.evidence_id: item for item in evidence_links.values()}
        sources = index(sheet.sources, "source_id", "fact_sheet.sources")
        fact_checks = index(sheet.fact_checks, "fact_check_id", "fact_sheet.fact_checks")
        # Relation/strength/note are pair-specific; immutable material is not.
        material_fields = (
            "source_id",
            "article_id",
            "article_version_id",
            "content_hash",
            "excerpt",
            "url",
            "published_at",
            "retrieved_at",
        )
        for i, item in enumerate(sheet.evidence):
            original = evidence[item.evidence_id]
            if any(getattr(original, field) != getattr(item, field) for field in material_fields):
                add(
                    SemanticFindingCode.EVIDENCE_IDENTITY_CONFLICT,
                    SemanticFindingCategory.CATEGORICAL_ASSERTION,
                    f"fact_sheet.evidence[{i}]",
                    claim=item.claim_id,
                    evidence=item.evidence_id,
                )

        def refs(ids, known, path, *, owner=None, role=None):
            nonlocal references
            seen = set()
            for identity in ids:
                references += 1
                if identity in seen:
                    add(
                        SemanticFindingCode.DUPLICATE_REFERENCE,
                        SemanticFindingCategory.DUPLICATE,
                        path,
                    )
                seen.add(identity)
                item = known.get(identity)
                if item is None:
                    add(
                        SemanticFindingCode.DANGLING_REFERENCE,
                        SemanticFindingCategory.REFERENCE_INTEGRITY,
                        path,
                        claim=identity if known is claims else owner,
                        evidence=identity if known is evidence else None,
                        source=identity if known is sources else None,
                    )
                    continue
                if known is evidence and owner is not None:
                    item = evidence_links.get((owner, identity))
                if item is None:
                    add(
                        SemanticFindingCode.WRONG_REFERENCE_OWNER,
                        SemanticFindingCategory.REFERENCE_INTEGRITY,
                        path,
                        claim=owner,
                        evidence=identity,
                    )
                    continue
                if role is not None and item.relation not in role:
                    add(
                        SemanticFindingCode.RELATION_ROLE_MISMATCH,
                        SemanticFindingCategory.STANCE_CONSISTENCY,
                        path,
                        claim=owner,
                        evidence=identity,
                    )

        if (content.story_id, content.fact_sheet_id, content.fact_sheet_version) != (
            sheet.story_id,
            sheet.fact_sheet_id,
            sheet.version,
        ):
            add(
                SemanticFindingCode.FACTUAL_IDENTITY_MISMATCH,
                SemanticFindingCategory.REFERENCE_INTEGRITY,
                "content",
            )
        refs(content.claim_ids_used, claims, "content.claim_ids_used")
        refs(source_ids_used, sources, "content.source_ids_used")
        for i, slide in enumerate(content.slides):
            refs(slide.claim_ids, claims, f"slides[{i}].claim_ids")
        expected_sources = {
            e.source_id
            for e in sheet.evidence
            if e.claim_id in content.claim_ids_used and e.source_id is not None
        }
        if set(source_ids_used) != expected_sources:
            add(
                SemanticFindingCode.SOURCE_PROVENANCE_MISMATCH,
                SemanticFindingCategory.REFERENCE_INTEGRITY,
                "content.source_ids_used",
            )
        temporal = 0
        for i, claim in enumerate(sheet.claims):
            path = f"fact_sheet.claims[{i}]"
            if claim.story_id != sheet.story_id:
                add(
                    SemanticFindingCode.WRONG_REFERENCE_OWNER,
                    SemanticFindingCategory.REFERENCE_INTEGRITY,
                    path,
                    claim=claim.claim_id,
                )
            refs(claim.evidence_ids, evidence, path + ".evidence_ids", owner=claim.claim_id)
            refs(
                claim.contradictory_evidence_ids,
                evidence,
                path + ".contradictory_evidence_ids",
                owner=claim.claim_id,
                role={EvidenceRelation.CONTRADICTS.value},
            )
            if not set(claim.contradictory_evidence_ids) <= set(claim.evidence_ids):
                add(
                    SemanticFindingCode.RELATION_ROLE_MISMATCH,
                    SemanticFindingCategory.STANCE_CONSISTENCY,
                    path,
                    claim=claim.claim_id,
                )
            if claim.temporal_start is not None and claim.temporal_end is not None:
                temporal += 1
                try:
                    invalid = claim.temporal_start > claim.temporal_end
                except TypeError:
                    add(
                        SemanticFindingCode.TEMPORAL_PRECISION_UNRESOLVED,
                        SemanticFindingCategory.CHRONOLOGY,
                        path,
                        claim=claim.claim_id,
                        warning=True,
                    )
                else:
                    if invalid:
                        add(
                            SemanticFindingCode.IMPOSSIBLE_TEMPORAL_RANGE,
                            SemanticFindingCategory.CHRONOLOGY,
                            path,
                            claim=claim.claim_id,
                        )
        for i, item in enumerate(sheet.evidence):
            path = f"fact_sheet.evidence[{i}]"
            refs((item.claim_id,), claims, path + ".claim_id")
            if item.source_id is not None:
                refs((item.source_id,), sources, path + ".source_id")
            claim = claims.get(item.claim_id)
            if claim is not None and item.evidence_id not in claim.evidence_ids:
                add(
                    SemanticFindingCode.WRONG_REFERENCE_OWNER,
                    SemanticFindingCategory.REFERENCE_INTEGRITY,
                    path,
                    claim=item.claim_id,
                    evidence=item.evidence_id,
                )
            if item.relation not in {r.value for r in EvidenceRelation}:
                add(
                    SemanticFindingCode.INVALID_EVIDENCE_RELATION,
                    SemanticFindingCategory.CATEGORICAL_ASSERTION,
                    path,
                    evidence=item.evidence_id,
                )
            if (
                claim is not None
                and item.relation == EvidenceRelation.CONTRADICTS.value
                and item.evidence_id not in claim.contradictory_evidence_ids
            ):
                add(
                    SemanticFindingCode.RELATION_ROLE_MISMATCH,
                    SemanticFindingCategory.STANCE_CONSISTENCY,
                    path,
                    claim=item.claim_id,
                    evidence=item.evidence_id,
                )
        for i, check in enumerate(sheet.fact_checks):
            path = f"fact_sheet.fact_checks[{i}]"
            if check.story_id != sheet.story_id:
                add(
                    SemanticFindingCode.WRONG_REFERENCE_OWNER,
                    SemanticFindingCategory.REFERENCE_INTEGRITY,
                    path,
                )
            if check.claim_id is not None:
                refs((check.claim_id,), claims, path + ".claim_id")
            refs(
                check.supporting_evidence_ids,
                evidence,
                path + ".supporting_evidence_ids",
                owner=check.claim_id,
                role={
                    EvidenceRelation.DIRECT_SUPPORT.value,
                    EvidenceRelation.INDIRECT_SUPPORT.value,
                },
            )
            refs(
                check.contradicting_evidence_ids,
                evidence,
                path + ".contradicting_evidence_ids",
                owner=check.claim_id,
                role={EvidenceRelation.CONTRADICTS.value},
            )

        if editorial_brief is not None:
            index(editorial_brief.claims, "claim_id", "editorial_brief.claims")
            for i, brief_claim in enumerate(editorial_brief.claims):
                path = f"editorial_brief.claims[{i}]"
                refs((brief_claim.claim_id,), claims, path + ".claim_id")
                refs((brief_claim.fact_check_id,), fact_checks, path + ".fact_check_id")
                refs(
                    brief_claim.evidence_ids,
                    evidence,
                    path + ".evidence_ids",
                    owner=brief_claim.claim_id,
                )
                check = fact_checks.get(brief_claim.fact_check_id)
                claim = claims.get(brief_claim.claim_id)
                if check is not None and check.claim_id != brief_claim.claim_id:
                    add(
                        SemanticFindingCode.WRONG_REFERENCE_OWNER,
                        SemanticFindingCategory.REFERENCE_INTEGRITY,
                        path,
                        claim=brief_claim.claim_id,
                    )
                if (
                    claim is not None
                    and (brief_claim.text != claim.claim_text or brief_claim.status != claim.status)
                ) or (check is not None and brief_claim.label != check.label):
                    add(
                        SemanticFindingCode.BRIEF_FACTUAL_METADATA_MISMATCH,
                        SemanticFindingCategory.CATEGORICAL_ASSERTION,
                        path,
                        claim=brief_claim.claim_id,
                    )

        # Only the canonical optional TimelineEvent reference fields are understood.
        # Array position is not an explicit ordering/dependency declaration.
        for i, entry in enumerate(sheet.timeline):
            for field, known in (("claim_ids", claims), ("evidence_ids", evidence)):
                value = entry.get(field)
                if not isinstance(value, (tuple, list)):
                    continue
                try:
                    identities = tuple(UUID(str(item)) for item in value)
                except (ValueError, TypeError):
                    continue
                refs(identities, known, f"fact_sheet.timeline[{i}].{field}")

        matches = []
        quotations = 0
        for path, quote, scope, start, end in quoted_spans(content):
            quotations += 1
            normalized = mechanical_normalize(quote)
            candidates = []
            for item in sorted(sheet.evidence, key=lambda e: str(e.evidence_id)):
                claim = claims.get(item.claim_id)
                if (
                    claim is not None
                    and item.claim_id in scope
                    and item.evidence_id in claim.evidence_ids
                    and item.excerpt
                ):
                    candidates.append(
                        (item.claim_id, item.evidence_id, item.source_id, item.excerpt)
                    )
            candidates.extend(
                (c.claim_id, None, None, c.claim_text)
                for c in sorted(sheet.claims, key=lambda c: str(c.claim_id))
                if c.claim_id in scope
            )
            for claim_id, evidence_id, source_id, span in candidates:
                offset = _span_offset(mechanical_normalize(span), normalized)
                if offset >= 0:
                    matches.append(
                        QuoteSpanMatch(
                            artifact_path=path,
                            generated_start=start,
                            generated_end=end,
                            claim_id=claim_id,
                            evidence_id=evidence_id,
                            source_id=source_id,
                            normalized_source_start=offset,
                            normalized_source_end=offset + len(normalized),
                        )
                    )
                    break
            else:
                add(
                    SemanticFindingCode.UNMATCHED_QUOTE,
                    SemanticFindingCategory.QUOTE_INTEGRITY,
                    f"{path}.quotes[{start}]",
                )
        ordered = tuple(findings[key] for key in sorted(findings))
        return SemanticValidationReport(
            methodology_version=self.methodology_version,
            passed=not any(f.severity == SemanticFindingSeverity.ERROR for f in ordered),
            findings=ordered,
            quote_matches=tuple(matches),
            check_counts=SemanticCheckCounts(
                quotations=quotations, references=references, temporal_ranges=temporal
            ),
        )
