# News AI Social Media Manager

# OPERATIONS_RUNBOOK.md

**Status:** Canonical
**Document Role:** Source of truth for day-to-day operation, health checks, incident triage, deployment execution, backup/recovery, publication safety, and runtime-independent service procedures.

Infrastructure architecture is owned by `INFRASTRUCTURE_AND_DEPLOYMENT.md`.

Shared lifecycle/runtime semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This runbook defines how to operate the News AI deployment safely without assuming one operating system or service manager.

Initial runtime philosophy:

```text
ONE HOST
ONE POSTGRESQL
ONE REDIS
ONE LOCAL AI SERVICE
ONE APPLICATION STACK
CONTROLLED CLOUD PROVIDERS
```

The current POCO host uses OpenRC, but operator workflows target the platform-neutral runtime abstraction.

---

# 2. Operator Interface

Canonical operator surface:

```text
newsctl runtime detect
newsctl service list
newsctl service status <service>
newsctl service start <service>
newsctl service stop <service>
newsctl service restart <service>
newsctl service enable <service>
newsctl service disable <service>
newsctl health
newsctl publish pause
newsctl publish resume
```

`newsctl`/`RuntimeController` detects the supported host capability and delegates to the appropriate runtime adapter.

Operational scripts should not directly assume `systemctl`, `rc-service`, `launchctl`, or another one host utility.

---

# 3. Initial Server Profile

Current target:

```text
Device: Xiaomi POCO F1
Codename: beryllium
Architecture: aarch64
OS: postmarketOS
Mode: headless CLI
Current detected service manager: OpenRC
Timezone: Asia/Kolkata
Remote administration: Tailscale + SSH
```

These are deployment characteristics, not application assumptions.

---

# 4. Operational Priorities

When something fails:

```text
1. stop unsafe/duplicate publication
2. protect PostgreSQL data
3. preserve logs/audit evidence
4. restore network/time/dependencies
5. restore event processing
6. restore AI/media/platform dependencies
7. re-enable publication only after validation
```

---

# 5. Global Publishing Kill Switch

Use:

```text
newsctl publish pause
```

when implemented by the operator tool.

When active:

```text
collection may continue
research may continue
AI analysis may continue
content generation may continue
review may continue
new external publication calls stop
```

Use during:

```text
platform API incidents
credential compromise/errors
duplicate-post behavior
model/content regression
prompt-injection incident
database inconsistency
major breaking-news uncertainty
unexpected mass scheduling
```

The kill switch is an application-level safety control and must not depend on one service manager.

---

# 6. Runtime Detection

Before host-level service operations:

```text
newsctl runtime detect
```

Expected conceptual output:

```text
platform: linux
service_manager: openrc
adapter: OpenRCServiceManager
status: supported
```

Detection should verify executable capability and runtime metadata.

If no supported manager is detected:

```text
status: unsupported/manual
```

Do not guess a system command.

---

# 7. Service Health

Use generic service commands:

```text
newsctl service list
newsctl service status postgres
newsctl service status redis
newsctl service status api
newsctl service status collector
newsctl service status processor
newsctl service status research-worker
newsctl service status ai-worker
newsctl service status publisher
newsctl service status scheduler
```

Logical service names map to deployment-specific native names through configuration/runtime adapters.

---

# 8. Native Diagnostic Fallback

When the generic operator utility itself is unavailable, a human operator may use the native utility **after detecting the host runtime**.

Current POCO example only:

```text
OpenRC detected
→ use OpenRC diagnostic utilities manually
```

Other hosts may use different tools.

Native commands are fallback operational diagnostics, not application architecture and not commands that business code should execute directly.

---

# 9. Whole-System Health

Canonical command:

```text
newsctl health
```

Health should cover:

```text
SYSTEM
├── CPU
├── RAM
├── disk
├── temperature
└── uptime

NETWORK
├── connectivity
├── DNS
└── Tailscale

SERVICES
├── PostgreSQL
├── Redis
├── API
├── Collector
├── Processor
├── Research Worker
├── AI Worker
├── Publisher
└── Scheduler

AI
├── local model
├── inference latency
├── queue depth
└── failures

JOBS
├── pending
├── running
├── failed
└── retrying

SOCIAL
├── adapter/account health
├── rate limits
└── publishing errors
```

Do not expose secrets.

---

# 10. Startup Dependency Order

Logical order:

