"""Replaceable reviewer authentication boundary for the current MVP."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from news_ai_common.config import AppSettings
from news_ai_review import ReviewCapability, ReviewerPrincipal


class ReviewerTokenAuthenticator:
    """Resolve an opaque environment-backed token to a stable human principal."""

    def __init__(self, settings: AppSettings) -> None:
        self._token = settings.review_api_token
        self._reviewer_id = settings.reviewer_id
        requested = {
            item.strip() for item in settings.review_capabilities.split(",") if item.strip()
        }
        try:
            self._capabilities = frozenset(ReviewCapability(item) for item in requested)
        except ValueError:
            self._capabilities = frozenset()
            self._valid_configuration = False
        else:
            self._valid_configuration = bool(requested)

    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> ReviewerPrincipal:
        if self._token is None or self._reviewer_id is None or not self._valid_configuration:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "REVIEW_AUTH_UNAVAILABLE", "message": "Review is unavailable"},
            )
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise self._unauthenticated()
        expected = self._token.get_secret_value().encode("utf-8")
        supplied = credentials.credentials.encode("utf-8")
        if not hmac.compare_digest(supplied, expected):
            raise self._unauthenticated()
        return ReviewerPrincipal(
            reviewer_id=self._reviewer_id,
            capabilities=self._capabilities,
        )

    @staticmethod
    def _unauthenticated() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail={"code": "UNAUTHENTICATED", "message": "Authentication required"},
        )


def require_capability(
    principal: ReviewerPrincipal, *capabilities: ReviewCapability
) -> ReviewerPrincipal:
    if not principal.has_any(*capabilities):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Insufficient review capability"},
        )
    return principal
