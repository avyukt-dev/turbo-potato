# Production acceptance audit — 2026-09-13

## Executive Summary

Audited application SHA: `035a9403223b37e128ab0f2245877b9bd783890b` on
`development`. The user's accepted baseline CI is `34737638596 — SUCCESS`;
historical PRs, CI runs, refs, and branch cleanup were deliberately not re-audited.
Audit branch: `audit/production-acceptance`. This is **not Stage 28**.

The implementation has substantial tested safety architecture: versioned events,
transactional outbox, durable research generations, conservative lineage and
authority, immutable factual snapshots, typed AI boundaries, exact human approval,
publication checkpoints, ambiguity fencing, and durable pause/resume. The ordinary
suite has 706 tests. That is not evidence of production AI quality, device capacity,
complete deployed ingestion, or a complete ingestion-to-publication rehearsal.

Release verdict: **NO_GO for general MVP acceptance and LIVE publication**.
Four confirmed P1 gaps remain; no P0 was reproduced. Controlled local development
and synthetic component staging remain useful, with publication paused and no
production credentials. This is not an authorization to deploy LIVE.

This audit changes no application behavior, policies, prompts, models, or schema.
Only the report, isolated diagnostic scripts, a fixture hook, and the audit-branch
push CI filter were added. The filter is narrowly necessary for the requested
exact-head push CI; previously only `feature/**` and `development` triggered it.

### Environment and evidence limits

Local Linux development host; Python 3.14.4 in `.venv`; PostgreSQL 16.15 and Redis
7.4.11 in existing isolated test containers, ports 55432 and 56379. CI uses Python
3.11, PostgreSQL 16 and Redis 7. No claim is made about POCO/OpenRC deployment,
thermal limits, physical power loss, production credentials, or real model quality.
AI in E2Es is deterministic through the real AIRouter/provider interface; LIVE-mode
transport tests use an injected transport, never Meta network calls.

Tests and canonical owners were inspected across all A–V areas. Negative audit
probes are standalone scripts, excluded from ordinary pytest discovery: successful
reproduction of a defect must not be confused with successful acceptance.

## Release Verdict

| Gate | Decision | Reason |
| --- | --- | --- |
| DEVELOPMENT | Continue | Existing regression suite and isolated diagnostics are useful. |
| STAGING | Restricted synthetic components only | Keep credentials absent, publication paused; full deployment acceptance is blocked. |
| MOCK rehearsal | Component paths pass; complete MVP fails | Caller-supplied media cannot traverse Quality Gate. |
| LIVE | NO_GO | P1 recovery/security/composition gaps plus unproven deployment/AI/account/media readiness. |
| General MVP | NO_GO | No passing full canonical flow or deployment-owned upstream execution boundary. |

Counts: P0 **0**, P1 **4**, P2 **6**, P3 **2**. Five findings below are
`NEEDS_ENVIRONMENT_VALIDATION`; absence of environment evidence is not proof of a
product defect. Findings concern this exact baseline, not an assertion that every
possible vulnerability has been excluded.

## Acceptance Matrix

`PASS` means the stated local/test scope passed, not unrestricted production approval.

