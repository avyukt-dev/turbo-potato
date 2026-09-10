# Prompt: Verify the current implementation

Perform an independent implementation audit of `avyukt-dev/turbo-potato` at the exact current `development` head.

This is an **audit-only** task. Do not modify files, create/delete branches, commit, push, open/edit/merge PRs, or change GitHub state. Do not repair findings yet.

Follow `AGENTS.md`. Treat `docs/README.md` as the documentation map, `docs/CANONICAL_CONTRACTS.md` as authoritative for shared contracts, and each owning domain document as authoritative for its area.

## Repository and CI verification

- Confirm the working tree is clean and checked against current remote state.
- Record exact `development` SHA and `main` SHA.
- Use `gh` to verify the latest completed CI for the exact `development` SHA, not merely the branch name.
- Inspect recent merged PRs and migrations to reconstruct what has actually landed.

## Contract audit

Audit the implemented code against canonical documentation, with emphasis on:

1. PostgreSQL durable truth vs Redis Streams transport.
2. Transactional outbox and ACK-after-commit worker behavior.
3. Event envelope validation, correlation/causation, idempotency, retries, and stale-state handling.
4. ORM ↔ Alembic parity and migration upgrade/downgrade behavior.
5. Article discovery/normalization/story clustering boundaries.
6. Editorial taxonomy separation from factual conclusions.
7. AIProvider abstraction, local llama integration, AIRouter capability/locality/sensitivity/fallback behavior, structured-output validation, and AI run/model provenance.
8. Claim extraction: atomicity, provenance, re-extraction semantics, and the invariant that extraction never verifies truth.
9. SearchProvider/research abstraction: AI != search, typed candidates, policy limits, and normalized failures.
10. Evidence engine: search result != evidence; explicit assessment before evidence promotion; contradiction preservation; source/lineage/independence provenance.
11. Fact-check engine: canonical claim statuses and verdict labels; `UNVERIFIED != FALSE`; `UNVERIFIED != REFUTED`; high-risk corroboration based on independent evidence rather than article count.
12. Fact Sheet generation: immutable/versioned factual snapshot, exact claim/fact-check/evidence/source provenance, unresolved questions/context/risk/sensitivity, and `story.verified → content.requested` causality.
13. Security/privacy basics: prompt-injection defenses, no secrets in prompts/logs/config, no policy bypass through model output.
14. Confirm that unimplemented future stages are not accidentally bypassed (quality gate, explicit human approval, social publication safety).

## Execute validation

Run the same gates as CI in a suitable local test environment:

```bash
python -m pip install -e '.[dev]'
ruff check .
ruff format --check apps packages migrations tests
python -m compileall -q apps packages migrations tests
alembic upgrade head
alembic check
pytest
alembic downgrade base
alembic upgrade head
```

If a gate cannot run because the environment lacks PostgreSQL/Redis or another required capability, try to provision the repo-supported local dependency first. If still impossible, report the exact limitation; do not mark that gate passed.

## Required output

Produce an audit matrix with one row per subsystem/contract and these fields:

- status: `PASS`, `PARTIAL`, or `FAIL`;
- canonical requirement;
- implementation evidence (file paths/symbols/tests/migrations);
- validation performed;
- concrete gap/risk if any;
- recommended repair, if needed.

Then give:

- **Overall implementation confidence** (high/medium/low, with reasons).
- **Blocking findings before the next stage**, if any.
- **Non-blocking technical debt** that can safely wait.
- **Verified next stage** and why it is next.
- **Exact GitHub/CI state** used for the audit.

Be adversarial about correctness: passing tests are evidence, not proof. Look for missing negative tests, contract drift, race/idempotency issues, misleading enum mappings, accidental truth inference, migrations that only work one way, and workers that ACK too early. Do not invent problems; distinguish confirmed defects from plausible risks.