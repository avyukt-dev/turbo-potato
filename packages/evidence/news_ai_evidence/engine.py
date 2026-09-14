"""Claim-driven research planning, acquisition, and evidence persistence."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from news_ai_database import (
    AIModel,
    AIRun,
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceGraphRelation,
    EvidenceItem,
    Job,
    ResearchRunClaim,
    Story,
)
from news_ai_domain import ClaimVerificationStatus
from news_ai_events import ClaimsExtractedV1, EventEnvelope, EventType, parse_event_payload
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import SearchCapability, SearchQueryFamily, SearchRequest, SearchResult
from .graph import (
    EVIDENCE_GRAPH_POLICY_VERSION,
    EvidenceDirectness,
    EvidenceOriginRole,
    EvidenceProvenanceState,
    EvidenceTemporalRole,
)
from .policy import SearchPolicy, SearchPolicyEnforcer
from .provider import (
    SearchCapabilityError,
    SearchInvalidResponseError,
    SearchProviderError,
    SearchProviderPolicyError,
    SearchProviderRateLimitError,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
)
from .registry import SearchProviderRegistry
from .semantic import semantic_key


class ResearchTargetRole(StrEnum):
    GENERAL_CONTEXT = "GENERAL_CONTEXT"
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    INDEPENDENT_REPORTING = "INDEPENDENT_REPORTING"
    CONTRADICTION = "CONTRADICTION"
    COUNTERCLAIM = "COUNTERCLAIM"


class EvidenceRelation(StrEnum):
    DIRECT_SUPPORT = "DIRECT_SUPPORT"
    INDIRECT_SUPPORT = "INDIRECT_SUPPORT"
    CONTRADICTS = "CONTRADICTS"
    QUALIFIES = "QUALIFIES"
    CONTEXT = "CONTEXT"
    PRIMARY_EVIDENCE = "PRIMARY_EVIDENCE"
    SECONDARY_EVIDENCE = "SECONDARY_EVIDENCE"


class ResearchQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    query: str = Field(min_length=1, max_length=2048)
    query_family: SearchQueryFamily
    capability: SearchCapability
    target_role: ResearchTargetRole
    language: str | None = Field(default=None, min_length=2, max_length=32)
    max_results: int = Field(default=10, ge=1, le=100)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    research_run_id: UUID
    story_id: UUID
    claim_ids: tuple[UUID, ...]
    queries: tuple[ResearchQuerySpec, ...]
    breaking_news: bool = False
    research_scope: dict[str, bool]

    @field_validator("claim_ids", "queries")
    @classmethod
    def require_nonempty(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("research plan must include claims and queries")
        return value

    @model_validator(mode="after")
    def validate_query_claims(self) -> ResearchPlan:
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("research plan claim_ids must be unique")
        claim_ids = set(self.claim_ids)
        if any(query.claim_id not in claim_ids for query in self.queries):
            raise ValueError("research query references claim outside plan")
        return self


class ResearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    query_family: SearchQueryFamily
    target_role: ResearchTargetRole
    request_id: UUID
    provider_id: str
    retrieved_at: datetime
    result: SearchResult


class EvidenceAssessmentAIProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=255)
    locality: str = Field(min_length=1, max_length=16)
    capabilities: dict[str, Any]
    prompt_id: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)
    prompt_checksum: str = Field(min_length=1, max_length=128)
    input_hash: str = Field(min_length=64, max_length=64)
    input_artifact_ids: tuple[str, ...]
    output_payload: dict[str, Any]
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    routing_attempts: tuple[dict[str, Any], ...]


class EvidenceAssessment(BaseModel):
    """Explicit claim-specific assessment; search intent never creates this implicitly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    candidate_url: str = Field(min_length=1, max_length=4096)
    relation: EvidenceRelation
    directness: EvidenceDirectness = EvidenceDirectness.UNKNOWN
    origin_role: EvidenceOriginRole = EvidenceOriginRole.UNKNOWN
    provenance_state: EvidenceProvenanceState = EvidenceProvenanceState.UNKNOWN
    temporal_role: EvidenceTemporalRole = EvidenceTemporalRole.UNKNOWN
    strength_score: Decimal | None = Field(default=None, ge=0, le=1)
    relevant_excerpt: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)
    ai_provenance: EvidenceAssessmentAIProvenance | None = None

    @model_validator(mode="after")
    def forbid_source_policy_relations(self) -> EvidenceAssessment:
        if self.relation in {
            EvidenceRelation.PRIMARY_EVIDENCE,
            EvidenceRelation.SECONDARY_EVIDENCE,
        }:
            raise ValueError("evidence assessor cannot assign source-authority relations")
        if (
            self.relation is EvidenceRelation.DIRECT_SUPPORT
            and self.directness is not EvidenceDirectness.DIRECT
        ):
            raise ValueError("direct support must be direct")
        if (
            self.relation is EvidenceRelation.INDIRECT_SUPPORT
            and self.directness is not EvidenceDirectness.INDIRECT
        ):
            raise ValueError("indirect support must be indirect")
        return self


class ResearchQueryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    query_family: SearchQueryFamily
    provider_id: str | None = None
    error_code: str
    error_message: str
    retryable: bool


class ResearchCollectionDisposition(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


class ResearchCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    research_run_id: UUID
    candidates: tuple[ResearchCandidate, ...] = ()
    assessments: tuple[EvidenceAssessment, ...] = ()
    failures: tuple[ResearchQueryFailure, ...] = ()
    successful_query_count: int = Field(default=0, ge=0)

    @property
    def disposition(self) -> ResearchCollectionDisposition:
        if self.successful_query_count:
            return (
                ResearchCollectionDisposition.PARTIAL
                if self.failures
                else ResearchCollectionDisposition.COMPLETE
            )
        if any(item.retryable for item in self.failures):
            return ResearchCollectionDisposition.RETRYABLE_FAILURE
        return ResearchCollectionDisposition.PERMANENT_FAILURE


@dataclass(frozen=True, slots=True)
class ResearchCollectionTask:
    plan: ResearchPlan
    claim_texts: dict[UUID, str]


@dataclass(frozen=True, slots=True)
class ResearchCurrency:
    current_claim_ids: tuple[UUID, ...]
    stale_claim_ids: tuple[UUID, ...]

    @property
    def fully_stale(self) -> bool:
        return not self.current_claim_ids and bool(self.stale_claim_ids)


@dataclass(frozen=True, slots=True)
class ResearchRequestResult:
    research_run_id: UUID
    event_id: UUID
    claim_ids: tuple[UUID, ...]
    claim_generations: dict[UUID, int]
    created: bool

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "event_id": str(self.event_id),
            "claim_ids": [str(item) for item in self.claim_ids],
            "claim_generations": {
                str(claim_id): generation for claim_id, generation in self.claim_generations.items()
            },
            "created": self.created,
        }


@dataclass(frozen=True, slots=True)
class EvidenceCollectionResult:
    research_run_id: UUID
    event_id: UUID | None
    evidence_ids: tuple[UUID, ...]
    current_claim_ids: tuple[UUID, ...]
    stale_claim_ids: tuple[UUID, ...]
    claim_evidence_count: int
    query_failure_count: int

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "event_id": str(self.event_id) if self.event_id is not None else None,
            "evidence_ids": [str(item) for item in self.evidence_ids],
            "current_claim_ids": [str(item) for item in self.current_claim_ids],
            "stale_claim_ids": [str(item) for item in self.stale_claim_ids],
            "claim_evidence_count": self.claim_evidence_count,
            "query_failure_count": self.query_failure_count,
        }


class EvidenceAssessor(Protocol):
    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None: ...


SearchProviderSelector = Callable[[SearchRequest, tuple[str, ...]], str]


RESEARCH_PLANNER_METHODOLOGY_VERSION = "research-planner-v1"


