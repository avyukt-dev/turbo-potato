# News AI Social Media Manager — Codex Instructions

## Purpose

Work on this repository as a careful implementation agent for an evidence-first AI newsroom and social publishing system. Prefer concrete, tested repository changes over speculative redesign.

## Source of truth

Read `docs/README.md` first. The files under `docs/` form one architecture specification, not independent designs.

Precedence:
1. Direct user task instructions.
2. `docs/CANONICAL_CONTRACTS.md` for shared contracts, enums, invariants, lifecycle semantics, and precedence.
3. The domain document that owns the concept, as mapped by `docs/README.md`.
4. Existing code/tests/configuration when they do not conflict with canonical docs.

Do not invent a parallel architecture when a canonical contract already exists. If code and canonical docs conflict materially, stop implementation of the conflicting part, report the exact conflict, and propose the smallest resolution. Do not silently rewrite canonical docs.

## Critical product invariants

Preserve these unless the user explicitly changes the canonical architecture:

- PostgreSQL is durable source of truth; Redis Streams is event/work transport.
- Worker pattern: read event → validate → idempotency check → load PostgreSQL state → validate transition → process → persist durable result → emit next event/outbox → ACK.
- Use transactional outbox when durable state and event intent must be atomic.
- Claim states are `UNASSESSED`, `SUPPORTED`, `PARTIALLY_SUPPORTED`, `DISPUTED`, `UNVERIFIED`, `REFUTED`.
- Fact-check labels are `TRUE`, `MOSTLY_TRUE`, `PARTIALLY_TRUE`, `MISLEADING`, `OUT_OF_CONTEXT`, `UNVERIFIED`, `FALSE`, `FABRICATED`, `SATIRE`.
- `UNVERIFIED != FALSE` and `UNVERIFIED != REFUTED`.
- Search results are candidates, not automatically evidence.
- Query intent does not determine whether a result supports or contradicts a claim.
- Source count is not the same as independent corroboration.
- AI providers and search providers are separate abstractions.
- Raw AI output is untrusted; validate it structurally and semantically.
- Fact Sheet is the normal factual boundary before editorial/content generation.
- Editorial policy may change attention, framing, tone, format, and context, but never factual status.
- MVP external publication requires explicit human approval.
- `story.verified` means verification processing completed; it does not mean every claim is true.
- No fabricated evidence, fake quotations, deliberate misinformation, group stereotyping, collective guilt, or caste/religion-based behavioral inference.

## Repository map

- `apps/` — runnable services/workers.
- `packages/` — reusable domain, database, event, AI, evidence, and platform packages.
- `migrations/` — Alembic schema history.
- `config/` — canonical runtime/source/research/editorial/model/prompt/platform configuration.
- `docs/` — canonical architecture and operational contracts.
- `tests/` — unit/integration coverage.
- `.github/workflows/ci.yml` — authoritative CI gate.

Do not hard-code project progress in this file. Determine current implementation state from `development`, merged PRs, migrations, tests, and the current user task.

## Git and GitHub workflow

Use local `git` for working-tree operations and `gh` for GitHub state, PRs, Actions, and remote metadata.

For implementation work:

1. Start from a clean worktree. Inspect `git status --short`, current branch, and `git rev-parse HEAD`.
2. Verify GitHub access with `gh auth status` and repository identity with `gh repo view`.
3. Fetch remote state. Confirm `development` head and its exact latest CI are green before branching.
4. Create a `feature/<slug>` branch from the exact green `development` head. Never develop directly on `main` or `development`.
5. Read the canonical docs and neighboring implementation/tests relevant to the task before editing.
6. Implement the smallest complete feature. Preserve existing contracts unless the task explicitly changes them.
7. Run all required validation locally before committing.
8. Review `git diff` and `git status`. Commit the finished change with a clear conventional message. Do not amend old commits to hide history.
9. Push only the feature branch. Never force-push `main` or `development`.
10. Use `gh` to inspect the exact feature-head CI. If CI fails, inspect logs, fix only on the feature branch, rerun local gates, commit/push, and repeat until the exact head is green.
11. Create a PR targeting `development`. Confirm base/head SHAs, diff, mergeability, and green CI.
12. Squash-merge only the exact green head.
13. Resolve the exact new `development` merge SHA and wait for CI on that SHA. A green feature branch alone is not completion.
14. Do not delete merged feature branches unless the user explicitly asks. Never delete `main` or `development`.

For analysis/review-only tasks, do not create branches, commits, PRs, or file changes unless the prompt explicitly authorizes fixes.

## Required local gates

Mirror `.github/workflows/ci.yml` after code changes:

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

Use PostgreSQL and Redis when the tests or migration checks require them. Do not replace a failing gate with a weaker substitute.

If only documentation/agent configuration changes and the full application gates are available, still run them. If an environment limitation prevents a gate, state exactly which gate could not run and why; do not call the feature green.

## Implementation rules

- Prefer existing abstractions and naming patterns over new frameworks.
- Keep domain/business logic independent of host-specific service commands.
- Keep durable IDs, correlation/causation IDs, idempotency keys, and AI/evidence provenance intact.
- Perform external/network AI/search work outside long PostgreSQL transactions; revalidate durable state before final persistence where staleness matters.
- Do not expose secrets in prompts, logs, fixtures, committed config, or error messages.
- Do not weaken validation merely to make tests pass.
- Do not treat SQLite behavior as authoritative when production behavior depends on PostgreSQL semantics; test portability boundaries explicitly.
- Schema changes require an Alembic migration plus ORM parity and migration drift/round-trip validation.
- Event changes require contract, producer, consumer, idempotency, causation/correlation, and outbox tests.
- AI changes require typed outputs, structural validation, semantic validation, prompt/version provenance, and routing/fallback behavior where applicable.
- External social APIs must be checked against current official provider documentation before implementation.

## Documentation discipline

Do not modify canonical docs merely to match an implementation shortcut. Change docs only when the implementation plan or canonical contract genuinely changes, and call that out explicitly before doing so when the task allows interaction.

Implementation details that fit the existing design should normally be expressed in code, migrations, configuration, tests, or focused developer tooling rather than new architecture documents.

## Completion report

At the end of an implementation task, report:

- branch and exact final feature SHA;
- files/behavior changed at a high level;
- local validation run and results;
- feature CI run/result for the exact head;
- PR number and merge method if merged;
- exact post-merge `development` SHA and CI result;
- any unresolved risks, skipped gates, or follow-up work;
- whether the feature branch is safe to delete (but do not delete unless asked).

Never claim a change, test pass, merge, or deletion that you did not actually verify.