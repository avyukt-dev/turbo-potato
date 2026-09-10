# Prompt: Release gate for a completed feature branch

Audit the current feature branch before it is allowed into `development`, and if defects are found, fix them on this feature branch and rerun the complete gate.

Follow `AGENTS.md`. Never modify `main` directly. Do not weaken tests or canonical contracts to make the branch pass.

## Verify provenance and branch state

- Confirm the feature branch name and exact HEAD SHA.
- Confirm its merge base is the expected current `development` lineage and report if it is behind.
- Use `gh` to inspect any existing PR and CI for this exact head.
- Review the full `development...HEAD` diff, not only the latest commit.

## Review checklist

Check for:

- canonical-doc contract violations;
- factuality/editorial boundary violations;
- invalid `UNVERIFIED`/`FALSE` or verification-state mappings;
- lost evidence/AI/source provenance;
- outbox/event causation, correlation, idempotency, ACK/retry errors;
- race conditions or stale-state persistence hazards;
- ORM/migration drift or unsafe downgrade paths;
- secrets, unsafe logs, prompt-injection exposure, or raw model output trust;
- unbounded external calls inside database transactions;
- accidental implementation of a future stage/bypass of quality or human approval;
- missing negative/replay/failure tests;
- formatter/lint/compiler/test failures.

Run all local gates from `AGENTS.md`.

If you find a confirmed defect, fix it on the feature branch, add regression coverage, rerun all gates, commit, push, and verify exact-head CI. Repeat until clean.

Only when the exact feature head is green and the review finds no blocking issue:

1. create/update the PR to target `development`;
2. confirm the PR head still equals the reviewed SHA and the base is expected;
3. confirm PR CI and mergeability;
4. squash-merge that exact head;
5. resolve the exact post-merge `development` SHA;
6. verify CI on that exact SHA is fully green.

Do not delete the feature branch unless explicitly asked. Report every defect fixed, all validation results, PR/merge SHA, and any remaining non-blocking risk.