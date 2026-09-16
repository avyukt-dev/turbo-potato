from __future__ import annotations

import conftest


def test_ci_skip_policy_accepts_exact_live_groq_skip() -> None:
    unexpected, missing = conftest._ci_skip_policy_violations(set(conftest._ALLOWED_CI_SKIPS))
    assert unexpected == frozenset()
    assert missing == frozenset()


def test_ci_skip_policy_rejects_additional_skip() -> None:
    observed = set(conftest._ALLOWED_CI_SKIPS)
    observed.add("tests/unit/test_example.py::test_unexpected")
    unexpected, missing = conftest._ci_skip_policy_violations(observed)
    assert unexpected == frozenset({"tests/unit/test_example.py::test_unexpected"})
    assert missing == frozenset()


def test_ci_skip_policy_requires_live_groq_skip_in_ci() -> None:
    unexpected, missing = conftest._ci_skip_policy_violations(set())
    assert unexpected == frozenset()
    assert missing == conftest._ALLOWED_CI_SKIPS
