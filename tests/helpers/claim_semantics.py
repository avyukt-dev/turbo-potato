"""Explicit current-policy fixture data; production never supplies classifications by default."""

from news_ai_database import AIModel, AIRun

SEMANTICS = {
    "policy_version": "claim-semantics-policy-v1",
    "semantic_type": "QUANTITATIVE",
    "semantic_state": "OBSERVED",
}


def presentation(claim_id, semantics=SEMANTICS):
    return {
        "claim_id": str(claim_id),
        "source_semantic_type": semantics["semantic_type"],
        "source_semantic_state": semantics["semantic_state"],
        "presented_semantic_type": semantics["semantic_type"],
        "presented_semantic_state": semantics["semantic_state"],
    }


def classification_run(session):
    model = AIModel(provider="fixture", model_name="classification-fixture", locality="LOCAL")
    session.add(model)
    session.flush()
    run = AIRun(
        ai_model_id=model.id,
        task_type="CLAIM_EXTRACTION",
        prompt_id="claim-extraction",
        prompt_version="v3",
        prompt_checksum="a" * 64,
        input_hash="b" * 64,
        output_payload={"fixture": "explicit current classification", "value_candidates": []},
        status="SUCCEEDED",
        validation_status="VALIDATED",
        latency_ms=1,
    )
    session.add(run)
    session.flush()
    return run.id
