"""Read-only exact-version approval eligibility checks for future publishing."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from news_ai_database import ContentVariant
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class ApprovedPublicationReference:
    variant: ContentVariant
    content_draft_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    review_decision_id: UUID
    quality_check_id: UUID


class ApprovalEligibilityService:
    def __init__(self, session_factory: sessionmaker[Session], publishing_policy: object) -> None:
        self.session_factory = session_factory
        self.publishing_policy = publishing_policy

    def load_approved_reference(
        self, session: Session, content_variant_id: UUID
    ) -> ApprovedPublicationReference | None:
        """Reuse the exact review graph under Story → Draft → Variant locks.

        Callers own the transaction. No second session or weaker publication-specific
        approval calculation is allowed at the scheduling boundary.
        """
        from .errors import ReviewError
        from .service import ReviewService

        service = ReviewService(self.session_factory, self.publishing_policy)
        try:
            graph = service._load_graph(session, content_variant_id, lock=True)
        except ReviewError:
            return None
        if not service.is_currently_eligible(session, graph.variant):
            return None
        return ApprovedPublicationReference(
            graph.variant,
            graph.draft.id,
            graph.fact_sheet.id,
            graph.fact_sheet.version,
            graph.existing_decision.id,
            graph.quality_check.id,
        )

    def is_exact_version_approved(self, content_variant_id: UUID) -> bool:
        from .service import ReviewService

        with self.session_factory() as session:
            variant = session.get(ContentVariant, content_variant_id)
            if variant is None:
                return False
            service = ReviewService(self.session_factory, self.publishing_policy)
            return service.is_currently_eligible(session, variant)