| Area | Verdict | Evidence and boundary | Findings |
| --- | --- | --- | --- |
| A. Repository/build integrity | PASS | Clean accepted baseline; editable install, lint, format, compile, existing CI specification. | — |
| B. DB/migrations/constraints | PASS | PG enum/uniqueness/concurrency tests, drift and round trips; synthetic logical restore. Not arbitrary historical production data. | PA-BACK-01 |
| C. Configuration ownership | PASS | Typed source/research/editorial/model/platform/runtime loaders; publishing and authority safety invariants rejected if disabled. | — |
| D. Event/outbox/Redis | PARTIAL | Versioned payloads, processed events, semantic replay, retry/DLQ and stale reclaim pass. Transport-loss restoration incomplete; raw dispatcher errors unsafe. | PA-REC-01, PA-SEC-01 |
| E. Discovery/normalization/clustering | PARTIAL | Durable raw discovery → normalizer → normalized outbox and clustering exercised in PG/Redis. No deployed collection-to-worker loop. | PA-RUN-01 |
| F. Claims/evidence/research | PARTIAL | Real corpus provider, AIRouter assessor, current generations and partial provider failures; no general-web/specialist vendor invented. | PA-RUN-01, PA-AI-01 |
| G. Fact checking/Fact Sheet | PASS | Conservative unresolved lineage, authority-qualified high-risk counts, exact version/provenance/current generations in real pipeline. Deterministic staged labels, not a general semantic verdict engine. | PA-AI-01 |
| H. AI platform | PARTIAL | Routing/schema/reference/retry/provenance/injection isolation tested. Deployed model editorial/evidence quality is not evaluated by fake AI. | PA-AI-01 |
| I. Content generation | PASS | Exact current Fact Sheet, effective brief/language semantic identity, router-only strict output, mandatory review and provenance. | PA-MVP-01 |
| J. Quality Gate | PARTIAL | PASS/FAIL, factual currency, typed brief, quotations, provenance and locks tested. Rejects real caller-owned media. | PA-MVP-01 |
| K. Human review | PASS | Authenticated capability checks, exact version/hash/media binding, optimistic state checks and atomic audit; no automatic publication. | PA-MVP-01 |
| L. Scheduling | PASS | PG due scanner, concurrency, cancellation, eligibility, durable global pause. | PA-RUN-01 |
| M. Social adapter | PARTIAL | MOCK zero transport; JPEG, container/media distinction, conservative readback and throttling tests; current official docs compared. Real account/media delivery not exercised. | PA-META-01 |
| N. Publication execution/recovery | PASS | Short transactions, active-attempt fence, intent checkpoint, external-ID retention, verification-only recovery, pre-intent retry and pause/resume pass with synthetic transport. | PA-REC-01 |
| O. Runtime/newsctl | PARTIAL | Native adapter contracts, closed service mapping, explicit override and advisory profile ordering; no real host service mutation. | PA-OPS-01 |
| P. Health/readiness/metrics | PASS | Canonical readiness, read-only metrics, bounded error-class/platform labels and expected API/scheduler/publisher health tests. Optional sensors normalize UNKNOWN. | PA-OPS-01 |
| Q. Security | PARTIAL | Review/publish auth and secret sentinel/injection/native-command tests pass; outbox diagnostic leak reproduced. No comprehensive vulnerability assessment. | PA-SEC-01, PA-SEC-02 |
| R. Failure recovery | PARTIAL | Worker retries, stale research, crashed publisher checkpoints and rollback/no-ACK tests pass. Lost Redis work has no implemented reconciliation path. | PA-REC-01 |
| S. Backup/restore | PARTIAL | All 113 synthetic rows restore identically; representative core graph survives later migration round trips. Asset files/secure retention/target recovery absent. | PA-BACK-01 |
| T. Performance/resources | PARTIAL | 100/1,000/10,000 short-article search samples measured locally; no sustained inference/POCO test. | PA-PERF-01, PA-OPS-01 |
| U. End-to-end MVP | FAIL | Canonical pipeline reaches approved text; valid media before quality is permanently rejected. Separate seeded publisher E2E cannot prove full composition. | PA-MVP-01 |
| V. Production deployment | FAIL | API/scheduler/publisher runnable; upstream stacks require a hand-driven harness. Target services/model/media/backups not accepted. | PA-RUN-01, PA-OPS-01 |

## P0 Findings

None reproduced. This does not prove absence of all catastrophic failure modes.
In particular, real publication, physical power-loss durability, and hostile
deployment testing were outside the authorized environment.

## P1 Findings

### PA-MVP-01 — Valid media cannot cross the quality/approval boundary

- Severity/confidence: **P1 / CONFIRMED**. Area: J/K/U.
- Release impact: blocks complete MOCK rehearsal, general MVP and LIVE; text-only
  development/component staging can continue.
- Requirement: `TESTING_AND_EVALUATION.md §40`, `SOCIAL_PUBLISHING.md §§7,10,14,16–18`
  and `CONTENT_SCHEMAS.md §§15–16,23`: reviewed publishable variants bind actual
  media, and the complete MVP flow must reach MOCK publication.
