"""Thin authenticated HTTP boundary for exact-version human review."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from news_ai_domain import ReviewState, RiskLevel
from news_ai_review import (
    ReviewActionRequest,
    ReviewActionResult,
    ReviewCapability,
    ReviewDetail,
    ReviewerPrincipal,
    ReviewQueuePage,
    ReviewService,
)

from .auth import ReviewerTokenAuthenticator, require_capability

_bearer = HTTPBearer(auto_error=False)


def create_review_router(
    service: ReviewService,
    authenticator: ReviewerTokenAuthenticator,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/review", tags=["review"])

    def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> ReviewerPrincipal:
        return authenticator.authenticate(credentials)

    @router.get("/queue", response_model=ReviewQueuePage)
    def queue(
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        risk_level: RiskLevel | None = None,
        sensitive_topic: str | None = None,
        review_state: ReviewState | None = None,
        platform: str | None = None,
        format: str | None = None,
        language: str | None = None,
    ) -> ReviewQueuePage:
        require_capability(actor, ReviewCapability.VIEW, ReviewCapability.REVIEW)
        return service.queue(
            offset=offset,
            limit=limit,
            risk_level=risk_level,
            sensitive_topic=sensitive_topic,
            review_state=review_state,
            platform=platform,
            format=format,
            language=language,
        )

    @router.get("/{artifact_type}/{artifact_id}", response_model=ReviewDetail)
    def detail(
        artifact_type: str,
        artifact_id: UUID,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
    ) -> ReviewDetail:
        require_capability(actor, ReviewCapability.VIEW, ReviewCapability.REVIEW)
        return service.detail(artifact_type=artifact_type, artifact_id=artifact_id)

    def perform(
        request: Request,
        artifact_type: str,
        artifact_id: UUID,
        action: ReviewActionRequest,
        actor: ReviewerPrincipal,
        idempotency_key: str,
        decision: ReviewState,
    ) -> ReviewActionResult:
        capability = (
            ReviewCapability.APPROVE
            if decision is ReviewState.APPROVED
            else ReviewCapability.REVIEW
        )
        require_capability(actor, capability)
        return service.decide(
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            request=action,
            decision=decision,
            principal=actor,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            correlation_id=request.state.request_id,
        )

    @router.post("/{artifact_type}/{artifact_id}/approve", response_model=ReviewActionResult)
    def approve(
        request: Request,
        artifact_type: str,
        artifact_id: UUID,
        action: ReviewActionRequest,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> ReviewActionResult:
        return perform(
            request,
            artifact_type,
            artifact_id,
            action,
            actor,
            idempotency_key,
            ReviewState.APPROVED,
        )

    @router.post("/{artifact_type}/{artifact_id}/reject", response_model=ReviewActionResult)
    def reject(
        request: Request,
        artifact_type: str,
        artifact_id: UUID,
        action: ReviewActionRequest,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> ReviewActionResult:
        return perform(
            request,
            artifact_type,
            artifact_id,
            action,
            actor,
            idempotency_key,
            ReviewState.REJECTED,
        )

    @router.post(
        "/{artifact_type}/{artifact_id}/request-changes", response_model=ReviewActionResult
    )
    def request_changes(
        request: Request,
        artifact_type: str,
        artifact_id: UUID,
        action: ReviewActionRequest,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> ReviewActionResult:
        return perform(
            request,
            artifact_type,
            artifact_id,
            action,
            actor,
            idempotency_key,
            ReviewState.CHANGES_REQUESTED,
        )

    return router
