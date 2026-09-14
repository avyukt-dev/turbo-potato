"""Typed evidence semantics and provenance-bearing graph edges."""

from enum import StrEnum
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVIDENCE_GRAPH_POLICY_VERSION = "evidence-graph-policy-v1"


class EvidenceDirectness(StrEnum):
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    UNKNOWN = "UNKNOWN"


class EvidenceOriginRole(StrEnum):
    ORIGINAL = "ORIGINAL"
    DERIVATIVE = "DERIVATIVE"
    REFERENCE = "REFERENCE"
    UNKNOWN = "UNKNOWN"


class EvidenceProvenanceState(StrEnum):
    DURABLE_VERSION_PRESERVED = "DURABLE_VERSION_PRESERVED"
    EXTERNAL_REFERENCE_ONLY = "EXTERNAL_REFERENCE_ONLY"
    UNKNOWN = "UNKNOWN"


class EvidenceTemporalRole(StrEnum):
    CONTEMPORARY = "CONTEMPORARY"
    RETROSPECTIVE = "RETROSPECTIVE"
    UPDATE = "UPDATE"
    UNKNOWN = "UNKNOWN"


class EvidenceGraphRelationType(StrEnum):
    DERIVED_FROM = "DERIVED_FROM"
    REFERENCES = "REFERENCES"
    CONTEXT_FOR = "CONTEXT_FOR"
    VERIFIES = "VERIFIES"
    UPDATES = "UPDATES"


class EvidenceGraphRelationSpec(BaseModel):
    """Deterministic edge intent before durable evidence IDs are available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_type: EvidenceGraphRelationType
    target_evidence_id: UUID | None = None
    external_reference: str | None = Field(default=None, min_length=1, max_length=4096)
    basis: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exactly_one_target(self):
        if (self.target_evidence_id is None) == (self.external_reference is None):
            raise ValueError("graph relation requires exactly one target")
        if not self.basis.strip() or (
            self.external_reference is not None and not self.external_reference.strip()
        ):
            raise ValueError("graph relation references and basis must not be blank")
        if self.external_reference is not None:
            reference = urlsplit(self.external_reference)
            if reference.scheme in {"http", "https"} and (
                not reference.hostname
                or reference.username is not None
                or reference.password is not None
                or any(
                    key.lower().replace("-", "_")
                    in {"token", "access_token", "api_key", "authorization", "password", "secret"}
                    for key, _ in parse_qsl(reference.query)
                )
            ):
                raise ValueError("graph external URL must be credential-free")
        return self