- Implementation: `packages/quality/news_ai_quality/service.py:227–228` rejects
  **all** non-empty media identifiers as fabricated. Meanwhile
  `packages/publishing/news_ai_publishing/instagram_request.py:37–56` requires
  non-empty durable assets matching slide count.
- Reproduction: standalone `tests/audit/mvp_probe.py` drives the existing real
  PostgreSQL/Redis pipeline with production stacks and official fake-AI injection.
  It attaches two valid durable JPEG records before Quality Gate. The reliable
  worker emits a permanent DLQ diagnostic, no quality result, no publication;
  the variant stays NOT_READY.
- Expected/actual: valid caller assets are independently checked and bound before
  human approval / valid assets are rejected before assessment.
- Risk: ordinary generated content cannot become a publishable carousel through
  supported composition. Attaching media after checking is not a safe workaround:
  `packages/review/news_ai_review/service.py:489–492` detects a changed quality
  artifact; approval also binds media at `:528–550`.
- Smallest remediation: define a validated caller-owned attachment handoff before
  quality/review; validate durable media rather than interpreting existence as
  fabrication. Preserve version invalidation and exact hashes. No AI media
  generation or automatic approval needed. Add a genuine full-flow positive E2E
  without manually editing QualityCheck hashes (seeded scheduler helpers currently
  do this at `tests/unit/publishing/test_scheduler.py:89–91`).
- Slice: **A — media/quality/review handoff**. Existing canonical contract restored;
  no factual/event vocabulary redesign required.

### PA-RUN-01 — No production owner runs the upstream pipeline continuously

- Severity/confidence: **P1 / CONFIRMED**. Area: E/F/V.
- Release impact: blocks deployed general MVP; hand-driven synthetic staging remains
  possible. Not a request for one process per logical stage.
- Requirement: `ARCHITECTURE.md §§5,22`, `INFRASTRUCTURE_AND_DEPLOYMENT.md §§11–12,26`:
  deployable collection through the application pipeline, with logical boundaries
  allowed to share a process.
- Evidence: executable boundaries exist in `apps/scheduler/.../__main__.py`,
  `apps/publisher/.../__main__.py`/`runner.py`, and API ASGI `main.py`. Collector,
  processor, research and AI stacks expose callable workers/composition but no
  executable integrated upstream loop. `config/runtime/services.yaml:9–13`
  truthfully keeps those logical stages unmanaged/non-expected.
- Reproduction: inventory `rg --files apps | rg '__main__|runner|composition'`;
  compare `tests/integration/test_production_research_pipeline.py:406–429`, which
  manually advances each worker and dispatcher.
- Expected/actual: an explicit production process owns those stages / deployment
  cannot run the canonical ingestion flow using the supplied entrypoints alone.
- Risk: API/scheduler/publisher health does not mean news ingestion is progressing.
- Smallest remediation: one minimal production runner/composition boundary for
  collection and existing upstream workers plus outbox dispatch, or an explicitly
  configured deployed owner. Add bounded polling, shutdown, injected dependencies,
  readiness and restart integration proof. Do not create fake standalone workers
  or service-manager orchestration just to match logical names.
- Slice: **B — upstream execution/deployment ownership**. No event/schema redesign.

### PA-REC-01 — Lost published transport work is not reconciled from PostgreSQL

- Severity/confidence: **P1 / CONFIRMED**. Area: D/R.
- Release impact: blocks accepted restart/restore recovery for general MVP/LIVE;
  development can use disposable infrastructure.
- Requirement: `CANONICAL_CONTRACTS.md §4`, `EVENTS.md §§29–32,38–44`, infrastructure
  `§17` and runbook `§39`: Redis loss retains durable state **and recovery**.
- Implementation: `packages/events/news_ai_events/dispatcher.py:103–115` selects
  PENDING rows only; its stale recovery selects PUBLISHING only. Scheduler
  `packages/publishing/news_ai_publishing/scheduler.py:37–42` excludes scheduled
  rows already carrying a scheduled event ID. No recovery command reconstructs
  missing consumer work from published outbox/current domain state.