```text
network
  ↓
time synchronization
  ↓
PostgreSQL
  ↓
Redis
  ↓
local AI service
  ↓
API / workers
  ↓
scheduler
  ↓
publisher eligibility
```

The runtime adapter should enforce only necessary dependencies.

---

# 11. Time Synchronization

Accurate time is required for OAuth, TLS, scheduling, event timestamps, audit logs, analytics, and Tailscale.

The health layer should detect available time-synchronization capability rather than hard-code one tool into application logic.

Current POCO may use chrony, but future hosts may differ.

Canonical database time remains UTC/`TIMESTAMPTZ`.

---

# 12. Network Checks

Generic health should validate:

```text
interface up
route available
DNS works
internet reachability where required
Tailscale/private-management reachability
```

Native diagnostic tools may vary by platform and belong to operator troubleshooting, not business code.

Successful Wi-Fi association is not proof that DNS/internet is healthy.

---

# 13. Tailscale and SSH

Prefer private/Tailscale access over public SSH exposure.

If remote access fails, check:

```text
host network
DNS/time
Tailscale daemon/session
SSH service
firewall/access policy
credentials/keys
```

Do not store reusable Tailscale authentication keys in docs/logs.

---

# 14. PostgreSQL Health

PostgreSQL is durable truth.

Application-level health should verify an actual configured database query/transaction capability, not merely process existence.

If PostgreSQL is unavailable:

```text
external publication stops
business-state mutation stops safely
workers do not invent state from Redis
```

Do not continue publication based on queued messages alone.

---

# 15. Redis Health

Redis is event/work transport and short-lived coordination.

Health should verify:

```text
connection
ping/readiness
stream access
consumer-group state
pending age
```

If Redis is unavailable:

```text
PostgreSQL remains authoritative
outbox remains durable
new async delivery pauses/fails safely
publication must not bypass scheduling
```

---

# 16. Queue Health

Monitor:

```text
stream
consumer group
pending count
oldest pending age
processing rate
failure rate
retry count
dead-letter count
```

A growing queue is a capacity/dependency signal, not a reason to discard events.

---

# 17. Stale/Pending Events

For stale work:

```text
identify event/job
load current PostgreSQL state
verify transition still valid
claim/retry only when safe
preserve idempotency
record result
```

Never replay publication side effects blindly.

---

# 18. Dead-Letter Handling

```text
inspect original event/job
inspect current PostgreSQL state
classify root cause
fix dependency/data/policy issue
requeue only when transition remains valid
```

Do not mass-replay publication dead letters without safety checks.

---

# 19. API Health

Use:

```text
GET /health
GET /ready
```

`/health` = process liveness.

`/ready` = required dependency readiness.

A live API with an unavailable database is not production-ready.

---

# 20. Collector Health

Monitor:

```text
last successful poll
source degradation/disablement
articles discovered rate
fetch/parse failures
queue lag
```

One broken source must not stop all collection.

---

# 21. Processor Health

Monitor:

```text
normalization throughput
deduplication failures
story-clustering latency
entity/classification failures
article queue lag
```

Workers always reload current PostgreSQL state before material transitions.

---

# 22. Research Worker Health

Monitor:

```text
research queue depth
queries per job
primary-source discovery rate
provider errors
source-fetch failures
contradictions found
budget exhaustion
research latency
```

Budget exhaustion must not become factual certainty.

---

# 23. AI Health

Monitor:

```text
process/endpoint alive
model loaded
inference succeeds
latency
tokens/sec where available
queue depth
failure rate
RAM/CPU/thermal behavior
```

Local/cloud model availability never changes the evidence rules.

---

# 24. AI Failure

If local AI fails:

```text
bounded retry
allowed fallback according to AI routing policy
otherwise queue/fail safely
```

If cloud AI fails:

```text
bounded retry
allowed provider fallback
preserve provenance/job state
```

Do not route sensitive material to arbitrary providers.

Inspect per-key AI credential state without exposing secrets:

```bash
newsctl ai credentials status
newsctl ai credentials probe --pool groq-production --slot 1
newsctl ai credentials reset --pool groq-production --slot 1 --reason "credential replaced"
```

`status` reports only pool, provider, slot, closed state/reason, bounded cooldown, timestamps, and
revision. `AUTH_FAILED` and `UNKNOWN` keys remain disabled until an explicit audited reset, a safe
supported probe, or secret replacement. A provider without a safe non-generative probe returns
`PROBE_UNSUPPORTED`; the command never hides a billable inference operation.

---

