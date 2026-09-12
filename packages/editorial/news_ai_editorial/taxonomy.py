"""Canonical editorial taxonomy and priority-signal contracts.

This module validates editorial relevance and editorial-owned risk routing. It deliberately does
not contain evidence, claim-verification, fact-check, or publication-approval semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_domain import RiskLevel
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RelevanceScore = Annotated[float, Field(ge=0.0, le=1.0)]
_TOPIC_KEY = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class EditorialCategory(StrEnum):
    """Canonical primary editorial taxonomy from CONTENT_AND_EDITORIAL.md."""

    INDIA = "INDIA"
    GEOPOLITICS = "GEOPOLITICS"
    SECURITY = "SECURITY"
    POLITICS = "POLITICS"
    CIVILIZATION = "CIVILIZATION"
    RELIGION = "RELIGION"
    HISTORY = "HISTORY"
    LAW = "LAW"
    RIGHTS = "RIGHTS"
    DEMOGRAPHICS = "DEMOGRAPHICS"
    ECONOMY = "ECONOMY"
    SCIENCE_TECH = "SCIENCE_TECH"
    FACT_CHECK = "FACT_CHECK"


class MandatoryReviewCategory(StrEnum):
    """Canonical sensitive-topic identifiers requiring human review."""

    COMMUNAL_VIOLENCE = "COMMUNAL_VIOLENCE"
    RELIGIOUS_ACCUSATION = "RELIGIOUS_ACCUSATION"
    RELIGIOUS_VIOLENCE = "RELIGIOUS_VIOLENCE"
    CRIMINAL_ALLEGATION = "CRIMINAL_ALLEGATION"
    SEXUAL_ASSAULT = "SEXUAL_ASSAULT"
    TERRORISM = "TERRORISM"
    WAR_CASUALTIES = "WAR_CASUALTIES"
    ELECTION_FRAUD = "ELECTION_FRAUD"
    SC_ST_ALLEGATION = "SC_ST_ALLEGATION"
    CASTE_RELATED_ACCUSATION = "CASTE_RELATED_ACCUSATION"
    BLASPHEMY_ALLEGATION = "BLASPHEMY_ALLEGATION"
    UNVERIFIED_BREAKING_NEWS = "UNVERIFIED_BREAKING_NEWS"


class EditorialRiskPolicy(BaseModel):
    """Closed editorial risk policy with canonical mandatory-review coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    risk_levels: tuple[RiskLevel, ...]
    mandatory_review_categories: tuple[MandatoryReviewCategory, ...]

    @model_validator(mode="after")
    def require_canonical_policy(self) -> EditorialRiskPolicy:
        if set(self.risk_levels) != set(RiskLevel) or len(self.risk_levels) != len(RiskLevel):
            raise ValueError("risk policy must contain each canonical risk level exactly once")
        expected = set(MandatoryReviewCategory)
        configured = set(self.mandatory_review_categories)
        if configured != expected or len(self.mandatory_review_categories) != len(expected):
            missing = sorted(item.value for item in expected - configured)
            extra = sorted(item.value for item in configured - expected)
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected: {', '.join(extra)}")
            raise ValueError(
                "risk policy must contain each canonical mandatory-review category exactly once"
                + (f" ({'; '.join(details)})" if details else "")
            )
        return self

    def requires_review(self, sensitive_topics: set[str] | frozenset[str]) -> bool:
        mandatory = {category.value for category in self.mandatory_review_categories}
        return bool(mandatory.intersection(sensitive_topics))


class TaxonomyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    categories: tuple[EditorialCategory, ...]

    @field_validator("categories")
    @classmethod
    def require_unique_categories(
        cls,
        value: tuple[EditorialCategory, ...],
    ) -> tuple[EditorialCategory, ...]:
        if len(value) != len(set(value)):
            raise ValueError("editorial taxonomy categories must be unique")
        return value

    @model_validator(mode="after")
    def require_canonical_taxonomy(self) -> TaxonomyConfig:
        configured = set(self.categories)
        expected = set(EditorialCategory)
        if configured != expected:
            missing = sorted(category.value for category in expected - configured)
            message = f"editorial taxonomy is missing canonical categories: {', '.join(missing)}"
            raise ValueError(message)
        return self


class EditorialPrioritiesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    topics: dict[str, RelevanceScore]

    @field_validator("topics")
    @classmethod
    def validate_topic_keys(cls, value: dict[str, RelevanceScore]) -> dict[str, RelevanceScore]:
        if not value:
            raise ValueError("editorial priorities must define at least one topic")
        invalid = sorted(key for key in value if _TOPIC_KEY.fullmatch(key) is None)
        if invalid:
            raise ValueError(f"invalid editorial priority topic keys: {', '.join(invalid)}")
        return value


class ContentStyleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Literal["INSTAGRAM"]
    format: Literal["CAROUSEL"]


class ContentStyleDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_aware: bool
    explicit_about_uncertainty: bool
    avoid_unsupported_motive_attribution: bool
    avoid_sensational_overstatement: bool

    @model_validator(mode="after")
    def require_safety_defaults(self) -> ContentStyleDefaults:
        if not all(self.model_dump().values()):
            raise ValueError("canonical content safety defaults cannot be disabled")
        return self


