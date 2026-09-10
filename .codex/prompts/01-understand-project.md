# Prompt: Understand the project

You are orienting yourself to `avyukt-dev/turbo-potato`, the News AI Social Media Manager.

This is an **analysis-only** task. Do not modify files, create/delete branches, commit, push, open/edit/merge PRs, or change GitHub state.

Follow the repository `AGENTS.md` and do the work rather than only describing how you would do it.

## Required investigation

1. Confirm the repository/worktree state with local Git and confirm GitHub authentication/repository identity with `gh`.
2. Fetch remote refs and identify the exact current `development` and `main` SHAs.
3. Use `gh` to identify the latest CI run for the exact `development` SHA and report whether every gate passed.
4. Read `docs/README.md` and then the canonical documents it maps. At minimum read enough of:
   - `docs/CANONICAL_CONTRACTS.md`
   - `docs/ARCHITECTURE.md`
   - `docs/DATA_MODEL.md`
   - `docs/EVENTS.md`
   - `docs/AI_PLATFORM.md`
   - `docs/SOURCE_AND_RESEARCH.md`
   - `docs/CONTENT_AND_EDITORIAL.md`
   - `docs/CONTENT_SCHEMAS.md`
   - `docs/TESTING_AND_EVALUATION.md`
   - `docs/SOCIAL_PUBLISHING.md`
   - `docs/API_SPEC.md`
   - `docs/INFRASTRUCTURE_AND_DEPLOYMENT.md`
   - `docs/OPERATIONS_RUNBOOK.md`
5. Inspect the repository tree, migrations, configuration roots, packages, apps/workers, tests, and recent merged PR/history on `development`.
6. Trace the implemented pipeline end-to-end from collection through the latest implemented stage. Identify actual producers/consumers, durable tables, events/outbox boundaries, AI/search abstractions, and safety gates from code—not from assumptions.
7. Determine the next unimplemented canonical stage from the architecture + repository evidence. Do not rely on a stale progress statement.

## Required report

Return one structured report with:

- **Repository state:** branches, exact `main`/`development` SHAs, latest exact-SHA CI status, dirty/clean worktree.
- **Architecture map:** services/apps, core packages, PostgreSQL/Redis responsibilities, AI/search boundaries, configuration ownership.
- **Implemented pipeline:** each implemented stage in order, with representative code/tests/migrations/events that prove it exists.
- **Factuality/editorial invariants:** especially claim states vs fact-check labels, `UNVERIFIED` semantics, evidence promotion, independence/corroboration, Fact Sheet boundary, and human-approval policy.
- **Current data/event contracts:** important durable entities and the event chain through the latest stage.
- **Testing/release workflow:** exact local/CI gates and GitHub flow.
- **Next stage:** name, intended scope, expected code areas, and explicit out-of-scope boundaries.
- **Potential inconsistencies or risks:** anything where docs, code, migrations, tests, events, or config appear inconsistent. Label each as confirmed, likely, or uncertain and cite file paths/line references or commands used.

Do not fix anything in this task. If you find a serious discrepancy, make it prominent so it can be verified in the next audit prompt.