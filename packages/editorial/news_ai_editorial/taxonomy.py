"""Canonical editorial taxonomy and priority-signal contracts.

This module validates editorial relevance only. It deliberately does not contain evidence,
claim-verification, fact-check, or publication-approval semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from news_ai_common.config import ConfigDomain, ConfigLoader
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
            raise ValueError(f"editorial taxonomy is missing canonical categories: {', '.join(missing)}")
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


@dataclass(frozen=True, slots=True)
class EditorialConfigSnapshot:
    taxonomy: TaxonomyConfig
    priorities: EditorialPrioritiesConfig


class EditorialConfigLoader:
    """Load only configuration owned by the editorial domain."""

    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

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
