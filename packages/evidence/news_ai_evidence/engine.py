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

from news_ai_database import Claim, ClaimEvidence, EventOutbox, EvidenceItem, Job, Story
from news_ai_events import ClaimsExtractedV1, EventEnvelope, EventType, parse_event_payload
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import SearchCapability, SearchQueryFamily, SearchRequest, SearchResult
from .policy import SearchPolicy, SearchPolicyEnforcer
from .provider import SearchProviderError, SearchProviderPolicyError
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


class EvidenceAssessment(BaseModel):
    """Explicit claim-specific assessment; search intent never creates this implicitly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    candidate_url: str = Field(min_length=1, max_length=4096)
    relation: EvidenceRelation
    strength_score: Decimal | None = Field(default=None, ge=0, le=1)
    source_level: int | None = Field(default=None, ge=1, le=4)
    independence_group: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)


class ResearchQueryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    query_family: SearchQueryFamily
    provider_id: str | None = None
    error_code: str
    error_message: str


class ResearchCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    research_run_id: UUID
    candidates: tuple[ResearchCandidate, ...] = ()
    assessments: tuple[EvidenceAssessment, ...] = ()
    failures: tuple[ResearchQueryFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchCollectionTask:
    plan: ResearchPlan
    claim_texts: dict[UUID, str]


@dataclass(frozen=True, slots=True)
class ResearchRequestResult:
    research_run_id: UUID
    event_id: UUID
    claim_ids: tuple[UUID, ...]
    created: bool

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "event_id": str(self.event_id),
            "claim_ids": [str(item) for item in self.claim_ids],
            "created": self.created,
        }


@dataclass(frozen=True, slots=True)
class EvidenceCollectionResult:
    research_run_id: UUID
    event_id: UUID
    evidence_ids: tuple[UUID, ...]
    claim_evidence_count: int
    query_failure_count: int

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "event_id": str(self.event_id),
            "evidence_ids": [str(item) for item in self.evidence_ids],
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


class EvidenceEngine:
    """Research claims without allowing search ranking or query intent to become factual truth."""

    def __init__(
        self,
        registry: SearchProviderRegistry,
        search_policy: SearchPolicy,
        provider_selector: SearchProviderSelector,
        assessor: EvidenceAssessor,
        *,
        producer: str = "research-worker",
        producer_version: str = "0.1.0",
    ) -> None:
        self.registry = registry
        self.search_policy = search_policy
        self.enforcer = SearchPolicyEnforcer(search_policy)
        self.provider_selector = provider_selector
        self.assessor = assessor
        self.producer = producer
        self.producer_version = producer_version

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
        session.add(
            Job(
                id=research_run_id,
                job_type="RESEARCH",
                status="PENDING",
                priority=2,
                payload={
                    "plan": plan.model_dump(mode="json"),
                    "triggering_event_id": str(triggering_event.event_id),
                },
                semantic_key=operation_key,
            )
        )
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
        return ResearchRequestResult(
            research_run_id=job.id,
            event_id=outbox.event_id,
            claim_ids=plan.claim_ids,
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

    async def collect(self, task: ResearchCollectionTask) -> ResearchCollection:
        candidates: list[ResearchCandidate] = []
        assessments: list[EvidenceAssessment] = []
        failures: list[ResearchQueryFailure] = []

        for query in task.plan.queries:
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
            constraints = self.enforcer.prepare(request, breaking_news=task.plan.breaking_news)
            compatible = self.registry.compatible_provider_ids(constraints.request)
            if not compatible:
                failures.append(
                    self._query_failure(
                        query,
                        None,
                        "NO_COMPATIBLE_PROVIDER",
                        "no compatible provider",
                    )
                )
                continue

            provider_id = self.provider_selector(constraints.request, compatible)
            if provider_id not in compatible:
                raise SearchProviderPolicyError(
                    f"search provider selector returned unauthorized provider {provider_id!r}"
                )

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
                    )
                )
                continue
            except SearchProviderError as exc:
                failures.append(
                    self._query_failure(query, provider_id, type(exc).__name__, str(exc))
                )
                continue

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
        self._validate_event_against_plan(event, task.plan)
        self._load_claims(session, task.plan.story_id, task.plan.claim_ids)

        assessment_map = self._assessment_map(collection.assessments)
        candidate_keys = {(item.claim_id, item.result.url) for item in collection.candidates}
        if not set(assessment_map).issubset(candidate_keys):
            raise ValueError("evidence assessment references candidate outside research collection")

        assessed_candidates = tuple(
            item
            for item in collection.candidates
            if (item.claim_id, item.result.url) in assessment_map
        )
        grouped = self._group_candidates(assessed_candidates)
        evidence_ids: list[UUID] = []
        relation_count = 0

        for url, candidates in grouped.items():
            first = candidates[0]
            assessment_records = [
                assessment for key, assessment in assessment_map.items() if key[1] == url
            ]
            evidence = EvidenceItem(
                source_id=None,
                title=first.result.title,
                url=url,
                evidence_type=first.result.candidate_type.value,
                published_at=first.result.published_at,
                retrieved_at=_earliest_retrieved(candidates),
                content_hash=_candidate_hash(first.result),
                excerpt=first.result.snippet,
                evidence_metadata={
                    "source_name": first.result.source_name,
                    "language": first.result.language,
                    "lineage_status": "UNASSESSED",
                    "search_provenance": [self._candidate_provenance(item) for item in candidates],
                    "relationship_assessments": [
                        assessment.model_dump(mode="json") for assessment in assessment_records
                    ],
                },
            )
            session.add(evidence)
            session.flush()
            evidence_ids.append(evidence.id)

            for assessment in assessment_records:
                session.add(
                    ClaimEvidence(
                        claim_id=assessment.claim_id,
                        evidence_id=evidence.id,
                        relation=assessment.relation.value,
                        strength_score=assessment.strength_score,
                    )
                )
                relation_count += 1

        job.status = "COMPLETED"
        job.result = {
            "evidence_ids": [str(item) for item in evidence_ids],
            "claim_evidence_count": relation_count,
            "candidate_count": len(collection.candidates),
            "unassessed_candidate_count": len(collection.candidates) - len(assessed_candidates),
            "query_failures": [item.model_dump(mode="json") for item in collection.failures],
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
                "claim_ids": [str(item) for item in task.plan.claim_ids],
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
        if job is None or job.job_type != "RESEARCH" or job.status != "COMPLETED":
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
        return story, EvidenceEngine._load_claims(session, story_id, claim_ids)

    @staticmethod
    def _load_claims(
        session: Session,
        story_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> list[Claim]:
        if session.get(Story, story_id) is None:
            raise ValueError("research story does not exist")
        claims = list(session.scalars(select(Claim).where(Claim.id.in_(claim_ids))))
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
    ) -> ResearchQueryFailure:
        return ResearchQueryFailure(
            claim_id=query.claim_id,
            query_family=query.query_family,
            provider_id=provider_id,
            error_code=error_code,
            error_message=error_message[:1000],
        )

    @staticmethod
    def _group_candidates(
        candidates: tuple[ResearchCandidate, ...],
    ) -> dict[str, list[ResearchCandidate]]:
        grouped: dict[str, list[ResearchCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.result.url, []).append(candidate)
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
        }


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