# 25. POCO Resource Policy

Monitor:

```text
RAM
CPU
storage
battery/power
temperature
inference latency
queue depth
```

If sustained local inference destabilizes the host:

```text
reduce concurrency
reduce model size
reduce context length
delay optional work
route allowed work to cloud
```

Stability takes priority over local inference throughput.

---

# 26. Disk Space

Monitor:

```text
PostgreSQL data
logs
media
models
backups
temporary files
```

Do not delete provenance/evidence/publication history merely to free space without an explicit retention decision.

---

# 27. Media Storage and Delivery

Local media persistence does not imply public accessibility.

If a platform needs a public URL, validate:

```text
asset exists
intended public-delivery mechanism is active
HTTPS/DNS works
MIME type is correct
URL expiry permits ingestion
platform can reach it
```

Do not expose unrelated internal services.

---

# 28. Publisher Health

Monitor:

```text
queue depth
running attempts
stuck PUBLISHING state
retrying attempts
platform auth failures
rate limits
media failures
duplicate-prevention events
```

Health should be visible per platform/account where useful.

---

# 29. MVP Approval Check

Before every external publication:

```text
Fact Sheet valid
content quality checked
human approval valid for exact content version
publication state eligible
account active
kill switch off
media eligible
platform validation passes
idempotency check passes
```

No low-risk exception exists in the MVP.

---

# 30. Ambiguous Publication Outcome

If a platform call times out after transmission:

```text
DO NOT blindly retry
```

Instead:

```text
record ambiguous attempt
verify external state where possible
if published → persist ID and mark PUBLISHED
if definitely not published → retry under policy
if uncertain → BLOCK / human intervention
```

Duplicate avoidance is more important than fast retry.

---

# 31. Platform Authentication Failure

On non-transient authentication/permission failure:

```text
stop automatic retries
mark account AUTH_ERROR / REAUTH_REQUIRED
pause affected publication jobs
preserve other platform processing
reauthenticate securely
verify capabilities
resume
```

Never log tokens.

---

# 32. Logs

Structured logs should include:

```text
timestamp
level
service
event/action
request_id
job_id
story_id where relevant
publication_id where relevant
error_code
duration
runtime adapter where operationally relevant
```

Never log secrets, credentials, private keys, authorization headers, or unnecessary personal data.

---

# 33. Incident Triage

```text
1. Is unsafe publication occurring?
2. Is the host alive?
3. Is network/time healthy?
4. Is PostgreSQL healthy?
5. Is Redis healthy?
6. Is API ready?
7. Are workers consuming?
8. Is research functioning?
9. Is AI functioning?
10. Is media delivery healthy?
11. Are external platform APIs healthy?
```

---

# 34. Incident Severity

Operational severity is distinct from editorial `RiskLevel`.

Suggested:

```text
SEV-1 unsafe/duplicate mass publication, credential compromise, data corruption
SEV-2 publication unavailable, database/major pipeline outage
SEV-3 one worker/provider/platform degraded with safe queueing/fallback
SEV-4 minor source/background failure
```

---

# 35. SEV-1 Immediate Actions

```text
activate publication kill switch
preserve logs/state
disable compromised credentials/accounts if applicable
stop affected workers through runtime abstraction if necessary
protect PostgreSQL
identify scope
avoid destructive cleanup
```

Resume publication only after safety validation.

---

# 36. Deployment Flow

```text
approved code
   ↓
tests
   ↓
dependency/build preparation
   ↓
migration check
   ↓
backup when warranted
   ↓
apply migration
   ↓
deploy code/config
   ↓
restart affected services via RuntimeController/newsctl
   ↓
health/readiness
   ↓
smoke test
   ↓
resume normal processing/publication
```

---

# 37. Migration Safety

Every schema change uses a versioned migration.

Before production migration:

```text
review migration
backup when appropriate
test against representative data
apply
validate constraints/readiness
smoke test
```

Do not blindly roll database schema backward when data compatibility is uncertain.

---

# 38. Backup

Back up:

```text
PostgreSQL
versioned configuration
required media/assets according to retention policy
deployment/runtime configuration
```

Do not place secrets in unsecured backups.

A backup is not verified until a restore has been tested.

---

# 39. Recovery

General recovery order:

```text
1. pause publication
2. protect/preserve current state and logs
3. restore/verify PostgreSQL
4. restore configuration/assets as required
5. restore Redis/event delivery
6. restore runtime-managed services
7. run health/readiness checks
8. run smoke pipeline
9. re-enable publication only after validation
```