- Reproduction: `tests/audit/acceptance_probes.py`: commit one canonical outbox
  event, publish to a unique real Redis stream, delete only that stream, then
  dispatch again. Initial stream length 1; durable row remains PUBLISHED;
  recovery claims 0; stream length remains 0. This models lost transport data,
  not an ordinary temporary connection outage or mere worker redelivery.
- Expected/actual: uncompleted durable operations become deliverable again under
  controlled reconciliation / event intent survives but work stays absent.
- Risk: workflows stall indefinitely after Redis loss, including queued publication
  work. This is **not** erased business data and is not proof of duplicate publishing.
- Smallest remediation: explicit bounded recovery/replay using original event IDs,
  consumer processing state and current domain state. Require publication pause,
  dry-run/reporting, and preserve post-intent/external-ID fences. Never mass-reset
  all published rows or blindly replay side effects. Add loss/rebuild/idempotent
  recovery E2Es and an operator procedure.
- Slice: **C — durable event reconciliation**.

### PA-SEC-01 — Outbox retries persist untrusted raw exceptions

- Severity/confidence: **P1 / CONFIRMED**. Area: D/Q.
- Release impact: blocks production credential use; synthetic development only.
- Requirement: `INFRASTRUCTURE_AND_DEPLOYMENT.md §23`, `DATA_MODEL.md §§14,20,26`,
  `EVENTS.md §42`: secrets never enter ordinary DB diagnostics or events/logs.
- Implementation: `packages/events/news_ai_events/dispatcher.py:142–149`
  interpolates exception type and arbitrary exception text into `last_error`.
  This path also catches database result-update errors after publication (`:163–166`).
- Reproduction: standalone outbox probe raises an exception containing a synthetic
  authorization bearer sentinel. Retry is durably recorded; sentinel retention
  in `event_outbox.last_error` is **true**, diagnostic length 73. No real secret
  was used or printed. Current unit test explicitly expects raw text in
  `tests/unit/events/test_dispatcher.py:123`.
- Expected/actual: bounded normalized/redacted diagnostics / arbitrary text stored.
- Risk: provider URLs, credentials or SQL bound input may enter backups/operator DB
  diagnostics. This is not evidence that `/metrics` exposes raw errors.
- Smallest remediation: use reliability-neutral closed error codes and bounded
  secret-safe diagnostics, including DB exception paths; add sentinel and oversize
  regressions. Keep the original event identity and safe retry semantics.
- Slice: **D — dispatcher diagnostic sanitization**. No migration expected.

## P2 Findings

