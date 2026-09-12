"""AI-router-backed claim-specific evidence assessment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from news_ai_ai import (
    AIInvalidResponseError,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRouter,
    AITaskType,
    PromptReference,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .engine import (
    EvidenceAssessment,
    EvidenceAssessmentAIProvenance,
    EvidenceRelation,
    ResearchCandidate,
)


class EvidenceAssessmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    candidate_url: str = Field(min_length=1, max_length=4096)
    relation: EvidenceRelation
    strength_score: float = Field(ge=0, le=1)
    relevant_excerpt: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def forbid_authority_classification(self) -> EvidenceAssessmentOutput:
        if self.relation in {
            EvidenceRelation.PRIMARY_EVIDENCE,
            EvidenceRelation.SECONDARY_EVIDENCE,
        }:
            raise ValueError("AI evidence output cannot classify source authority")
        return self


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentPrompt:
    prompt_id: str
    version: str
    checksum: str
    system_prompt: str

    @classmethod
    def load(cls, path: Path, *, version: str = "v1") -> EvidenceAssessmentPrompt:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("evidence-assessment prompt must not be blank")
        return cls(
            prompt_id="evidence-assessment",
            version=version,
            checksum=hashlib.sha256(text.encode()).hexdigest(),
            system_prompt=text,
        )


class AIRouterEvidenceAssessor:
    """Assess relation only; deterministic source policy and lineage remain separate."""

    def __init__(self, router: AIRouter, prompt: EvidenceAssessmentPrompt) -> None:
        self.router = router
        self.prompt = prompt

    async def assess(
        self, candidate: ResearchCandidate, claim_text: str
    ) -> EvidenceAssessment | None:
        material = {
            "claim_id": str(candidate.claim_id),
            "claim_text": claim_text,
            "candidate_url": candidate.result.url,
            "candidate_title": candidate.result.title,
            "candidate_snippet": candidate.result.snippet,
            "candidate_metadata": candidate.result.metadata,
        }
        input_hash = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        parsed: EvidenceAssessmentOutput | None = None

        def validate(response: AIResponse) -> None:
            nonlocal parsed
            try:
                output = EvidenceAssessmentOutput.model_validate(response.structured)
            except ValidationError as exc:
                raise AIInvalidResponseError(
                    "evidence assessment output failed schema validation"
                ) from exc
            if output.claim_id != candidate.claim_id:
                raise AIInvalidResponseError("evidence assessment changed claim_id")
            if output.candidate_url != candidate.result.url:
                raise AIInvalidResponseError("evidence assessment changed candidate URL")
            if output.relevant_excerpt is not None and (
                candidate.result.snippet is None
                or output.relevant_excerpt not in candidate.result.snippet
            ):
                raise AIInvalidResponseError(
                    "evidence assessment excerpt is absent from reviewed source text"
                )
            parsed = output

        artifacts = _candidate_artifacts(candidate)
        request = AIRequest(
            task_type=AITaskType.EVIDENCE_ASSESSMENT,
            system_prompt=self.prompt.system_prompt,
            input=material,
            prompt=PromptReference(
                prompt_id=self.prompt.prompt_id,
                version=self.prompt.version,
                checksum=self.prompt.checksum,
            ),
            response_format=AIResponseFormat.STRUCTURED,
            language=candidate.result.language,
            input_artifact_ids=artifacts,
            input_hash=input_hash,
        )
        routed = await self.router.execute(request, response_validator=validate)
        if parsed is None:
            raise AIInvalidResponseError("evidence assessment response was not validated")
        response = routed.response
        provider = self.router.registry.get(response.provider)
        return EvidenceAssessment(
            claim_id=parsed.claim_id,
            candidate_url=parsed.candidate_url,
            relation=parsed.relation,
            strength_score=parsed.strength_score,
            relevant_excerpt=parsed.relevant_excerpt,
            notes=parsed.notes,
            ai_provenance=EvidenceAssessmentAIProvenance(
                provider_id=response.provider,
                model_name=response.model,
                locality=provider.capabilities.locality.value,
                capabilities=provider.capabilities.model_dump(mode="json"),
                prompt_id=self.prompt.prompt_id,
                prompt_version=self.prompt.version,
                prompt_checksum=self.prompt.checksum,
                input_hash=input_hash,
                input_artifact_ids=artifacts,
                output_payload=parsed.model_dump(mode="json"),
                latency_ms=response.latency_ms,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                routing_attempts=tuple(item.model_dump(mode="json") for item in routed.attempts),
            ),
        )


def _candidate_artifacts(candidate: ResearchCandidate) -> tuple[str, ...]:
    metadata = candidate.result.metadata
    result = [f"claim:{candidate.claim_id}"]
    for key, prefix in (
        ("source_id", "source"),
        ("article_id", "article"),
        ("article_version_id", "article_version"),
    ):
        value = metadata.get(key)
        if value is not None:
            result.append(f"{prefix}:{value}")
    return tuple(result)
