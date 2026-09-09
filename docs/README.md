# News AI Social Media Manager — Documentation Index

The files in this directory form one architecture specification. They are not independent designs.

## Canonical document map

| Document | Owns |
| --- | --- |
| `CANONICAL_CONTRACTS.md` | Shared enums, lifecycle semantics, invariants, precedence, configuration ownership, and runtime portability contracts |
| `ARCHITECTURE.md` | Overall architecture and end-to-end system boundaries |
| `DATA_MODEL.md` | PostgreSQL schema, persistence, provenance, and durable state |
| `EVENTS.md` | Redis Streams contracts, event envelopes, delivery, retries, and consumer behavior |
| `AI_PLATFORM.md` | AI providers, local/cloud routing, prompts, structured output, provenance, and model lifecycle |
| `SOURCE_AND_RESEARCH.md` | Source roles, research planning, evidence acquisition, source independence, contradiction handling, fact-check/historical research methodology |
| `CONTENT_AND_EDITORIAL.md` | Editorial taxonomy, prioritization, factual/editorial separation, sensitive-topic rules, content policy, and review policy |
| `CONTENT_SCHEMAS.md` | Application-layer Pydantic/JSON contracts from claims/evidence through Fact Sheet, content, quality, review, media, and publication requests |
| `INFRASTRUCTURE_AND_DEPLOYMENT.md` | Runtime portability, capability detection, service-manager adapters, networking, storage, secrets, backups, and infrastructure design |
| `SOCIAL_PUBLISHING.md` | Social adapters, publication state, scheduling, retries, media handoff, and platform constraints |
| `API_SPEC.md` | FastAPI HTTP boundary, endpoint semantics, authorization expectations, async orchestration, and API state validation |
| `TESTING_AND_EVALUATION.md` | Tests, AI/evidence evaluation, runtime portability, release gates, regression, and rollback criteria |
| `OPERATIONS_RUNBOOK.md` | Day-to-day operations, generic runtime control, health checks, deployment execution, incidents, recovery, and publication safety |

## Precedence

For concepts shared across multiple documents, `CANONICAL_CONTRACTS.md` is authoritative.

For a concept owned by one domain document, that domain document is authoritative unless a shared contract explicitly constrains it.

Current documentation should use current canonical terminology directly; outdated illustrative examples are not intentionally retained.

## Normalizations adopted on 2026-09-09

1. **PostgreSQL is the durable source of truth. Redis Streams is event/work transport.**
2. **Claim verification state and fact-check verdict are different enums.**
   - Claim verification: `UNASSESSED`, `SUPPORTED`, `PARTIALLY_SUPPORTED`, `DISPUTED`, `UNVERIFIED`, `REFUTED`.
   - Fact-check verdict: `TRUE`, `MOSTLY_TRUE`, `PARTIALLY_TRUE`, `MISLEADING`, `OUT_OF_CONTEXT`, `UNVERIFIED`, `FALSE`, `FABRICATED`, `SATIRE`.
3. **`UNVERIFIED != FALSE` and `UNVERIFIED != REFUTED`.**
4. **Risk and sensitivity are separate.** Risk is `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`; sensitive-topic categories are separate policy metadata.
5. **MVP external publication always requires explicit human approval.** Future low-risk auto-approval is opt-in, disabled by default, and can never bypass mandatory human-review categories.
6. **`story.verified` means the verification stage completed, not that every claim is true.**
7. **AI providers and search/research providers are separate abstractions.**
8. **Fact Sheet is the normal factual boundary before content generation.**
9. **Local media persistence and public HTTPS delivery are separate concerns.**
10. **Runtime/service management is platform-independent.** Application/domain code uses a runtime abstraction; available host utilities are capability-detected behind adapters. The POCO currently uses OpenRC, but OpenRC is not an application assumption.
11. **Source collection configuration, research/evidence policy, and editorial policy have separate canonical roots.**
12. **Application schemas, HTTP API semantics, research methodology, infrastructure/runtime behavior, and operational procedures each have one explicit canonical owner.**

## Configuration ownership

```text
config/
├── sources/
│   ├── registry.yaml
│   ├── feeds.yaml
│   └── collection.yaml
│
├── research/
│   ├── source-policy.yaml
│   ├── search-policy.yaml
│   ├── corroboration.yaml
│   ├── fact-check.yaml
│   └── historical-research.yaml
│
├── editorial/
│   ├── priorities.yaml
│   ├── taxonomy.yaml
│   ├── content-style.yaml
│   ├── risk-policy.yaml
│   └── publishing-policy.yaml
│
├── models/
├── prompts/
└── platforms/
```

Interpretation:

```text
config/sources/   = where/how we collect
config/research/  = how evidence is researched/evaluated
config/editorial/ = what we prioritize/how verified material is presented/reviewed
```

No policy should have two configuration owners.

## Runtime portability

Canonical operator/runtime boundary:

```text
Application / Operator
        ↓
newsctl / RuntimeController
        ↓
RuntimeDetector
        ↓
ServiceManager interface
        ↓
capability-selected host adapter
```

Business/domain code must not directly execute `systemctl`, `rc-service`, `launchctl`, Windows service commands, or equivalent host-specific utilities.

## Current canonical flow

```text
DISCOVERY / COLLECTION
        ↓
NORMALIZATION / CLUSTERING
        ↓
CLAIMS
        ↓
SOURCE_AND_RESEARCH
        ↓
EVIDENCE / FACT CHECK
        ↓
FACT SHEET
        ↓
EDITORIAL INTERPRETATION
        ↓
CONTENT
        ↓
QUALITY CHECK
        ↓
HUMAN APPROVAL (MVP)
        ↓
SOCIAL PUBLISHING
        ↓
ANALYTICS
```

## Documentation hygiene

Current docs should not intentionally retain legacy examples after a contradiction is known.

When a shared contract changes:

1. update `CANONICAL_CONTRACTS.md` in the same change set;
2. update every affected domain document;
3. update examples, schemas, tests, and directory layouts;
4. update configuration ownership if necessary;
5. verify repository-wide terminology before implementation relies on the change.

Git history preserves old designs; current documentation describes the current design.

## Implementation transition

The architecture/documentation set is now sufficient for implementation.

New architecture documents should be added only when they define a genuinely new subsystem or contract that cannot live in an existing owner document.

The next phase should favor code, migrations, tests, configuration schemas, runtime adapters, and focused implementation decisions over further broad architecture expansion.
