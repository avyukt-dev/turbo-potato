"""Deterministic source-authority and lineage resolution over durable source versions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from uuid import UUID

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_database import Article, ArticleVersion, Source
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .engine import ResearchCandidate
from .graph import (
    EvidenceGraphRelationSpec,
    EvidenceGraphRelationType,
    EvidenceOriginRole,
    EvidenceProvenanceState,
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_POSITIVE_ORIGIN_KEYS = (
    "primary_document_id",
    "dataset_id",
    "eyewitness_record_id",
    "source_record_id",
)
_SHARED_REFERENCE_KEYS = (
    "originating_url",
    "wire_origin",
    *_POSITIVE_ORIGIN_KEYS,
    "citation_source_url",
)
_LINEAGE_REFERENCE_KEYS = tuple(dict.fromkeys((*_SHARED_REFERENCE_KEYS, *_POSITIVE_ORIGIN_KEYS)))


class SourceAuthorityLevel(IntEnum):
    PRIMARY = 1
    ESTABLISHED_SECONDARY = 2
    SPECIALIST_RESEARCH = 3
    DISCOVERY = 4


class LineageStatus(StrEnum):
    KNOWN_SHARED = "KNOWN_SHARED"
    INFERRED_SHARED = "INFERRED_SHARED"
    INDEPENDENT = "INDEPENDENT"
    UNRESOLVED = "UNRESOLVED"


class SourcePolicyRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_is_not_automatic_truth: bool
    discovery_is_not_sufficient_for_serious_claims: bool
    preserve_source_lineage: bool
    unknown_effective_level: SourceAuthorityLevel = SourceAuthorityLevel.DISCOVERY


class SourcePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    source_levels: dict[SourceAuthorityLevel, str]
    rules: SourcePolicyRules

    @model_validator(mode="after")
    def validate_canonical_levels(self) -> SourcePolicyConfig:
        expected = {
            SourceAuthorityLevel.PRIMARY: "primary",
            SourceAuthorityLevel.ESTABLISHED_SECONDARY: "established_secondary",
            SourceAuthorityLevel.SPECIALIST_RESEARCH: "specialist_research",
            SourceAuthorityLevel.DISCOVERY: "discovery",
        }
        if self.source_levels != expected:
            raise ValueError("source policy must define the canonical authority hierarchy")
        if not self.rules.primary_is_not_automatic_truth:
            raise ValueError("primary sources must not be configured as automatic truth")
        if not self.rules.preserve_source_lineage:
            raise ValueError("source lineage preservation is mandatory")
        if not self.rules.discovery_is_not_sufficient_for_serious_claims:
            raise ValueError("discovery sources cannot be sufficient for serious claims")
        if self.rules.unknown_effective_level is not SourceAuthorityLevel.DISCOVERY:
            raise ValueError("unknown source authority must resolve to discovery level")
        return self


class CorroborationRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count_independent_evidence_groups_not_article_count: bool
    detect_syndication_and_shared_origin: bool
    retain_contradictory_evidence: bool
    ai_model_agreement_is_not_corroboration: bool
    unresolved_counts_as_independent: bool = False


class CorroborationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    rules: CorroborationRules
    near_duplicate_similarity_threshold: float = Field(default=0.82, ge=0.5, le=1)
    shingle_size: int = Field(default=5, ge=2, le=10)

    @model_validator(mode="after")
    def enforce_conservative_invariants(self) -> CorroborationConfig:
        if not self.rules.count_independent_evidence_groups_not_article_count:
            raise ValueError("corroboration must count independent groups")
        if not self.rules.detect_syndication_and_shared_origin:
            raise ValueError("syndication detection must remain enabled")
        if not self.rules.retain_contradictory_evidence:
            raise ValueError("contradictory evidence must be retained")
        if self.rules.ai_model_agreement_is_not_corroboration is not True:
            raise ValueError("AI model agreement cannot be corroboration")
        if self.rules.unresolved_counts_as_independent:
            raise ValueError("unresolved lineage cannot count as independent")
        return self


@dataclass(frozen=True, slots=True)
class ResearchPolicySnapshot:
    source_policy: SourcePolicyConfig
    corroboration: CorroborationConfig


class ResearchPolicyLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> ResearchPolicySnapshot:
        return ResearchPolicySnapshot(
            source_policy=self.loader.load_domain_file(
                ConfigDomain.RESEARCH, "source-policy.yaml", SourcePolicyConfig
            ),
            corroboration=self.loader.load_domain_file(
                ConfigDomain.RESEARCH, "corroboration.yaml", CorroborationConfig
            ),
        )


class SourcePolicyResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_level: SourceAuthorityLevel
    basis: str
    source_id: UUID | None = None


class LineageResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LineageStatus
    independence_group: str | None = Field(default=None, max_length=255)
    basis: str
    originating_reference: str | None = Field(default=None, max_length=4096)


@dataclass(frozen=True, slots=True)
class CandidateSourceResolution:
    source: Source | None
    article: Article | None
    version: ArticleVersion | None
    authority: SourcePolicyResolution
    lineage: LineageResolution
    graph_relations: tuple[EvidenceGraphRelationSpec, ...] = ()
    origin_role: EvidenceOriginRole = EvidenceOriginRole.UNKNOWN
    provenance_state: EvidenceProvenanceState = EvidenceProvenanceState.UNKNOWN


@dataclass(slots=True)
class _Record:
    candidate: ResearchCandidate
    source: Source
    article: Article
    version: ArticleVersion
    references: dict[str, str]


class SourceEvidenceResolver:
    """Resolve policy and grouping without treating heuristics as statistical independence."""

    def __init__(self, policy: ResearchPolicySnapshot) -> None:
        self.policy = policy

    def resolve(
        self,
        session: Session,
        candidates: tuple[ResearchCandidate, ...],
    ) -> dict[tuple[UUID, str], CandidateSourceResolution]:
        result: dict[tuple[UUID, str], CandidateSourceResolution] = {}
        valid_by_claim: dict[UUID, list[_Record]] = {}
        for candidate in candidates:
            key = (candidate.claim_id, candidate.result.url)
            record = self._load_record(session, candidate)
            if record is None:
                result[key] = CandidateSourceResolution(
                    source=None,
                    article=None,
                    version=None,
                    authority=SourcePolicyResolution(
                        effective_level=self.policy.source_policy.rules.unknown_effective_level,
                        basis="conservative-unclassified-source",
                    ),
                    lineage=LineageResolution(
                        status=LineageStatus.UNRESOLVED,
                        basis="missing-or-mismatched-durable-source-version",
                    ),
                )
                continue
            valid_by_claim.setdefault(candidate.claim_id, []).append(record)

        for records in valid_by_claim.values():
            result.update(self._resolve_claim(records))
        return result

    def _load_record(self, session: Session, candidate: ResearchCandidate) -> _Record | None:
        metadata = candidate.result.metadata
        try:
            source_id = UUID(str(metadata["source_id"]))
            article_id = UUID(str(metadata["article_id"]))
            version_id = UUID(str(metadata["article_version_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        source = session.get(Source, source_id)
        article = session.get(Article, article_id)
        version = session.get(ArticleVersion, version_id)
        if (
            source is None
            or article is None
            or version is None
            or article.source_id != source.id
            or version.article_id != article.id
            or article.canonical_url != candidate.result.url
            or version.content_hash != metadata.get("content_hash")
        ):
            return None
        combined = {**(source.source_metadata or {}), **(version.version_metadata or {})}
        references = {
            key: str(combined[key]).strip()
            for key in _LINEAGE_REFERENCE_KEYS
            if combined.get(key) is not None and str(combined[key]).strip()
        }
        return _Record(candidate, source, article, version, references)

    @staticmethod
    def _graph_relations(record: _Record) -> tuple[EvidenceGraphRelationSpec, ...]:
        mappings = {
            "originating_url": EvidenceGraphRelationType.DERIVED_FROM,
            "wire_origin": EvidenceGraphRelationType.DERIVED_FROM,
            "citation_source_url": EvidenceGraphRelationType.REFERENCES,
            "primary_document_id": EvidenceGraphRelationType.REFERENCES,
            "dataset_id": EvidenceGraphRelationType.REFERENCES,
            "eyewitness_record_id": EvidenceGraphRelationType.REFERENCES,
            "source_record_id": EvidenceGraphRelationType.REFERENCES,
        }
        return tuple(
            EvidenceGraphRelationSpec(
                relation_type=mappings[key],
                external_reference=record.references[key],
                basis=f"explicit-{key}",
            )
            for key in mappings
            if key in record.references
        )

    def _resolve_claim(
        self, records: list[_Record]
    ) -> dict[tuple[UUID, str], CandidateSourceResolution]:
        parents = list(range(len(records)))
        pair_basis: dict[tuple[int, int], tuple[LineageStatus, str, str | None]] = {}

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = root(left), root(right)
            if left_root != right_root:
                parents[right_root] = left_root

        for left in range(len(records)):
            for right in range(left + 1, len(records)):
                shared = self._shared_basis(records[left], records[right])
                if shared is not None:
                    pair_basis[(left, right)] = shared
                    union(left, right)

        components: dict[int, list[int]] = {}
        for index in range(len(records)):
            components.setdefault(root(index), []).append(index)

        output: dict[tuple[UUID, str], CandidateSourceResolution] = {}
        for indexes in components.values():
            component_pairs = {
                pair: match
                for pair, match in pair_basis.items()
                if pair[0] in indexes and pair[1] in indexes
            }
            shared_group = None
            if component_pairs:
                fingerprint = "|".join(sorted(str(records[index].version.id) for index in indexes))
                shared_group = "shared:" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
            for index in indexes:
                record = records[index]
                if shared_group is not None:
                    incident = [match for pair, match in component_pairs.items() if index in pair]
                    selected = min(
                        incident,
                        key=lambda item: (
                            0 if item[0] is LineageStatus.KNOWN_SHARED else 1,
                            item[1],
                            item[2] or "",
                        ),
                    )
                    lineage = LineageResolution(
                        status=selected[0],
                        independence_group=shared_group,
                        basis=selected[1],
                        originating_reference=selected[2],
                    )
                else:
                    positive = self._positive_origin(record)
                    if positive is None:
                        lineage = LineageResolution(
                            status=LineageStatus.UNRESOLVED,
                            basis="no-positive-lineage-or-independence-signal",
                        )
                    else:
                        basis, reference = positive
                        fingerprint = f"{basis}|{reference}"
                        lineage = LineageResolution(
                            status=LineageStatus.INDEPENDENT,
                            independence_group="independent:"
                            + hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
                            basis=basis,
                            originating_reference=reference,
                        )
                level = record.source.authority_level
                explicit = level in {1, 2, 3, 4}
                authority = SourcePolicyResolution(
                    effective_level=(
                        SourceAuthorityLevel(level)
                        if explicit
                        else self.policy.source_policy.rules.unknown_effective_level
                    ),
                    basis=(
                        "explicit-source-authority-level"
                        if explicit
                        else "conservative-unclassified-source"
                    ),
                    source_id=record.source.id,
                )
                output[(record.candidate.claim_id, record.candidate.result.url)] = (
                    CandidateSourceResolution(
                        source=record.source,
                        article=record.article,
                        version=record.version,
                        authority=authority,
                        lineage=lineage,
                        graph_relations=self._graph_relations(record),
                        origin_role=self._origin_role(record),
                        provenance_state=EvidenceProvenanceState.DURABLE_VERSION_PRESERVED,
                    )
                )
        return output

    @staticmethod
    def _origin_role(record: _Record) -> EvidenceOriginRole:
        if any(key in record.references for key in ("originating_url", "wire_origin")):
            return EvidenceOriginRole.DERIVATIVE
        if any(key in record.references for key in _POSITIVE_ORIGIN_KEYS):
            return EvidenceOriginRole.ORIGINAL
        if "citation_source_url" in record.references:
            return EvidenceOriginRole.REFERENCE
        return EvidenceOriginRole.UNKNOWN

    @staticmethod
    def _positive_origin(record: _Record) -> tuple[str, str] | None:
        for key in _POSITIVE_ORIGIN_KEYS:
            reference = record.references.get(key)
            if reference is not None:
                return f"explicit-{key}", reference
        return None

    def _shared_basis(
        self, left: _Record, right: _Record
    ) -> tuple[LineageStatus, str, str | None] | None:
        if left.article.id == right.article.id:
            return LineageStatus.KNOWN_SHARED, "same-canonical-article", left.article.canonical_url
        if left.version.content_hash == right.version.content_hash:
            return LineageStatus.KNOWN_SHARED, "identical-content-hash", left.version.content_hash
        if left.source.id == right.source.id:
            return LineageStatus.KNOWN_SHARED, "same-registered-publisher", str(left.source.id)
        for key in _SHARED_REFERENCE_KEYS:
            if key in left.references and left.references.get(key) == right.references.get(key):
                return LineageStatus.KNOWN_SHARED, f"shared-{key}", left.references[key]
        similarity = _shingle_similarity(
            left.version.body or "",
            right.version.body or "",
            self.policy.corroboration.shingle_size,
        )
        if similarity >= self.policy.corroboration.near_duplicate_similarity_threshold:
            reference = "|".join(sorted((left.version.content_hash, right.version.content_hash)))
            return LineageStatus.INFERRED_SHARED, "near-duplicate-content", reference
        return None


def _shingle_similarity(left: str, right: str, size: int) -> float:
    def shingles(value: str) -> set[tuple[str, ...]]:
        tokens = [item.casefold() for item in _WORD_RE.findall(value)]
        if not tokens:
            return set()
        if len(tokens) < size:
            return {tuple(tokens)}
        return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}

    left_set, right_set = shingles(left), shingles(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)