| ID | Confidence / area / impact | Exact evidence, reproduction and expected vs actual | Risk, smallest remediation and slice |
| --- | --- | --- | --- |
| PA-PERF-01 | CONFIRMED; T; staging sizing/LIVE resource gate | `packages/evidence/news_ai_evidence/corpus.py:74–95,126`: materializes every active latest article version, then filters/tokenizes/ranks in Python synchronously. Standalone samples below grow with corpus even for max_results=10. Expected complete and resource-controlled search; actual complete but whole-corpus per-query allocation. | Memory/latency and event-loop contention on POCO. DB-side equivalent filters/ranking or a correctness-preserving bounded-memory strategy; never restore silent truncation. Slice E, measured capacity/queries. |
| PA-AI-01 | NEEDS_ENVIRONMENT_VALIDATION; H/F/I/J; blocks LIVE model promotion | `config/models/providers.yaml:5–14` selects a configured local model alias/context; deterministic E2E responses are in `tests/integration/test_production_research_pipeline.py:77–193`. `AI_PLATFORM.md §§27,38–39`, testing §§27,29,33 require measured quality/thermal regression. Expected deployed-model golden metrics; actual protocol fixtures, not model evaluation. | Wrong relation/claims or undetected content drift despite valid JSON. Run fixed factual/legal/historical/sensitive/injection datasets through real router/model; record thresholds and promotion/rollback. No prompt/model tuning in audit. Slice E. |
| PA-OPS-01 | NEEDS_ENVIRONMENT_VALIDATION; O/P/V; blocks target LIVE deployment | Runtime adapter tests use injected capabilities/commands; `config/runtime/services.yaml` describes expected services, not installed units. Infrastructure §§9,12–14,19,26 requires actual startup/time/private networking/resource behavior. Expected device install/reboot/health proof; actual local synthetic tests. | Units may be missing, time inaccurate or inference exhaust resources. POCO checklist below; read-only capability checks first, explicit authorization for later deployment. Slice B/E. |
| PA-BACK-01 | NEEDS_ENVIRONMENT_VALIDATION; S; blocks target LIVE restore acceptance | Infrastructure §28/runbook §§38–39/testing §39 requires DB+config+media+deployment backup. `tests/audit/restore_probe.py` verifies local logical DB only. Expected secure retained full-system recovery; no asset-byte restore, encrypted/off-device retention/RPO/RTO or target restore evidence. | DB provenance can survive while required media/config does not. Define ownership/retention, matching PG tools, protected credentials, restore drill and paused recovery; no unsecured production dump. Slice C. |
| PA-SEC-02 | NEEDS_ENVIRONMENT_VALIDATION; Q; security acceptance gate | Testing §37 and API §§4,43 require security checks. Existing auth/sentinel/injection/native-command tests pass; no dependency-vulnerability/secret-history scanner report or deployed network/role/backup-access assessment was produced. Expected threat/dependency assessment; actual targeted regression evidence only. | Unknown dependency/exposure risks. Approved scanners and least-privilege deployment review; don't claim absence of vulnerabilities from pattern matching. Slice F, security acceptance. |
| PA-META-01 | NEEDS_ENVIRONMENT_VALIDATION; M; LIVE gate | `config/platforms/instagram.yaml`, adapter/transport and official comparison below support configured API semantics. No actual professional account, scope/token lifecycle, public JPEG delivery or platform rate-state rehearsal. Expected approved account/media preflight; actual mock transport only. | Real authorization/media/provider constraints can fail. Explicitly authorized read-only account/token/media validation before a separate supervised publication acceptance; never infer readiness from config. Slice B/F. |

## P3 Findings

| ID | Confidence / area / impact | Evidence and expected vs actual | Remediation / slice |
| --- | --- | --- | --- |
| PA-API-01 | CONFIRMED; API/build; operator breadth, not approval safety | `docs/API_SPEC.md §§8–18,28–31` describes story/claim/source/research/job/config APIs beyond the review/publication/health routes present in `apps/api/news_ai_api/main.py`. Expected eventual documented surface; actual implemented MVP subset. | Inventory explicitly supported endpoints and prioritize operator needs; don't weaken canonical safety or invent missing APIs in audit. Post-MVP/API slice after acceptance blockers. |
| PA-DOC-01 | CONFIRMED; operations; cleanup only | `apps/scheduler/news_ai_scheduler/__main__.py:1` says publication execution belongs to a later stage although accepted Stage 26 now exists. Expected current description; actual stale docstring. | Focused terminology cleanup, no behavior change; leave canonical owners intact. Post-acceptance cleanup. |

## Environment Validation Required

Five P2 findings are explicitly environment-gated. Real AI quality, POCO native
startup/reboot, secret/dependency/network assessment, asset/secure backup recovery,
and real account/media readiness remain unproven. No measured confidence score
is interpreted as a calibrated probability of truth.

## Full E2E Result

**FAIL for the full MVP.** The existing real PostgreSQL/Redis E2E passes through
discovery, normalization, clustering, router-backed claim extraction, research,
explicit evidence assessment, lineage/authority-aware FactCheck, story verification,
Fact Sheet, content generation, Quality Gate and authenticated human decision.
It preserves PARTIALLY_SUPPORTED and UNVERIFIED claims: stage completion does not
mean every proposition is true. Causation/correlation and source-version/AIRun
provenance are asserted. Its three cases are PASS/approve, PASS/request-changes,
and semantic FAIL/NOT_READY; it deliberately asserts **zero Publications**.

The new standalone probe does not inject final claims/evidence or manually rewrite
quality/approval hashes. It adds caller-owned synthetic JPEG metadata at the real
handoff and reproduces PA-MVP-01. Separate scheduler/publisher E2Es seeded from
review fixtures pass but **cannot substitute** for this full-flow proof.

No asset bytes were generated or hosted; MOCK needs none. Public delivery remains
an external caller/deployment responsibility, not permission to fetch arbitrary URLs.

