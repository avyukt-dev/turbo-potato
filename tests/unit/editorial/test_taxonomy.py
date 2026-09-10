from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from news_ai_common.config import ConfigLoader
from news_ai_editorial import (
    CategoryAssignment,
    EditorialCategory,
    EditorialConfigLoader,
    EditorialConfigSnapshot,
    EditorialPrioritiesConfig,
    EditorialTaxonomyEngine,
    EditorialTaxonomyInput,
    TaxonomyConfig,
)

CANONICAL_CATEGORIES = tuple(EditorialCategory)


def _snapshot() -> EditorialConfigSnapshot:
    return EditorialConfigSnapshot(
        taxonomy=TaxonomyConfig(
            schema_version=1,
            categories=CANONICAL_CATEGORIES,
        ),
        priorities=EditorialPrioritiesConfig(
            schema_version=1,
            topics={
                "india_governance": 1.0,
                "history": 0.95,
                "geopolitics": 0.9,
            },
        ),
    )


def test_loader_reads_taxonomy_and_priorities(tmp_path: Path) -> None:
    editorial = tmp_path / "editorial"
    editorial.mkdir()
    (editorial / "taxonomy.yaml").write_text(
        "schema_version: 1\ncategories:\n"
        + "".join(f"  - {category.value}\n" for category in CANONICAL_CATEGORIES),
        encoding="utf-8",
    )
    (editorial / "priorities.yaml").write_text(
        """
schema_version: 1
topics:
  india_governance: 1.0
  history: 0.95
""".strip(),
        encoding="utf-8",
    )

    snapshot = EditorialConfigLoader(ConfigLoader(tmp_path)).load()

    assert snapshot.taxonomy.categories == CANONICAL_CATEGORIES
    assert snapshot.priorities.topics["india_governance"] == 1.0


def test_taxonomy_requires_every_canonical_category() -> None:
    with pytest.raises(ValidationError, match="missing canonical categories"):
        TaxonomyConfig(
            schema_version=1,
            categories=CANONICAL_CATEGORIES[:-1],
        )


def test_taxonomy_rejects_duplicate_categories() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        TaxonomyConfig(
            schema_version=1,
            categories=(*CANONICAL_CATEGORIES, EditorialCategory.INDIA),
        )


def test_taxonomy_rejects_unknown_category() -> None:
    categories = [category.value for category in CANONICAL_CATEGORIES]
    categories[-1] = "UNKNOWN"

    with pytest.raises(ValidationError):
        TaxonomyConfig(schema_version=1, categories=categories)


def test_priorities_require_bounded_weights_and_normalized_keys() -> None:
    with pytest.raises(ValidationError):
        EditorialPrioritiesConfig(schema_version=1, topics={"history": 1.1})

    with pytest.raises(ValidationError, match="invalid editorial priority topic keys"):
        EditorialPrioritiesConfig(schema_version=1, topics={"History News": 0.9})


def test_assignment_rejects_duplicate_categories() -> None:
    with pytest.raises(ValidationError, match="assignments must be unique"):
        EditorialTaxonomyInput(
            categories=(
                CategoryAssignment(category=EditorialCategory.INDIA, relevance_score=0.9),
                CategoryAssignment(category=EditorialCategory.INDIA, relevance_score=0.8),
            )
        )


def test_engine_rejects_topics_not_owned_by_priority_config() -> None:
    engine = EditorialTaxonomyEngine(_snapshot())

    with pytest.raises(ValueError, match="unknown editorial priority topics"):
        engine.assess(EditorialTaxonomyInput(topic_relevance={"unknown_topic": 0.8}))


def test_engine_orders_categories_and_exposes_transparent_priority_signals() -> None:
    engine = EditorialTaxonomyEngine(_snapshot())

    result = engine.assess(
        EditorialTaxonomyInput(
            categories=(
                CategoryAssignment(category=EditorialCategory.HISTORY, relevance_score=0.8),
                CategoryAssignment(category=EditorialCategory.INDIA, relevance_score=0.9),
                CategoryAssignment(category=EditorialCategory.POLITICS, relevance_score=0.8),
            ),
            topic_relevance={
                "history": 0.9,
                "india_governance": 0.8,
                "geopolitics": 0.4,
            },
        )
    )

    assert [assignment.category for assignment in result.categories] == [
        EditorialCategory.INDIA,
        EditorialCategory.POLITICS,
        EditorialCategory.HISTORY,
    ]
    assert [signal.topic for signal in result.priority_signals] == [
        "history",
        "india_governance",
        "geopolitics",
    ]
    assert result.priority_signals[0].weighted_score == pytest.approx(0.855)
    assert result.priority_signals[0].priority_weight == 0.95
    assert result.taxonomy_schema_version == 1
    assert result.priorities_schema_version == 1


def test_editorial_taxonomy_result_contains_no_factual_verdict_fields() -> None:
    result = EditorialTaxonomyEngine(_snapshot()).assess(
        EditorialTaxonomyInput(
            categories=(
                CategoryAssignment(category=EditorialCategory.FACT_CHECK, relevance_score=1.0),
            ),
            topic_relevance={},
        )
    )

    payload = result.model_dump()
    assert "claim_status" not in payload
    assert "fact_check_label" not in payload
    assert "evidence_strength" not in payload
