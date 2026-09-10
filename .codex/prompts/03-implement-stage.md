# Prompt template: Implement a canonical stage

Implement **Stage <NUMBER> — <NAME>** in `avyukt-dev/turbo-potato`.

Use branch: `feature/<SLUG>`.

Follow `AGENTS.md` and all canonical docs. Treat this prompt as authorization to perform the full feature workflow through merge into `development` only after all required gates are green.

## Before coding

- Start from a clean checkout.
- Fetch remote state and resolve the exact current `development` SHA.
- Use `gh` to verify CI is green on that exact SHA.
- Create `feature/<SLUG>` from that exact SHA.
- Read `docs/README.md`, `docs/CANONICAL_CONTRACTS.md`, and the owning domain documents for this stage.
- Inspect neighboring implementation/tests/migrations/events/config first.
- State any material canonical conflict before changing docs. Do not change docs just to accommodate an implementation shortcut.

## Scope

Implement:

<INSERT REQUIRED BEHAVIOR / CONTRACTS>

Explicitly out of scope:

<INSERT NEXT-STAGE OR PROVIDER-SPECIFIC WORK THAT MUST NOT LEAK INTO THIS FEATURE>

## Engineering requirements

- Reuse existing abstractions and contracts.
- Preserve factuality/editorial separation and all canonical enum semantics.
- Preserve provenance, causation/correlation, idempotency, and transactional-outbox rules.
- Add/modify migrations for schema changes and keep ORM parity.
- Add focused positive, negative, idempotency/replay, failure, and stale-state tests as applicable.
- For AI output, require typed structural + semantic validation and durable provenance.
- Keep external/network work outside long PostgreSQL transactions.
- Do not authorize external publication unless this stage explicitly owns that action and all prior gates are satisfied.

## Validation and delivery

Run every local gate from `AGENTS.md`. Fix all failures before committing.

Then:
1. commit and push the feature branch;
2. inspect CI for the exact feature head with `gh`;
3. fix and repeat until exact-head CI is green;
4. create a PR to `development`;
5. re-check base/head SHA, diff, mergeability, and PR CI;
6. squash-merge the exact green head;
7. resolve the exact new `development` SHA;
8. wait for and verify CI on that exact merge SHA;
9. do not delete the feature branch unless explicitly instructed.

Finish with the completion report required by `AGENTS.md`. Do not call the stage complete before the exact post-merge `development` CI is green.