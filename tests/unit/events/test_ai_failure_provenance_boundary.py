from __future__ import annotations

import json
from typing import Any

from news_ai_events.reliability import _extract_ai_failure_provenance


class _DiagnosticError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("AI routing execution failed")
        self.failure_provenance_payload = payload


def _payload() -> dict[str, Any]:
    return {
        "task_type": "CLAIM_EXTRACTION",
        "prompt_id": "claim-extraction",
        "prompt_version": "v1",
        "prompt_checksum": "sha256:prompt",
        "input_artifact_ids": ["article:1"],
        "input_hash": "sha256:input",
        "correlation_id": "9a60c05c-d70b-4c76-a389-5a8e6c8b7f2e",
        "attempts": [
            {
                "provider_id": "groq",
                "model": "openai/gpt-oss-120b",
                "reasoning_effort": "medium",
                "reasoning_policy_version": "reasoning-routing-policy-v1",
                "reasoning_reasons": [],
                "outcome": "FAILED",
                "failure_reason": "UNAVAILABLE",
                "raw_response": "provider-body-must-not-persist",
                "request_metadata": {"authorization": "Bearer nested-secret"},
            }
        ],
        "final_failure_reason": "UNAVAILABLE",
        "fallback_decision": "EXHAUSTED",
        "system_prompt": "raw-system-prompt-must-not-persist",
        "input": {"raw": "raw-input-must-not-persist"},
        "request_metadata": {"api_key": "top-level-secret"},
    }


def test_ai_failure_provenance_boundary_strips_unknown_nested_fields() -> None:
    result = _extract_ai_failure_provenance(_DiagnosticError(_payload()))

    assert result is not None
    assert set(result) == {
        "task_type",
        "prompt_id",
        "prompt_version",
        "prompt_checksum",
        "input_artifact_ids",
        "input_hash",
        "correlation_id",
        "attempts",
        "final_failure_reason",
        "fallback_decision",
    }
    assert set(result["attempts"][0]) == {
        "provider_id",
        "model",
        "reasoning_effort",
        "reasoning_policy_version",
        "reasoning_reasons",
        "outcome",
        "failure_reason",
    }
    serialized = json.dumps(result)
    assert "provider-body-must-not-persist" not in serialized
    assert "raw-system-prompt-must-not-persist" not in serialized
    assert "raw-input-must-not-persist" not in serialized
    assert "nested-secret" not in serialized
    assert "top-level-secret" not in serialized


def test_ai_failure_provenance_boundary_rejects_oversized_payload() -> None:
    payload = _payload()
    payload["input_artifact_ids"] = ["x" * 70_000]

    assert _extract_ai_failure_provenance(_DiagnosticError(payload)) is None


def test_ai_failure_provenance_boundary_rejects_malformed_attempts() -> None:
    payload = _payload()
    payload["attempts"] = [{"provider_id": "groq"}]

    assert _extract_ai_failure_provenance(_DiagnosticError(payload)) is None
