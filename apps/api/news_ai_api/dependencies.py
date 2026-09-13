"""Production composition for the synchronous human-review boundary."""

from dataclasses import dataclass

from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_database import create_database_engine, create_session_factory
from news_ai_editorial import EditorialConfigLoader
from news_ai_review import (
    ApprovalEligibilityService,
    ReviewConfigurationError,
    ReviewService,
)
from sqlalchemy import Engine

from .auth import ReviewerTokenAuthenticator


@dataclass(frozen=True, slots=True)
class ProductionReviewStack:
    engine: Engine
    service: ReviewService
    eligibility: ApprovalEligibilityService
    authenticator: ReviewerTokenAuthenticator


def build_production_review_stack(settings: AppSettings) -> ProductionReviewStack:
    if not settings.database_url:
        raise ReviewConfigurationError("review database configuration is unavailable")
    if settings.review_api_token is None or settings.reviewer_id is None:
        raise ReviewConfigurationError("review authentication configuration is unavailable")
    policy = EditorialConfigLoader(ConfigLoader(settings.config_dir)).load_publishing_policy()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    return ProductionReviewStack(
        engine=engine,
        service=ReviewService(factory, policy),
        eligibility=ApprovalEligibilityService(factory, policy),
        authenticator=ReviewerTokenAuthenticator(settings),
    )