## 39.1 PostgreSQL restore with Redis transport loss

For a verified restore/loss incident:

1. Activate the environment hard publication pause where available.
2. Activate the durable PostgreSQL publication pause before transport replay.
3. Stop or quiet mutating workers as required by the selected restore procedure.
4. Restore PostgreSQL with tooling compatible with the server major version.
5. Restore canonical configuration from its protected versioned source.
6. Restore and validate caller-owned media references/assets according to their actual
   ownership and retention policy.
7. Start Redis as disposable/empty transport if its state cannot be trusted.
8. Start required consumers, or allow reconciliation to create only their missing
   canonical groups.
9. Run `newsctl events reconcile --dry-run --limit 100` in bounded batches.
10. Review every `MANUAL_REVIEW_REQUIRED`, `UNSUPPORTED`, and invalid item.
11. Run `newsctl events reconcile --apply --limit 100 --reason "verified Redis restore"`.
12. Let workers converge while publication remains paused and repeat bounded dry-runs.
13. Inspect publication attempts, external IDs, and AMBIGUOUS/post-intent states.
14. Verify health, readiness, and a non-publishing smoke pipeline.
15. Resume publication only through an explicit operator decision after validation.

Redis is not a backup of PostgreSQL truth. Reconciliation does not restore missing media
bytes or recover unknown provider IDs. AMBIGUOUS publication remains blocked, and known
external IDs are verified rather than republished. Credentials and secrets must not be
written to ordinary backups, logs, reasons, or reconciliation output. Restored
configuration and media integrity remain operator responsibilities. This procedure does
not by itself validate the environment's complete backup/restore readiness.

---

# 40. Smoke Test

After deployment/recovery:

```text
1. /health
2. /ready
3. PostgreSQL read/write test
4. Redis/event test
5. runtime/service-manager detection test
6. local AI health
7. ingest one test article
8. cluster story
9. extract claims
10. run research/evidence
11. build Fact Sheet
12. generate test content
13. run quality check
14. verify review gate
15. ensure real publication is not accidentally triggered
```

---

# 41. Runtime Adapter Failure

If runtime detection or service control fails:

```text
record detected signals
fail clearly
use explicit manual/unsupported mode if configured
require operator intervention
```

Never execute a guessed fallback command.

Application-level publication safety should remain controllable even if host service management is unavailable.

---

# 42. Runtime Adapter Testing

Before supporting a new service manager, verify:

```text
detection
status
start
stop
restart
enable
disable
permission failure
missing executable
command timeout
unexpected output
explicit override
unsupported mode
```

Ordinary application tests should use a fake service manager.

---

# 43. Final Operational Rules

## Upstream production owner

After PostgreSQL, Redis, and configured AI services are available, start
`news-pipeline` (or `python -m news_ai_pipeline`) using the application
environment/configuration. Empty active feeds leave it waiting normally.
Use `newsctl service status news-pipeline` and the existing service
start/stop/restart operations through a supported configured native adapter;
manual/unmanaged hosts run the executable explicitly. No privileged host
command is executed by the pipeline itself.

Check normalized startup/readiness and per-component failures alongside
durable progress and Redis pending/lag. An unavailable critical owner degrades
whole-system health. A fatal component error exits the process; this command
does not automatically restart services. SIGTERM/SIGINT performs bounded
shutdown. A new owner reclaims stale pending work under the configured idle
threshold; do not lower that threshold below healthy AI/search processing time.

Publishing pause does not pause upstream collection or quality. Generated
content without caller media remains `NOT_READY` with quality work deferred;
attach validated caller media through the existing authenticated boundary,
then ordinary reclaim can finish quality. Never manually mark events processed
to force progress. Lost Redis history still requires the explicit paused
`newsctl events reconcile` procedure, not an automatic runner action.

This software runtime does not certify physical-host cold boot, thermal
stability, Tailscale resilience, real Meta publication, or backup RPO/RTO.

```text
Use platform-neutral operator commands as the canonical surface.
Detect host capability before native service operations.
Do not place OS/service-manager commands in business code.
PostgreSQL remains authoritative.
Redis failure must not invent/erase durable state.
Unsafe publication takes priority over throughput.
Ambiguous publish outcome is not a blind-retry condition.
All external MVP publication requires explicit human approval.
Backups require restore testing.
Runtime detection failure must fail safely rather than guess.
```
