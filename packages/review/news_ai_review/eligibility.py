"""Read-only exact-version approval eligibility checks for future publishing."""

from __future__ import annotations

from uuid import UUID

from news_ai_database import ContentVariant
from sqlalchemy.orm import Session, sessionmaker


class ApprovalEligibilityService:
    def __init__(self, session_factory: sessionmaker[Session], publishing_policy: object) -> None:
        self.session_factory = session_factory
        self.publishing_policy = publishing_policy

    def is_exact_version_approved(self, content_variant_id: UUID) -> bool:
        from .service import ReviewService

        with self.session_factory() as session:
            variant = session.get(ContentVariant, content_variant_id)
            if variant is None:
                return False
            service = ReviewService(self.session_factory, self.publishing_policy)
            return service.is_currently_eligible(session, variant)