## Failure-Recovery Result

| Scenario | Result and limitation |
| --- | --- |
| Redis transport data loss | FAIL automatic reconciliation; durable outbox survives (PA-REC-01). |
| Outbox connection failure/backoff | Existing bounded retries, final exhaustion and stale lease tests PASS. Secret-safe exception storage FAIL (PA-SEC-01). |
| Publish succeeds, DB status/ACK interrupted | Existing outbox duplicate and publisher checkpoint/ACK-recovery regressions PASS with synthetic boundaries. |
| Old research finishes after new research | Current generation rejects stale mutation/downstream emission; tests PASS including partial multi-Claim currency. |
| Deterministic invalid payload/provider output | Strict schema/reference validation and durable permanent DLQ tests PASS. |
| Provider outage/partial evidence | Transient total failure retries; useful partial work retained; tests PASS. |
| Global pause/resume | Real PG/Redis running-process tests PASS; no failure-budget consumption or permanent queued-work loss. |
| Publisher PREPARED recovery | FINISHED readiness recheck, defer/expired/error/unexpected published fences PASS. |
| Lost publish response after intent | Remains AMBIGUOUS/BLOCKED; no blind replay. Tests PASS, not a lost-ID recovery guarantee. |
| External ID persisted | Verification-only recovery; exact ID checked and unconfirmed outcome never authoritatively PUBLISHED. Tests PASS. |
| Physical power loss/real provider outage | NOT_TESTED; no POCO or real social environment touched. |

## Backup/Restore Result

`tests/audit/restore_probe.py` uses a uniquely named disposable local database.
Logical backup of the synthetic graph restored **113 rows across 29 non-empty
tables identically**, comparing per-table sorted JSON row hashes, not counts only.
Archive size **149,249 bytes**. Graph includes factual/AI/content/review state and
separately seeded scheduler/MOCK execution state, not a claimed complete MVP E2E.
Source database unchanged by restore/migration operations.

On that clone, `0012 → 0011 → head`, `head → 0010 → head`, and
`head → 0009 → head` preserve all compared factual/AI/content/review core rows;
each re-upgrade passes Alembic drift check. Existing runtime migration regression
also proves exact Stage-26 columns/checks/uniques/FKs/index schema restoration.
Lower-stage tables and stage-specific audits are intentionally removed on downgrade;
this is **not** authorization for lossless production rollback of publication history.
Re-upgrade safely seeds publication paused rather than restoring a prior permission.

Host pg_dump/restore 18.6 against PG16 initially failed because newer dump session
settings are not backwards-compatible. Matching PG16.15 tools in the test container
passed. This is a tested operational toolchain lesson, not an application migration
defect. Asset files, secret storage, off-device retention and target RPO/RTO are not
verified; PA-BACK-01 remains open.

## Security Result

- Unauthorized review/approval/publish, exact artifact mismatch, malformed IDs,
  stale factual state and no privileged HTTP service control: existing tests PASS.
- Metrics use durable attempt **error_class**, not provider code; known categories
  TRANSIENT/PERMANENT/AMBIGUOUS and OTHER are bounded; platform taxonomy includes
  INSTAGRAM/X/FACEBOOK/TELEGRAM. Secret sentinels excluded from metrics/CLI/API tests.
- Raw source injection is isolated as material, not tool/system authorization.
  Structured output/reference/quote checks do not prove actual model immunity.
- Native utilities occur only inside adapters; shell execution is disabled,
  logical-to-native service mapping closed and output/errors bounded/normalized.
- Media URL checks are structural HTTPS/private-literal/credential restrictions,
  not proof of public reachability or DNS trust. Corpus assessment makes no
  arbitrary candidate fetch; no real media URL was fetched in audit.
- **Secret retention FAIL in dispatcher diagnostics**, PA-SEC-01. Passing other
  sentinel fixtures is not whole-system secret safety.
- No comprehensive scanner findings or deployed pen-test results are claimed.
  Existing reviewed authentication is a configured operator token/principal,
  not a full identity lifecycle/credential administration product.

### Current official Meta comparison (checked 2026-09-13)