class ContentStyleConfig(BaseModel):
    """Editorial-owned presentation policy; factual methodology does not belong here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=1)
    methodology_version: str = Field(min_length=1, max_length=64)
    default_target: ContentStyleTarget
    default_tone: str = Field(min_length=1, max_length=128)
    default_generation_language: str = Field(min_length=2, max_length=32)
    defaults: ContentStyleDefaults

    @field_validator("default_generation_language")
    @classmethod
    def normalize_generation_language(cls, value: str) -> str:
        normalized = value.strip().replace("_", "-").lower()
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", normalized) is None:
            raise ValueError("default generation language must be a simple BCP-47 style tag")
        return normalized


class MVPPublishingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    external_publication_requires_human_approval: Literal[True]
    low_risk_auto_approval_enabled: Literal[False]


class FuturePublishingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    low_risk_auto_approval_default_enabled: Literal[False]
    mandatory_review_categories_may_bypass_human_review: Literal[False]


class PublishingPolicyConfig(BaseModel):
    """Editorial-owned publication eligibility policy; Stage 21 only reads its MVP gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    mvp: MVPPublishingPolicy
    future: FuturePublishingPolicy


@dataclass(frozen=True, slots=True)
class EditorialConfigSnapshot:
    taxonomy: TaxonomyConfig
    priorities: EditorialPrioritiesConfig
    risk_policy: EditorialRiskPolicy
    content_style: ContentStyleConfig
    publishing_policy: PublishingPolicyConfig


class EditorialConfigLoader:
    """Load only configuration owned by the editorial domain."""

    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load_risk_policy(self) -> EditorialRiskPolicy:
        return self.loader.load_domain_file(
            ConfigDomain.EDITORIAL,
            "risk-policy.yaml",
            EditorialRiskPolicy,
        )

    def load_content_style(self) -> ContentStyleConfig:
        return self.loader.load_domain_file(
            ConfigDomain.EDITORIAL,
            "content-style.yaml",
            ContentStyleConfig,
        )

    def load_publishing_policy(self) -> PublishingPolicyConfig:
        return self.loader.load_domain_file(
            ConfigDomain.EDITORIAL,
            "publishing-policy.yaml",
            PublishingPolicyConfig,
        )

    def load(self) -> EditorialConfigSnapshot:
        return EditorialConfigSnapshot(
            taxonomy=self.loader.load_domain_file(
                ConfigDomain.EDITORIAL,
                "taxonomy.yaml",
                TaxonomyConfig,
            ),
            priorities=self.loader.load_domain_file(
                ConfigDomain.EDITORIAL,
                "priorities.yaml",
                EditorialPrioritiesConfig,
            ),
            risk_policy=self.load_risk_policy(),
            content_style=self.load_content_style(),
            publishing_policy=self.load_publishing_policy(),
        )


class CategoryAssignment(BaseModel):
    """One editorial category relevance assertion from an upstream classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: EditorialCategory
    relevance_score: RelevanceScore


class EditorialTaxonomyInput(BaseModel):
    """Untrusted classifier output entering the editorial taxonomy boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[CategoryAssignment, ...] = ()
    topic_relevance: dict[str, RelevanceScore] = Field(default_factory=dict)

    @field_validator("categories")
    @classmethod
    def unique_assignments(
        cls,
        value: tuple[CategoryAssignment, ...],
    ) -> tuple[CategoryAssignment, ...]:
        categories = [assignment.category for assignment in value]
        if len(categories) != len(set(categories)):
            raise ValueError("editorial category assignments must be unique")
        return value

    @field_validator("topic_relevance")
    @classmethod
    def validate_topic_keys(cls, value: dict[str, RelevanceScore]) -> dict[str, RelevanceScore]:
        invalid = sorted(key for key in value if _TOPIC_KEY.fullmatch(key) is None)
        if invalid:
            raise ValueError(f"invalid editorial topic relevance keys: {', '.join(invalid)}")
        return value


class TopicPrioritySignal(BaseModel):
    """Transparent combination of classifier relevance and configured editorial preference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str
    relevance_score: RelevanceScore
    priority_weight: RelevanceScore
    weighted_score: RelevanceScore


class EditorialTaxonomyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy_schema_version: int
    priorities_schema_version: int
    categories: tuple[CategoryAssignment, ...]
    priority_signals: tuple[TopicPrioritySignal, ...]


class EditorialTaxonomyEngine:
    """Validate and normalize taxonomy output without making factual judgments."""

    def __init__(self, snapshot: EditorialConfigSnapshot) -> None:
        self.snapshot = snapshot
        self._category_order = {
            category: index for index, category in enumerate(snapshot.taxonomy.categories)
        }

    def assess(self, raw: EditorialTaxonomyInput) -> EditorialTaxonomyResult:
        configured_categories = set(self.snapshot.taxonomy.categories)
        disabled = sorted(
            assignment.category.value
            for assignment in raw.categories
            if assignment.category not in configured_categories
        )
        if disabled:
            raise ValueError(f"editorial categories are not configured: {', '.join(disabled)}")

        unknown_topics = sorted(set(raw.topic_relevance) - set(self.snapshot.priorities.topics))
        if unknown_topics:
            raise ValueError(f"unknown editorial priority topics: {', '.join(unknown_topics)}")

        categories = tuple(
            sorted(
                raw.categories,
                key=lambda assignment: (
                    -assignment.relevance_score,
                    self._category_order[assignment.category],
                ),
            )
        )
        priority_signals = tuple(
            sorted(
                (
                    TopicPrioritySignal(
                        topic=topic,
                        relevance_score=relevance,
                        priority_weight=self.snapshot.priorities.topics[topic],
                        weighted_score=relevance * self.snapshot.priorities.topics[topic],
                    )
                    for topic, relevance in raw.topic_relevance.items()
                ),
                key=lambda signal: (-signal.weighted_score, signal.topic),
            )
        )
        return EditorialTaxonomyResult(
            taxonomy_schema_version=self.snapshot.taxonomy.schema_version,
            priorities_schema_version=self.snapshot.priorities.schema_version,
            categories=categories,
            priority_signals=priority_signals,
        )
