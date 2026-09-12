"""Thin authenticated scheduling API; no platform execution."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from news_ai_publishing import (
    CancelPublicationRequest,
    CreatePublicationRequest,
    PublicationAttemptView,
    PublicationService,
    PublicationView,
)
from news_ai_review import ReviewCapability, ReviewerPrincipal

from .auth import ReviewerTokenAuthenticator, require_capability


def create_publication_router(
    service: PublicationService, authenticator: ReviewerTokenAuthenticator
):
    router = APIRouter(prefix="/api/v1/publications", tags=["publications"])
    bearer = HTTPBearer(auto_error=False)

    def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> ReviewerPrincipal:
        return authenticator.authenticate(credentials)

    @router.post("", response_model=PublicationView, status_code=201)
    def create(
        body: CreatePublicationRequest,
        request: Request,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
    ):
        require_capability(actor, ReviewCapability.PUBLISH)
        return service.create(
            body,
            actor,
            correlation_id=request.state.request_id,
            request_id=request.state.request_id,
        )

    @router.get("/{publication_id}", response_model=PublicationView)
    def get(publication_id: UUID, actor: Annotated[ReviewerPrincipal, Depends(principal)]):
        require_capability(actor, ReviewCapability.VIEW, ReviewCapability.PUBLISH)
        return service.get(publication_id)

    @router.post("/{publication_id}/cancel", response_model=PublicationView)
    def cancel(
        publication_id: UUID,
        body: CancelPublicationRequest,
        request: Request,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
    ):
        require_capability(actor, ReviewCapability.PUBLISH)
        return service.cancel(
            publication_id, actor, reason=body.reason, request_id=request.state.request_id
        )

    @router.get("/{publication_id}/attempts", response_model=list[PublicationAttemptView])
    def attempts(
        publication_id: UUID,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        require_capability(actor, ReviewCapability.VIEW, ReviewCapability.PUBLISH)
        return service.attempts(publication_id, limit=limit)

    @router.post("/{publication_id}/retry", response_model=PublicationView)
    def retry(
        publication_id: UUID,
        request: Request,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$")
        ],
    ):
        require_capability(actor, ReviewCapability.PUBLISH)
        return service.retry(
            publication_id,
            actor,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )

    @router.post("/{publication_id}/publish-now", response_model=PublicationView)
    def publish_now(
        publication_id: UUID,
        request: Request,
        actor: Annotated[ReviewerPrincipal, Depends(principal)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$")
        ],
    ):
        require_capability(actor, ReviewCapability.PUBLISH)
        return service.publish_now(
            publication_id,
            actor,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )

    return router