Configured v26.0 is listed as released July 29, 2026 with expiration TBD in
[Meta Graph API versions](https://developers.facebook.com/docs/graph-api/changelog/versions).
Instagram-login host/scopes, JPEG-only IMAGE, carousel maximum ten, container
expiry/status readiness, one-minute/five-minute polling and media-ID response
match the deployed configuration/adapter design. Published-media readback uses
media fields rather than container status.
[Meta content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing).

The same content guide has inconsistent rolling-day limits (main text 100 versus
carousel section 50); do not hard-code either as a universal quota. Validate actual
account/platform limit information at deployment. No general request-idempotency
or lost-response Media-ID recovery promise was found; conservative ambiguity
fencing remains appropriate. This comparison is documentation review, not LIVE proof.
[Meta content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing).

Recognized code families, including Instagram 80002 and application/user/Page
throttling, are classified as rate limits rather than validation/permission;
OAuth 190 is handled separately.
[Meta rate limits](https://developers.facebook.com/docs/graph-api/overview/rate-limiting),
[Meta error handling](https://developers.facebook.com/docs/graph-api/guides/error-handling).

## Performance Observations

Standalone corpus search samples: one cold measured query per size, tracemalloc
enabled, approximately 600-byte bodies, one active synthetic source and latest
version per Article; max_results=10. These are not percentile/production benchmarks.

| Eligible articles | Returned | Seconds | Peak Python allocations MiB |
| --- | --- | --- | --- |
| 100 | 10 | 0.052 | 0.83 |
| 1,000 | 10 | 0.267 | 6.33 |
| 10,000 | 10 | 2.843 | 65.12 |

This excludes full RSS bodies, LLM memory, concurrent requests, inference and DB
server memory. Actual RSS content and repeated per-claim research queries can cost
much more. Complete-corpus correctness was retained; no arbitrary scan cutoff was
introduced. The Postgres review skill informed inspection of query materialization
and index/access-pattern evidence, not schema changes.

Review queue uses bounded batches but can scan many candidates to validate exact
artifact eligibility (`packages/review/news_ai_review/service.py:282–316`); no
large invalid-candidate workload or target latency SLO was measured. Health DB
aggregates have statement timeout and bounded labels. Pending/failed outbox, event,
AIRun and audit growth/retention under sustained load remain sizing work, not
permission to delete provenance indiscriminately.

## Target POCO Validation Checklist

- Record Python/dependency/PG/Redis versions, aarch64 wheel/build viability and disk.
- Read-only capability detect; verify actual OpenRC usability and mapped units.
- Install/run the explicit upstream owner after PA-RUN-01, plus API/scheduler/publisher.
- Validate private binds, Tailscale/SSH, TLS/time sync and least-privilege secrets.
- Resolve required AI routes, check local endpoint/model and run golden quality suite.
- Measure sustained RAM/CPU/temperature/battery, query load and thermal throttling.
- Reboot/reconnect with publishing paused; prove durable pending work recovery,
  READY/UNKNOWN service semantics and no duplicate provider side effects.
- Restore DB/config/media on isolated target storage and record RPO/RTO.
- Keep optional missing sensors UNKNOWN/NOT_APPLICABLE, never invented healthy.

All above target operations remain unperformed and require separate authorization.

## LIVE Publication Checklist

- Close four P1 findings and pass full MOCK E2E with actual reviewed attachment hashes.
- Approve deployed model quality and sustained target capacity.
- Validate professional account/scopes/token lifecycle and safe credential references.
- Verify caller-owned ordinary JPEG bytes, public HTTPS availability/expiry and
  exact immutable reviewed media metadata, without exposing internal files/services.
- Verify provider version/account quota; configure polling/backoff safely.
- Confirm environment hard pause cannot be overridden, DB read failure fails closed,
  and resume/reconciliation does not replay post-intent or known-ID publication.
- Test protected backup/restore; require explicit operator human approval for exact
  content and a separately authorized supervised external publication rehearsal.

Nothing in this audit enables LIVE mode or authorizes a real social call.

## CI Gate Recommendations and Local Validation

Ordinary CI must remain green; known-defect scripts are explicit audit tools, not
tests that silently redefine defective behavior as correct. No xfail was added.
Current CI runs install/lint/format/compile/upgrade/drift/pytest/base downgrade/
re-upgrade against real PG/Redis. Extend dedicated acceptance CI after remediation
to run complete MOCK flow, Redis-loss reconciliation and logical restore with
matching PG tools. Separate scheduled authorized real-model/device evaluations
from ordinary network-free CI. Do not put credentials in CI fixtures.

Recorded local baseline: editable install, Ruff lint, 212-file format check,
compileall, upgrade/drift PASS; **706 passed, 2 warnings, 88.12s**. Focused run:
**296 existing tests plus one isolated negative media reproduction passed**.
The latter was subsequently moved out of pytest discovery; it is a reproduced
acceptance failure, not an additional success gate. Framework warnings concern
Starlette/httpx and anyio compatibility, not failed application assertions.

Final audit-change local gates: editable install, lint, **215-file** format check,
compileall, head upgrade, drift check, **706 passed / 2 warnings / 106.27s**, base
downgrade and head re-upgrade all PASS. Focused runtime/detection/API health:
**78 passed / 2 warnings / 2.10s**. A read-only development-host runtime probe
selects usable systemd; an explicit manual override reports news-api UNKNOWN,
not healthy. No service was mutated.

Dedicated PostgreSQL/Redis integration suite, run separately after re-upgrade:
**60 passed / 2 warnings / 73.17s**. Ordinary collection remains **706 tests**;
there are **three standalone audit scripts**, not extra acceptance passes.

Remote delivery/CI results are recorded in the audit handoff and must not be inferred
from baseline CI. No new migration; head remains `0012_runtime_controls`. No
production schema or environment was changed.

### Reproduction commands

Use an explicit **isolated test** DB/Redis and `NEWS_AI_ENVIRONMENT=test`; these
tools intentionally insert synthetic records. The MVP harness truncates test
tables/clears canonical test streams; never point it at production.

```bash
PYTHONPATH=tests .venv/bin/python tests/audit/mvp_probe.py
.venv/bin/python tests/audit/acceptance_probes.py
NEWS_AI_AUDIT_POSTGRES_CONTAINER=news-ai-stage27-postgres \
  .venv/bin/python tests/audit/restore_probe.py
```

The last container option is optional, uses a validated explicit test container,
and selects matching server dump/restore utilities. Restore/migration work runs
only against a uniquely named disposable clone. Source backup data must be synthetic.

## Remediation Plan

1. **D — dispatcher sanitization:** bounded normalized secret-safe errors; sentinel/
   oversize/DB-update-failure tests. No business/AI/schema change.
2. **A — media handoff:** validate caller-owned attachment/version before quality and
   exact approval; complete PG/Redis ingestion→MOCK publication without forged hashes.
3. **C — reconciliation/restore:** original IDs + processed/domain state, paused
   bounded dry-run/recovery, lost stream/group and published-event recovery tests.
   Include secure DB/config/media restore procedure.
4. **B — upstream/deployment ownership:** minimal integrated runner of existing
   components/outbox, shutdown and read-only health; target installation/restart proof.
5. **E/F — measured AI/resource/security/account acceptance:** real router golden
   scores, capacity/thermal tests, query optimization only with measured evidence,
   approved scanners and separately authorized platform/media preflight.

Do not combine unrelated fixes or weaken approval/ambiguity fencing to make E2E pass.
This report proposes those slices; it does not implement them.

## Known Accepted Debt

Canonical build order stops at Stage 27. No analytics, additional social adapter,
automatic high-risk publishing, translation, AI/media generation, full multi-user
identity administration or broad process supervision was added. Staged specialized
MISLEADING/OUT_OF_CONTEXT/FABRICATED/SATIRE classifiers are not falsely claimed
implemented. Corpus-only search does not pretend to provide general-web/specialist
access; missing capability/insufficient evidence remains visible and UNVERIFIED
does not become FALSE/REFUTED. Retain these boundaries explicitly in deployments.

No real social publication, production credential, production database operation,
POCO access, native service mutation, branch cleanup or broad remediation occurred.
Audit PR is intended to remain **UNMERGED** for architectural review.