class EvidenceEngine:
    """Research claims without allowing search ranking or query intent to become factual truth."""

    def __init__(
        self,
        registry: SearchProviderRegistry,
        search_policy: SearchPolicy,
        provider_selector: SearchProviderSelector,
        assessor: EvidenceAssessor,
        *,
        source_resolver: Any | None = None,
        producer: str = "research-worker",
        producer_version: str = "0.1.0",
        methodology_version: str = RESEARCH_PLANNER_METHODOLOGY_VERSION,
    ) -> None:
        if not methodology_version.strip():
            raise ValueError("research planner methodology version must not be empty")
        self.registry = registry
        self.search_policy = search_policy
        self.enforcer = SearchPolicyEnforcer(search_policy)
        self.provider_selector = provider_selector
        self.assessor = assessor
        self.source_resolver = source_resolver
        self.producer = producer
        self.producer_version = producer_version
        self.methodology_version = methodology_version

    def request_research(
        self,
        session: Session,
        triggering_event: EventEnvelope,
        *,
        breaking_news: bool = False,
    ) -> ResearchRequestResult:
        story, claims = self._claims_from_extracted_event(session, triggering_event, lock=True)
        payload = parse_event_payload(
            triggering_event.event_type,
            triggering_event.schema_version,
            triggering_event.payload,
            ClaimsExtractedV1,
        )
        operation_key = semantic_key(
            "research",
            {
                "methodology_version": self.methodology_version,
                "evidence_graph_policy_version": EVIDENCE_GRAPH_POLICY_VERSION,
                "story_id": story.id,
                "claim_ids": sorted(payload.claim_ids, key=str),
                "ai_run_id": payload.ai_run_id,
                "model_id": payload.model_id,
                "breaking_news": breaking_news,
                "language": story.language,
                "search_policy": self.search_policy.model_dump(mode="json"),
                "claims": [
                    {
                        "id": claim.id,
                        "text": claim.claim_text,
                        "normalized": claim.normalized_claim,
                        "extraction_context_hash": (claim.claim_metadata or {}).get(
                            "extraction_context_hash"
                        ),
                    }
                    for claim in sorted(claims, key=lambda item: str(item.id))
                ],
            },
        )
        existing = session.scalar(select(Job).where(Job.semantic_key == operation_key))
        if existing is not None:
            return self._existing_research_request(session, existing, operation_key)

        research_run_id = uuid4()
        plan = self._build_plan(
            research_run_id=research_run_id,
            story=story,
            claims=claims,
            breaking_news=breaking_news,
        )
        job = Job(
            id=research_run_id,
            job_type="RESEARCH",
            status="PENDING",
            priority=2,
            payload={
                "plan": plan.model_dump(mode="json"),
                "methodology_version": self.methodology_version,
                "evidence_graph_policy_version": EVIDENCE_GRAPH_POLICY_VERSION,
                "triggering_event_id": str(triggering_event.event_id),
            },
            semantic_key=operation_key,
        )
        session.add(job)
        session.flush()
        claim_generations: dict[UUID, int] = {}
        for claim in claims:
            generation = claim.research_generation + 1
            claim.research_generation = generation
            claim.current_research_run_id = research_run_id
            claim.current_fact_check_id = None
            claim.status = ClaimVerificationStatus.UNASSESSED
            claim_generations[claim.id] = generation
            session.add(
                ResearchRunClaim(
                    research_run_id=research_run_id,
                    claim_id=claim.id,
                    research_generation=generation,
                )
            )
        job.payload = {
            **job.payload,
            "claim_generations": {
                str(claim_id): generation for claim_id, generation in claim_generations.items()
            },
        }
        story.verification_semantic_key = None
        event = EventEnvelope(
            event_type=EventType.EVIDENCE_REQUESTED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="research_run",
            aggregate_id=research_run_id,
            correlation_id=triggering_event.correlation_id,
            causation_id=triggering_event.event_id,
            idempotency_key=f"evidence.requested:{operation_key}",
            payload={
                "story_id": str(story.id),
                "claim_ids": [str(claim.id) for claim in claims],
                "research_scope": {
                    "primary_sources": plan.research_scope["primary_sources"],
                    "independent_corroboration": plan.research_scope["independent_corroboration"],
                    "contradiction_search": plan.research_scope["contradiction_search"],
                },
            },
        )
        session.add(build_outbox_record(event))
        session.flush()
        return ResearchRequestResult(
            research_run_id=research_run_id,
            event_id=event.event_id,
            claim_ids=tuple(claim.id for claim in claims),
            claim_generations=claim_generations,
            created=True,
        )

    @staticmethod
    def _existing_research_request(
        session: Session,
        job: Job,
        operation_key: str,
    ) -> ResearchRequestResult:
        outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.EVIDENCE_REQUESTED.value,
                EventOutbox.aggregate_id == job.id,
                EventOutbox.idempotency_key == f"evidence.requested:{operation_key}",
            )
        )
        if outbox is None:
            raise ValueError("semantic research job is missing its outbox event")
        raw_plan = job.payload.get("plan")
        if raw_plan is None:
            raise ValueError("semantic research job is missing its persisted plan")
        plan = ResearchPlan.model_validate(raw_plan)
        generations = {
            row.claim_id: row.research_generation
            for row in session.scalars(
                select(ResearchRunClaim).where(ResearchRunClaim.research_run_id == job.id)
            )
        }
        if set(generations) != set(plan.claim_ids):
            raise ValueError("semantic research job is missing claim generation provenance")
        return ResearchRequestResult(
            research_run_id=job.id,
            event_id=outbox.event_id,
            claim_ids=plan.claim_ids,
            claim_generations=generations,
            created=False,
        )

    def load_collection_task(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> ResearchCollectionTask:
        self._validate_requested_event(event)
        job = session.get(Job, event.aggregate_id)
        if job is None or job.job_type != "RESEARCH":
            raise ValueError("evidence.requested references unknown research run")
        if job.status not in {"PENDING", "RUNNING"}:
            raise ValueError(f"research run is not collectible from status {job.status!r}")
        if job.payload.get("evidence_graph_policy_version") != EVIDENCE_GRAPH_POLICY_VERSION:
            raise ValueError("research run requires replanning under current evidence semantics")
        raw_plan = job.payload.get("plan")
        if raw_plan is None:
            raise ValueError("research job is missing persisted plan")
        plan = ResearchPlan.model_validate(raw_plan)
        if plan.research_run_id != job.id:
            raise ValueError("research plan id does not match job id")
        self._validate_event_against_plan(event, plan)
        claims = self._load_claims(session, plan.story_id, plan.claim_ids)
        return ResearchCollectionTask(
            plan=plan,
            claim_texts={claim.id: claim.claim_text for claim in claims},
        )

    def collection_currency(
        self,
        session: Session,
        task: ResearchCollectionTask,
        *,
        lock: bool = False,
    ) -> ResearchCurrency:
        claims = self._load_claims(session, task.plan.story_id, task.plan.claim_ids, lock=lock)
        generations = {
            row.claim_id: row.research_generation
            for row in session.scalars(
                select(ResearchRunClaim).where(
                    ResearchRunClaim.research_run_id == task.plan.research_run_id
                )
            )
        }
        if set(generations) != set(task.plan.claim_ids):
            raise ValueError("research run is missing claim generation provenance")
        current = tuple(
            claim.id
            for claim in claims
            if claim.current_research_run_id == task.plan.research_run_id
            and claim.research_generation == generations[claim.id]
        )
        current_set = set(current)
        return ResearchCurrency(
            current_claim_ids=current,
            stale_claim_ids=tuple(claim.id for claim in claims if claim.id not in current_set),
        )

    def persist_superseded_collection(
        self,
        session: Session,
        event: EventEnvelope,
        task: ResearchCollectionTask,
    ) -> EvidenceCollectionResult:
        self._validate_requested_event(event)
        job = session.scalar(select(Job).where(Job.id == event.aggregate_id).with_for_update())
        if job is None or job.job_type != "RESEARCH":
            raise ValueError("research run disappeared before stale classification")
        currency = self.collection_currency(session, task, lock=True)
        if not currency.fully_stale:
            raise ValueError("research run became current before stale classification")
        job.status = "SUPERSEDED"
        job.result = {
            "evidence_ids": [],
            "claim_evidence_count": 0,
            "candidate_count": 0,
            "unassessed_candidate_count": 0,
            "query_failures": [],
            "current_claim_ids": [],
            "stale_claim_ids": [str(item) for item in currency.stale_claim_ids],
        }
        return EvidenceCollectionResult(
            research_run_id=task.plan.research_run_id,
            event_id=None,
            evidence_ids=(),
            current_claim_ids=(),
            stale_claim_ids=currency.stale_claim_ids,
            claim_evidence_count=0,
            query_failure_count=0,
        )

    async def collect(
        self,
        task: ResearchCollectionTask,
        *,
        active_claim_ids: tuple[UUID, ...] | None = None,
    ) -> ResearchCollection:
        candidates: list[ResearchCandidate] = []
        assessments: list[EvidenceAssessment] = []
        failures: list[ResearchQueryFailure] = []
        successful_query_count = 0
        active_claim_set = set(active_claim_ids or task.plan.claim_ids)

        for query in task.plan.queries:
            if query.claim_id not in active_claim_set:
                continue
            request = SearchRequest(
                query=query.query,
                query_family=query.query_family,
                capability=query.capability,
                claim_id=query.claim_id,
                story_id=task.plan.story_id,
                language=query.language,
                max_results=query.max_results,
                metadata={"target_role": query.target_role.value},
            )
            try:
                constraints = self.enforcer.prepare(request, breaking_news=task.plan.breaking_news)
            except (SearchProviderPolicyError, ValidationError, ValueError) as exc:
                failures.append(
                    self._query_failure(query, None, type(exc).__name__, str(exc), False)
                )
                continue
            compatible = self.registry.compatible_provider_ids(constraints.request)
            if not compatible:
                failures.append(
                    self._query_failure(
                        query,
                        None,
                        "NO_COMPATIBLE_PROVIDER",
                        "no compatible provider",
                        False,
                    )
                )
                continue

            provider_id = self.provider_selector(constraints.request, compatible)
            if provider_id not in compatible:
                failures.append(
                    self._query_failure(
                        query,
                        provider_id,
                        "UNAUTHORIZED_PROVIDER_SELECTION",
                        f"search provider selector returned unauthorized provider {provider_id!r}",
                        False,
                    )
                )
                continue

            try:
                response = await asyncio.wait_for(
                    self.registry.execute(provider_id, constraints.request),
                    timeout=constraints.request.timeout_seconds,
                )
            except TimeoutError:
                failures.append(
                    self._query_failure(
                        query,
                        provider_id,
                        "TIMEOUT",
                        "search provider exceeded research timeout",
                        True,
                    )
                )
                continue
            except SearchProviderError as exc:
                failures.append(
                    self._query_failure(
                        query,
                        provider_id,
                        type(exc).__name__,
                        str(exc),
                        isinstance(
                            exc,
                            (
                                SearchProviderTimeoutError,
                                SearchProviderUnavailableError,
                                SearchProviderRateLimitError,
                            ),
                        )
                        or not isinstance(
                            exc,
                            (
                                SearchProviderPolicyError,
                                SearchInvalidResponseError,
                                SearchCapabilityError,
                            ),
                        ),
                    )
                )
                continue
            except (ValidationError, ValueError) as exc:
                failures.append(
                    self._query_failure(query, provider_id, type(exc).__name__, str(exc), False)
                )
                continue

            successful_query_count += 1
            for result in response.results:
                candidate = ResearchCandidate(
                    claim_id=query.claim_id,
                    query_family=query.query_family,
                    target_role=query.target_role,
                    request_id=response.request_id,
                    provider_id=response.provider_id,
                    retrieved_at=response.retrieved_at,
                    result=result,
                )
                candidates.append(candidate)
                assessment = await self.assessor.assess(
                    candidate,
                    task.claim_texts[query.claim_id],
                )
                if assessment is not None:
                    if assessment.claim_id != query.claim_id:
                        raise ValueError("evidence assessor changed candidate claim_id")
                    if assessment.candidate_url != result.url:
                        raise ValueError("evidence assessor changed candidate URL")
                    assessments.append(assessment)

        return ResearchCollection(
            research_run_id=task.plan.research_run_id,
            candidates=tuple(candidates),
            assessments=tuple(assessments),
            failures=tuple(failures),
            successful_query_count=successful_query_count,
        )

    def persist_collection(
        self,
        session: Session,
        event: EventEnvelope,
        task: ResearchCollectionTask,
        collection: ResearchCollection,
    ) -> EvidenceCollectionResult:
        self._validate_requested_event(event)
        if collection.research_run_id != task.plan.research_run_id:
            raise ValueError("research collection does not match task")
        job = session.scalar(select(Job).where(Job.id == event.aggregate_id).with_for_update())
        if job is None or job.job_type != "RESEARCH":
            raise ValueError("research run disappeared before persistence")
        if job.status == "COMPLETED":
            raise ValueError("research run is already completed")
        if job.status not in {"PENDING", "RUNNING"}:
            raise ValueError(f"research run cannot complete from status {job.status!r}")
        if job.payload.get("evidence_graph_policy_version") != EVIDENCE_GRAPH_POLICY_VERSION:
            raise ValueError("research run requires replanning under current evidence semantics")
        self._validate_event_against_plan(event, task.plan)
        currency = self.collection_currency(session, task, lock=True)
        current_claim_ids = currency.current_claim_ids
        stale_claim_ids = currency.stale_claim_ids

        if not current_claim_ids:
            job.status = "SUPERSEDED"
            job.result = {
                "evidence_ids": [],
                "claim_evidence_count": 0,
                "candidate_count": len(collection.candidates),
                "unassessed_candidate_count": len(collection.candidates),
                "query_failures": [item.model_dump(mode="json") for item in collection.failures],
                "current_claim_ids": [],
                "stale_claim_ids": [str(item) for item in stale_claim_ids],
            }
            return EvidenceCollectionResult(
                research_run_id=task.plan.research_run_id,
                event_id=None,
                evidence_ids=(),
                current_claim_ids=(),
                stale_claim_ids=stale_claim_ids,
                claim_evidence_count=0,
                query_failure_count=len(collection.failures),
            )

        current_claim_set = set(current_claim_ids)
        current_candidates = tuple(
            item for item in collection.candidates if item.claim_id in current_claim_set
        )
        current_assessments = tuple(
            item for item in collection.assessments if item.claim_id in current_claim_set
        )
        assessment_map = self._assessment_map(current_assessments)
        candidate_keys = {(item.claim_id, item.result.url) for item in current_candidates}
        if not set(assessment_map).issubset(candidate_keys):
            raise ValueError("evidence assessment references candidate outside research collection")

        assessed_candidates = tuple(
            item
            for item in current_candidates
            if (item.claim_id, item.result.url) in assessment_map
        )
        resolutions = (
            self.source_resolver.resolve(session, assessed_candidates)
            if self.source_resolver is not None
            else {}
        )
        grouped = self._group_candidates(assessed_candidates)
        evidence_ids: list[UUID] = []
        relation_count = 0
        evidence_by_claim_url: dict[tuple[UUID, str], UUID] = {}
        pending_graph_relations = []

        for (claim_id, url), candidates in grouped.items():
            first = candidates[0]
            assessment_records = [assessment_map[(claim_id, url)]]
            resolution = resolutions.get((claim_id, url))
            source = resolution.source if resolution is not None else None
            version = resolution.version if resolution is not None else None
            article = resolution.article if resolution is not None else None
            authority = resolution.authority if resolution is not None else None
            lineage = resolution.lineage if resolution is not None else None
            assessment_ai_run_ids = [
                str(self._persist_assessment_ai_run(session, event, assessment))
                for assessment in assessment_records
                if assessment.ai_provenance is not None
            ]
            evidence = EvidenceItem(
                source_id=source.id if source is not None else None,
                title=first.result.title,
                url=url,
                evidence_type=first.result.candidate_type.value,
                published_at=(
                    article.published_at if article is not None else first.result.published_at
                ),
                retrieved_at=(
                    version.retrieved_at if version is not None else _earliest_retrieved(candidates)
                ),
                content_hash=(
                    version.content_hash if version is not None else _candidate_hash(first.result)
                ),
                excerpt=assessment_records[0].relevant_excerpt or first.result.snippet,
                evidence_metadata={
                    "source_name": first.result.source_name,
                    "language": first.result.language,
                    "research_run_id": str(task.plan.research_run_id),
                    "research_generation": int(job.payload["claim_generations"][str(claim_id)]),
                    "source_id": str(source.id) if source is not None else None,
                    "article_id": str(article.id) if article is not None else None,
                    "article_version_id": str(version.id) if version is not None else None,
                    "article_version_number": (
                        version.version_number if version is not None else None
                    ),
                    "canonical_url": article.canonical_url if article is not None else url,
                    "content_hash": (
                        version.content_hash
                        if version is not None
                        else _candidate_hash(first.result)
                    ),
                    "source_level": (
                        int(authority.effective_level) if authority is not None else None
                    ),
                    "source_policy_basis": authority.basis if authority is not None else None,
                    "lineage_status": lineage.status.value if lineage is not None else "UNRESOLVED",
                    "lineage_basis": (
                        lineage.basis
                        if lineage is not None
                        else "source-policy-resolver-not-configured"
                    ),
                    "independence_group": (
                        lineage.independence_group if lineage is not None else None
                    ),
                    "originating_reference": (
                        lineage.originating_reference if lineage is not None else None
                    ),
                    "assessment_ai_run_ids": assessment_ai_run_ids,
                    "search_provenance": [self._candidate_provenance(item) for item in candidates],
                    "relationship_assessments": [
                        assessment.model_dump(mode="json") for assessment in assessment_records
                    ],
                },
            )
            session.add(evidence)
            session.flush()
            evidence_ids.append(evidence.id)
            evidence_by_claim_url[(claim_id, url)] = evidence.id
            if resolution is not None:
                pending_graph_relations.extend(
                    (claim_id, evidence.id, spec) for spec in resolution.graph_relations
                )

            for assessment in assessment_records:
                session.add(
                    ClaimEvidence(
                        claim_id=assessment.claim_id,
                        evidence_id=evidence.id,
                        relation=assessment.relation.value,
                        strength_score=assessment.strength_score,
                        directness=assessment.directness.value,
                        origin_role=(
                            resolution.origin_role.value
                            if resolution is not None
                            else EvidenceOriginRole.UNKNOWN.value
                        ),
                        provenance_state=(
                            resolution.provenance_state.value
                            if resolution is not None
                            else EvidenceProvenanceState.UNKNOWN.value
                        ),
                        temporal_role=assessment.temporal_role.value,
                        semantics_policy_version=EVIDENCE_GRAPH_POLICY_VERSION,
                    )
                )
                relation_count += 1

        seen_graph_relations = set()
        for claim_id, source_evidence_id, spec in pending_graph_relations:
            target_id = (
                evidence_by_claim_url.get((claim_id, spec.external_reference))
                if spec.external_reference is not None
                else spec.target_evidence_id
            )
            if target_id == source_evidence_id and spec.external_reference is not None:
                # An explicit originating reference may name the reviewed document;
                # retain that reference rather than inventing a self-edge.
                target_id = None
            if target_id is not None and (
                target_id == source_evidence_id
                or target_id
                not in {
                    value
                    for (owner, _), value in evidence_by_claim_url.items()
                    if owner == claim_id
                }
            ):
                raise ValueError("graph target is outside current claim research evidence")
            identity = (
                source_evidence_id,
                spec.relation_type,
                target_id,
                None if target_id is not None else spec.external_reference,
            )
            if identity in seen_graph_relations:
                continue
            seen_graph_relations.add(identity)
            session.add(
                EvidenceGraphRelation(
                    source_evidence_id=source_evidence_id,
                    target_evidence_id=target_id,
                    external_reference=None if target_id is not None else spec.external_reference,
                    relation_type=spec.relation_type.value,
                    basis=spec.basis,
                    policy_version=EVIDENCE_GRAPH_POLICY_VERSION,
                    research_run_id=task.plan.research_run_id,
                    research_generation=int(job.payload["claim_generations"][str(claim_id)]),
                )
            )

        job.status = "COMPLETED"
        job.result = {
            "evidence_graph_policy_version": EVIDENCE_GRAPH_POLICY_VERSION,
            "evidence_ids": [str(item) for item in evidence_ids],
            "claim_evidence_count": relation_count,
            "candidate_count": len(collection.candidates),
            "unassessed_candidate_count": len(current_candidates) - len(assessed_candidates),
            "query_failures": [item.model_dump(mode="json") for item in collection.failures],
            "current_claim_ids": [str(item) for item in current_claim_ids],
            "stale_claim_ids": [str(item) for item in stale_claim_ids],
        }
        event_out = EventEnvelope(
            event_type=EventType.EVIDENCE_COLLECTED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="research_run",
            aggregate_id=task.plan.research_run_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"evidence.collected:{task.plan.research_run_id}",
            payload={
                "story_id": str(task.plan.story_id),
                "claim_ids": [str(item) for item in current_claim_ids],
                "evidence_ids": [str(item) for item in evidence_ids],
                "research_run_id": str(task.plan.research_run_id),
            },
        )
        session.add(build_outbox_record(event_out))
        session.flush()
        return EvidenceCollectionResult(
            research_run_id=task.plan.research_run_id,
            event_id=event_out.event_id,
            evidence_ids=tuple(evidence_ids),
            current_claim_ids=current_claim_ids,
            stale_claim_ids=stale_claim_ids,
            claim_evidence_count=relation_count,
            query_failure_count=len(collection.failures),
        )

    def existing_collection_result(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> dict[str, Any] | None:
        self._validate_requested_event(event)
        job = session.get(Job, event.aggregate_id)
        if (
            job is None
            or job.job_type != "RESEARCH"
            or job.status
            not in {
                "COMPLETED",
                "SUPERSEDED",
            }
        ):
            return None
        return job.result

    def _build_plan(
        self,
        *,
        research_run_id: UUID,
        story: Story,
        claims: list[Claim],
        breaking_news: bool,
    ) -> ResearchPlan:
        queries: list[ResearchQuerySpec] = []
        rules = self.search_policy.rules
        for claim in claims:
            text = _bounded_query(claim.claim_text)
            queries.append(
                ResearchQuerySpec(
                    claim_id=claim.id,
                    query=text,
                    query_family=SearchQueryFamily.EXACT_CLAIM,
                    capability=SearchCapability.WEB,
                    target_role=ResearchTargetRole.GENERAL_CONTEXT,
                    language=story.language,
                )
            )
            if rules.search_independent_reporting:
                queries.append(
                    ResearchQuerySpec(
                        claim_id=claim.id,
                        query=text,
                        query_family=SearchQueryFamily.ENTITY_EVENT,
                        capability=SearchCapability.NEWS,
                        target_role=ResearchTargetRole.INDEPENDENT_REPORTING,
                        language=story.language,
                    )
                )
            if rules.search_primary_sources:
                queries.append(
                    ResearchQuerySpec(
                        claim_id=claim.id,
                        query=_bounded_query(f"{text} official primary document"),
                        query_family=SearchQueryFamily.PRIMARY_DOCUMENT,
                        capability=SearchCapability.WEB,
                        target_role=ResearchTargetRole.PRIMARY_SOURCE,
                        language=story.language,
                    )
                )
            if rules.search_contradictions:
                queries.append(
                    ResearchQuerySpec(
                        claim_id=claim.id,
                        query=_bounded_query(f"{text} contradiction evidence"),
                        query_family=SearchQueryFamily.CONTRADICTION,
                        capability=SearchCapability.WEB,
                        target_role=ResearchTargetRole.CONTRADICTION,
                        language=story.language,
                    )
                )
            if rules.search_counterclaims:
                queries.append(
                    ResearchQuerySpec(
                        claim_id=claim.id,
                        query=_bounded_query(f"{text} counterclaim"),
                        query_family=SearchQueryFamily.COUNTERCLAIM,
                        capability=SearchCapability.WEB,
                        target_role=ResearchTargetRole.COUNTERCLAIM,
                        language=story.language,
                    )
                )
        return ResearchPlan(
            research_run_id=research_run_id,
            story_id=story.id,
            claim_ids=tuple(claim.id for claim in claims),
            queries=tuple(queries),
            breaking_news=breaking_news,
            research_scope={
                "primary_sources": rules.search_primary_sources,
                "independent_corroboration": rules.search_independent_reporting,
                "contradiction_search": rules.search_contradictions,
                "counterclaim_search": rules.search_counterclaims,
            },
        )

    @staticmethod
    def _claims_from_extracted_event(
        session: Session,
        event: EventEnvelope,
        *,
        lock: bool = False,
    ) -> tuple[Story, list[Claim]]:
        if event.event_type != EventType.CLAIMS_EXTRACTED:
            raise ValueError("research planning requires claims.extracted")
        if event.aggregate_type != "story":
            raise ValueError("claims.extracted must use story aggregate")
        story_id = UUID(str(event.payload.get("story_id")))
        if story_id != event.aggregate_id:
            raise ValueError("claims.extracted story_id must match aggregate_id")
        raw_claim_ids = event.payload.get("claim_ids")
        if not isinstance(raw_claim_ids, list) or not raw_claim_ids:
            raise ValueError("claims.extracted must contain non-empty claim_ids")
        claim_ids = tuple(UUID(str(item)) for item in raw_claim_ids)
        statement = select(Story).where(Story.id == story_id)
        if lock:
            statement = statement.with_for_update()
        story = session.scalar(statement)
        if story is None:
            raise ValueError("research story does not exist")
        return story, EvidenceEngine._load_claims(session, story_id, claim_ids, lock=lock)

    @staticmethod
    def _load_claims(
        session: Session,
        story_id: UUID,
        claim_ids: tuple[UUID, ...],
        *,
        lock: bool = False,
    ) -> list[Claim]:
        if session.get(Story, story_id) is None:
            raise ValueError("research story does not exist")
        statement = select(Claim).where(Claim.id.in_(claim_ids)).order_by(Claim.id)
        if lock:
            statement = statement.with_for_update()
        claims = list(session.scalars(statement))
        by_id = {claim.id: claim for claim in claims}
        if set(by_id) != set(claim_ids):
            raise ValueError("research plan references missing claims")
        ordered = [by_id[claim_id] for claim_id in claim_ids]
        if any(claim.story_id != story_id for claim in ordered):
            raise ValueError("research plan contains claim from another story")
        return ordered

    @staticmethod
    def _validate_requested_event(event: EventEnvelope) -> None:
        if event.event_type != EventType.EVIDENCE_REQUESTED:
            raise ValueError("evidence collection requires evidence.requested")
        if event.aggregate_type != "research_run":
            raise ValueError("evidence.requested must use research_run aggregate")

    @staticmethod
    def _validate_event_against_plan(event: EventEnvelope, plan: ResearchPlan) -> None:
        if event.aggregate_id != plan.research_run_id:
            raise ValueError("evidence.requested aggregate does not match research plan")
        if UUID(str(event.payload.get("story_id"))) != plan.story_id:
            raise ValueError("evidence.requested story does not match research plan")
        event_claims = tuple(UUID(str(item)) for item in event.payload.get("claim_ids", []))
        if event_claims != plan.claim_ids:
            raise ValueError("evidence.requested claims do not match research plan")

    @staticmethod
    def _query_failure(
        query: ResearchQuerySpec,
        provider_id: str | None,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> ResearchQueryFailure:
        return ResearchQueryFailure(
            claim_id=query.claim_id,
            query_family=query.query_family,
            provider_id=provider_id,
            error_code=error_code,
            error_message=error_message[:1000],
            retryable=retryable,
        )

    @staticmethod
    def _group_candidates(
        candidates: tuple[ResearchCandidate, ...],
    ) -> dict[tuple[UUID, str], list[ResearchCandidate]]:
        grouped: dict[tuple[UUID, str], list[ResearchCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault((candidate.claim_id, candidate.result.url), []).append(candidate)
        return grouped

    @staticmethod
    def _assessment_map(
        assessments: tuple[EvidenceAssessment, ...],
    ) -> dict[tuple[UUID, str], EvidenceAssessment]:
        result: dict[tuple[UUID, str], EvidenceAssessment] = {}
        for assessment in assessments:
            key = (assessment.claim_id, assessment.candidate_url)
            existing = result.get(key)
            if existing is not None and existing != assessment:
                raise ValueError("conflicting evidence assessments for same claim and candidate")
            result[key] = assessment
        return result

    @staticmethod
    def _candidate_provenance(candidate: ResearchCandidate) -> dict[str, Any]:
        return {
            "claim_id": str(candidate.claim_id),
            "query_family": candidate.query_family.value,
            "target_role": candidate.target_role.value,
            "request_id": str(candidate.request_id),
            "provider_id": candidate.provider_id,
            "rank": candidate.result.rank,
            "retrieved_at": candidate.retrieved_at.isoformat(),
            "source_version": candidate.result.metadata,
        }

    @staticmethod
    def _persist_assessment_ai_run(
        session: Session,
        event: EventEnvelope,
        assessment: EvidenceAssessment,
    ) -> UUID:
        provenance = assessment.ai_provenance
        if provenance is None:
            raise ValueError("assessment has no AI provenance")
        model = session.scalar(
            select(AIModel)
            .where(
                AIModel.provider == provenance.provider_id,
                AIModel.model_name == provenance.model_name,
            )
            .with_for_update()
        )
        if model is None:
            model = AIModel(
                provider=provenance.provider_id,
                model_name=provenance.model_name,
                locality=provenance.locality,
                capabilities=provenance.capabilities,
                enabled=True,
                model_metadata={},
            )
            session.add(model)
            session.flush()
        run = AIRun(
            ai_model_id=model.id,
            task_type="EVIDENCE_ASSESSMENT",
            prompt_id=provenance.prompt_id,
            prompt_version=provenance.prompt_version,
            prompt_checksum=provenance.prompt_checksum,
            input_artifact_ids=list(provenance.input_artifact_ids),
            input_hash=provenance.input_hash,
            output_payload=provenance.output_payload,
            status="SUCCEEDED",
            validation_status="VALIDATED",
            latency_ms=provenance.latency_ms,
            input_tokens=provenance.input_tokens,
            output_tokens=provenance.output_tokens,
            correlation_id=event.correlation_id,
            routing_attempts=list(provenance.routing_attempts),
        )
        session.add(run)
        session.flush()
        return run.id


def _bounded_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("claim text cannot produce an empty research query")
    if len(normalized) > 1900:
        raise ValueError("claim text is too long for bounded research query planning")
    return normalized


def _candidate_hash(result: SearchResult) -> str:
    material = "\n".join((result.url, result.title or "", result.snippet or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _earliest_retrieved(candidates: list[ResearchCandidate]) -> datetime:
    return min(item.retrieved_at for item in candidates)
