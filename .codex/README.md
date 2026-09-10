# Codex project setup

This directory contains repository-scoped Codex configuration, command policy, and reusable prompt templates. `AGENTS.md` at the repository root is the persistent project instruction map.

## First-time setup

1. Open this repository in Codex/CLI from the repository root.
2. Mark the repository as **trusted** when Codex asks. Project `.codex/config.toml` and `.codex/rules/` are intentionally ignored for untrusted projects.
3. Ensure the GitHub CLI is authenticated for this repository:

   ```bash
   gh auth status
   gh repo view avyukt-dev/turbo-potato
   ```

4. Confirm the loaded instructions:

   ```bash
   codex --ask-for-approval never "Summarize the project instructions you loaded. Do not modify files."
   ```

5. Optionally validate the GitHub rules locally:

   ```bash
   codex execpolicy check --pretty --rules .codex/rules/github.rules -- gh pr view 16
   codex execpolicy check --pretty --rules .codex/rules/github.rules -- gh pr merge 16 --squash
   codex execpolicy check --pretty --rules .codex/rules/github.rules -- git push origin feature/example
   codex execpolicy check --pretty --rules .codex/rules/github.rules -- git push --force origin feature/example
   ```

Expected policy: read-only inspection is allowed, GitHub/remote writes prompt, and force pushes are forbidden.

## Prompt sequence for this project

Before the next feature implementation, run these in separate Codex chats/turns:

1. `.codex/prompts/01-understand-project.md` — orient Codex to the architecture and live repository state without changing anything.
2. `.codex/prompts/02-verify-implementation.md` — independently audit the current `development` implementation against canonical docs and run validation without changing anything.
3. Review the two reports. Resolve any material discrepancy before new feature work.
4. For a new stage, copy `.codex/prompts/03-implement-stage.md`, fill in the placeholders, and run it from a clean `development` checkout.
5. Before merge, `.codex/prompts/04-release-gate.md` can be used as an explicit second-pass release check.

## Design principle

Keep `AGENTS.md` short enough to remain useful. Detailed architecture stays in `docs/`, which is the canonical source of truth. Prompt templates are workflows, not architecture and should not override canonical contracts.