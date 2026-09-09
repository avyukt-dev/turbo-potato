# News AI Social Media Manager — Documentation Index

The files in this directory form one architecture specification. They are not independent designs.

## Canonical document map

| Document | Owns |
| --- | --- |
| `CANONICAL_CONTRACTS.md` | Shared enums, lifecycle semantics, invariants, precedence, and cross-document boundaries |
| `ARCHITECTURE.md` | Overall architecture and end-to-end system boundaries |
| `DATA_MODEL.md` | PostgreSQL schema, persistence, provenance, and durable state |
| `EVENTS.md` | Redis Streams contracts, event envelopes, delivery, retries, and consumer behavior |
| `AI_PLATFORM.md` | AI providers, local/cloud routing, prompts, structured output, provenance, and model lifecycle |
| `CONTENT_AND_EDITORIAL.md` | Editorial taxonomy, factual/editorial separation, sensitive-topic rules, evidence-aware content policy |
| `INFRASTRUCTURE_AND_DEPLOYMENT.md` | POCO/runtime deployment, networking, storage, secrets, backups, monitoring, and operations |
| `SOCIAL_PUBLISHING.md` | Social adapters, publication state, scheduling, retries, media handoff, platform constraints |
| `TESTING_AND_EVALUATION.md` | Tests, AI/evidence evaluation, release gates, regression, and rollback criteria |

## Precedence

For concepts shared across multiple documents, `CANONICAL_CONTRACTS.md` is authoritative.

For a concept owned by one domain document, that domain document is authoritative unless a shared contract in `CANONICAL_CONTRACTS.md` explicitly constrains it.

Explanatory examples in other documents must be interpreted using the canonical shared terminology.

## Normalizations adopted on 2026-09-09

The documentation audit established the following rules:

1. **PostgreSQL is the durable source of truth. Redis Streams is event/work transport.**
2. **Claim verification state and fact-check verdict are different enums.**
   - Claim verification: `UNASSESSED`, `SUPPORTED`, `PARTIALLY_SUPPORTED`, `DISPUTED`, `UNVERIFIED`, `REFUTED`.
   - Fact-check verdict: `TRUE`, `MOSTLY_TRUE`, `PARTIALLY_TRUE`, `MISLEADING`, `OUT_OF_CONTEXT`, `UNVERIFIED`, `FALSE`, `FABRICATED`, `SATIRE`.
3. **`UNVERIFIED != FALSE`.**
4. **Risk and sensitivity are separate.** Risk is `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`; sensitive-topic categories are separate policy metadata.
5. **MVP external publication always requires explicit human approval.** Future low-risk auto-approval is an opt-in feature, disabled by default, and can never bypass mandatory human-review categories.
6. **`story.verified` means the verification stage completed, not that every claim is true.**
7. **AI providers and search/research providers are separate abstractions.**
8. **Fact Sheet is the normal factual boundary before content generation.**
9. **Local media persistence and public HTTPS delivery are separate concerns.**
10. **Native/OpenRC-managed services are the default POCO deployment; Docker is optional, not required.**

## Known legacy wording

Some older examples in the original domain documents predate `CANONICAL_CONTRACTS.md`. Until each example is mechanically normalized during implementation, use the canonical definitions above.

Most importantly:

- A `fact_check.completed` example using `PARTIALLY_SUPPORTED` must be read as a legacy enum mismatch. The fact-check field is `label`, and the equivalent fact-check verdict is `PARTIALLY_TRUE` when that verdict is justified.
- A publication rule mentioning future low-risk automation does **not** enable it for the MVP. MVP publishing requires explicit human approval.
- Claim statuses such as `FALSE`, `FABRICATED`, `OUT_OF_CONTEXT`, and `SATIRE` in older claim-status examples belong to fact-check classification, not the canonical claim-verification workflow enum.

## Change rule

When a future design change affects a shared enum, review rule, evidence invariant, publication lifecycle, or ownership boundary:

1. update `CANONICAL_CONTRACTS.md`;
2. update every affected domain document in the same change set;
3. update tests/contracts before implementation relies on the new meaning.

Do not introduce a second vocabulary for an existing concept.
