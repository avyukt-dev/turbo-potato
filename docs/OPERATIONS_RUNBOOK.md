# News AI Social Media Manager

# OPERATIONS_RUNBOOK.md

**Status:** Canonical
**Document Role:** Source of truth for day-to-day operation, health checks, incident triage, deployment execution, backup/recovery, publication safety, and POCO server procedures.

Infrastructure architecture remains owned by `INFRASTRUCTURE_AND_DEPLOYMENT.md`.

Shared lifecycle semantics are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This runbook defines how to operate the initial News AI deployment safely and predictably.

The initial runtime philosophy is:

```text
ONE POCO
ONE POSTGRESQL
ONE REDIS
ONE LOCAL AI SERVICE
ONE APPLICATION STACK
CONTROLLED CLOUD PROVIDERS
```

Native/OpenRC-managed services are the default on the POCO. Containers are optional where useful and are not an MVP requirement.

---

# 2. Initial Server Profile

Current target:

```text
Device: Xiaomi POCO F1
Codename: beryllium
Architecture: aarch64
OS: postmarketOS
Init: OpenRC
Mode: headless CLI
Timezone: Asia/Kolkata
Remote administration: Tailscale + SSH
```

Hardware values such as RAM, storage, kernel version, addresses, and battery readings are deployment characteristics and must not become hard-coded application assumptions.

---

# 3. Operational Priorities

When something fails, prioritize:

```text
1. prevent unsafe/duplicate publication
2. protect PostgreSQL data
3. preserve logs/audit evidence
4. restore network/time/dependencies
5. restore processing
6. re-enable publication only after validation
```

---

# 4. Global Publishing Kill Switch

The system must support a global publication pause.

When active:

```text
collection may continue
research may continue
AI analysis may continue
content generation may continue
review may continue
new external publication calls stop
```

Use the kill switch during:

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

The kill switch must be auditable.

---

# 5. Server Health Command

The existing operator command is:

```bash
health
```

It should evolve toward whole-server coverage:

```text
SYSTEM
├── CPU
├── RAM
├── disk
├── temperature
└── uptime

NETWORK
├── Wi-Fi
├── Internet
└── Tailscale

SERVICES
├── PostgreSQL
├── Redis
├── API
├── Collector
├── Processor
├── AI Worker
├── Publisher
└── Scheduler

AI
├── Local model
├── inference latency
├── queue depth
└── failures

JOBS
├── pending
├── running
├── failed
└── retrying

SOCIAL
├── Instagram
├── X
└── other configured adapters
```

The health command must not expose secrets.

---

# 6. Basic Host Checks

Check uptime/kernel:

```bash
uname -a
uptime
```

Check memory:

```bash
free -h
```

Check filesystem:

```bash
df -h
```

Check processes:

```bash
ps aux
```

Use deployment-appropriate thermal/battery sysfs paths rather than assuming one path across future hardware.

---

# 7. OpenRC Service Checks

List services:

```bash
rc-status
```

Check one service:

```bash
rc-service <service> status
```

Start:

```bash
sudo rc-service <service> start
```

Stop:

```bash
sudo rc-service <service> stop
```

Restart:

```bash
sudo rc-service <service> restart
```

Enable at boot:

```bash
sudo rc-update add <service> default
```

Actual application service names are implementation/deployment configuration and should be documented when created.

---

# 8. Startup Dependency Order

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
API
  ↓
Collector / Processor / Workers
  ↓
Scheduler
  ↓
Publisher eligibility
```

Only enforce necessary dependencies in OpenRC; avoid artificial coupling between independent workers.

---

# 9. Time Synchronization

Accurate time is mandatory for:

```text
OAuth/token validation
scheduled publication
event timestamps
audit logs
TLS/API interactions
analytics
Tailscale
```

Check chrony:

```bash
chronyc tracking
chronyc sources -v
```

If system time is wrong, repair time synchronization before debugging authentication/token failures.

Canonical database timestamps use UTC/TIMESTAMPTZ even though the server/operator timezone is Asia/Kolkata.

---

# 10. Network Checks

Interfaces:

```bash
ip addr
```

Routing:

```bash
ip route
```

DNS resolver state:

```bash
cat /etc/resolv.conf
```

Internet reachability:

```bash
ping -c 3 1.1.1.1
```

DNS resolution:

```bash
getent hosts example.com
```

Successful Wi-Fi association is not proof that DNS/internet connectivity is healthy.

---

# 11. Tailscale

Check:

```bash
tailscale status
tailscale ip
```

Check daemon:

```bash
sudo rc-service tailscale status
```

Restart if needed:

```bash
sudo rc-service tailscale restart
```

Do not place Tailscale authentication keys or reusable enrollment credentials in docs/logs.

---

# 12. SSH

Prefer Tailscale/private management access rather than exposing SSH directly to the public Internet.

If SSH fails, check:

```text
network
Tailscale
sshd OpenRC state
firewall
host key/configuration
user permissions
```

---

# 13. PostgreSQL Health

PostgreSQL is the durable source of truth.

Check service using the deployed OpenRC service name.

Basic query:

```bash
psql -c "SELECT now();"
```

Application readiness should verify the configured application database connection, not only that a local process exists.

If PostgreSQL is unavailable:

```text
external publication must stop
business-state mutation must stop safely
workers must not invent state from Redis
```

---

# 14. Redis Health

Redis is event/work transport and short-lived coordination, not durable business truth.

Check:

```bash
redis-cli ping
```

Expected:

```text
PONG
```

Inspect queue/stream health with `SCAN`/stream commands appropriate to the implementation.

Avoid production-wide `KEYS *` once data volume grows.

---

# 15. Redis Failure

If Redis is unavailable:

```text
PostgreSQL durable state remains authoritative
new async delivery pauses/fails safely
outbox records remain pending
workers should not lose durable job/result state
external publication should not bypass normal scheduling
```

After Redis recovery, restore/verify consumer groups and outbox delivery before resuming normal throughput.

---

# 16. Event Queue Health

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

# 17. Stuck Pending Events

For stale pending entries:

```text
identify consumer/idle time
load current PostgreSQL state
claim/retry only when safe
preserve idempotency
record failures
```

Never replay publication side effects blindly.

---

# 18. Dead-Letter Handling

Dead-letter work should remain inspectable.

Operational steps:

```text
inspect original event/job
inspect current PostgreSQL state
classify root cause
fix dependency/data/policy issue
requeue only when transition remains valid
```

Do not mass-replay dead-letter publication events without individual safety checks.

---

# 19. API Health

Check:

```text
GET /health
GET /ready
```

`/health` confirms process liveness.

`/ready` confirms required dependencies for current API operation.

A healthy API process with an unhealthy database is not production-ready.

---

# 20. Collector Health

Check:

```text
last successful poll
sources degraded/disabled
articles discovered rate
HTTP/DNS failures
feed parse failures
queue depth
```

One broken source must not stop all collection.

---

# 21. Processor Health

Check:

```text
normalization throughput
deduplication failures
story clustering latency
entity/classification failures
article queue lag
```

Stale processors should not be allowed to act on invalid historical state without reloading PostgreSQL.

---

# 22. AI Service Health

Local AI health should cover:

```text
process alive
model loaded
inference request succeeds
recent latency
tokens/sec where measurable
queue depth
failure rate
RAM/CPU/thermal behavior
```

The local model is a processing component, never the factual source of truth.

---

# 23. AI Failure

If local AI fails:

```text
retry boundedly
use allowed router fallback where policy permits
otherwise queue/fail job safely
```

If a cloud provider fails:

```text
bounded retry
allowed provider fallback
preserve job/provenance
```

Do not route sensitive material to arbitrary providers outside configured policy.

---

# 24. POCO Resource Policy

Monitor:

```text
RAM
CPU
storage
battery
battery temperature
CPU/thermal sensors where available
inference latency
queue depth
```

The POCO must not be assumed to have usable NPU/GPU acceleration until measured.

If sustained local inference destabilizes the server:

```text
reduce concurrency
reduce model size
reduce context length
slow background work
route allowed work to cloud
pause optional jobs
```

Stability takes priority over maximizing local inference throughput.

---

# 25. Battery and Thermal Operation

The POCO is a mobile device used as a server.

Track:

```text
capacity
charging state
temperature
voltage/current where exposed
thermal throttling
```

Thresholds should be configuration-driven.

When unsafe/high temperatures occur:

```text
pause expensive optional work
reduce AI concurrency
preserve core API/database functions where safe
```

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

Low disk space can corrupt normal operation before the filesystem reaches 100%.

Temporary media and logs require cleanup/rotation policies.

Never delete provenance/evidence/publication history merely to free space without an explicit retention decision.

---

# 27. Media Storage

Initial persistence may use local storage such as:

```text
/opt/news-ai/media/
```

Local persistence does not imply public accessibility.

When a platform requires a fetchable URL, provide the selected asset through the deliberately configured HTTPS media-delivery mechanism/object storage defined by infrastructure/publishing policy.

Do not expose arbitrary filesystem paths, the admin API, database, backups, or internal services to satisfy media ingestion.

---

# 28. Public Media Delivery Failure

If a social platform cannot fetch media:

```text
verify asset exists
verify public URL is intended/current
verify HTTPS/DNS
verify MIME type
verify expiration policy
verify platform reachability
```

Do not weaken network security by exposing unrelated internal services.

---

# 29. Publisher Health

Monitor:

```text
publishing queue depth
running attempts
stuck PUBLISHING state
retrying attempts
platform auth failures
rate-limit events
media failures
duplicate-prevention events
```

Publisher health should be separate per platform/account where useful.

---

# 30. MVP Approval Check

Before any external publication:

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

For the MVP, every external publication requires explicit human approval.

---

# 31. Ambiguous Publication Outcome

If a platform call times out after transmission:

```text
DO NOT blindly retry
```

Instead:

```text
record ambiguous attempt
query/verify external state where possible
if published → persist external ID and mark PUBLISHED
if definitely not published → retry according to policy
if uncertain → BLOCK and require human intervention
```

Duplicate avoidance is more important than fast retry.

---

# 32. Platform Authentication Failure

On authentication/permission failure:

```text
stop automatic retries when non-transient
mark account AUTH_ERROR / REAUTH_REQUIRED as appropriate
pause affected publication jobs
preserve other platform processing
reauthenticate securely
verify account capability
resume
```

Do not log tokens.

---

# 33. Rate Limiting

When a platform or provider rate-limits:

```text
respect retry-after/reset when supplied
otherwise use bounded backoff
preserve queued work
do not hammer provider
```

Rate-limit state should be visible operationally.

---

# 34. Logs

Structured logs should include:

```text
timestamp
level
service
event/action
request_id
job_id where relevant
story_id where relevant
publication_id where relevant
error_code
duration
```

Never log:

```text
passwords
API keys
OAuth tokens
refresh tokens
cookies
private keys
authorization headers
unnecessary personal data
```

---

# 35. Incident Triage Order

Use this order:

```text
1. Is unsafe publication occurring?
2. Is the host alive?
3. Is network available?
4. Is system time correct?
5. Is PostgreSQL healthy?
6. Is Redis healthy?
7. Is the API healthy?
8. Are workers consuming?
9. Is local/cloud AI healthy?
10. Are media delivery and external platform APIs healthy?
```

This prevents debugging application symptoms before lower-level causes.

---

# 36. Incident Severity

Suggested operational severity:

```text
SEV-1: unsafe/duplicate mass publication, credential compromise, data corruption
SEV-2: publication unavailable, database/major pipeline outage
SEV-3: one worker/provider/platform degraded with safe fallback/queueing
SEV-4: minor source or non-critical background failure
```

Operational severity is not the same concept as editorial `RiskLevel`.

---

# 37. SEV-1 Immediate Actions

```text
activate global publication kill switch
preserve logs/state
disable compromised credentials/accounts if applicable
stop affected workers if needed
protect PostgreSQL
identify scope
avoid destructive cleanup
```

Resume publication only after root cause and safety checks.

---

# 38. Deployment Flow

Canonical deployment sequence:

```text
approved code
   ↓
run tests
   ↓
dependency/install step
   ↓
migration check
   ↓
backup where migration warrants
   ↓
apply migration
   ↓
deploy code/config
   ↓
restart affected services
   ↓
health/readiness
   ↓
smoke test
   ↓
resume normal processing/publication
```

Do not deploy an untested documentation-to-implementation semantic change silently.

---

# 39. Database Migrations

Every schema change requires a versioned migration.

Before a production migration:

```text
review migration
backup as appropriate
verify backup strategy
test migration on representative data
apply
run integrity/readiness checks
```

Do not perform routine manual production-table edits outside migration history.

---

# 40. Backups

Back up at minimum:

```text
PostgreSQL
versioned configuration
important media needed for publication/audit
operational deployment metadata needed for recovery
```

Secrets require a separate secure backup/recovery approach and must not be committed to Git.

---

# 41. Backup Verification

A backup is not considered operationally verified until restore has been tested.

Periodically:

```text
create backup
restore into isolated test database
run integrity checks
verify critical record counts/relationships
```

---

# 42. Recovery Order

General recovery:

```text
1. pause publishing
2. protect/preserve current data and logs
3. restore network/time
4. restore/verify PostgreSQL
5. restore/verify Redis
6. restore API/workers
7. restore AI/media services
8. verify external platform credentials/capabilities
9. run smoke tests
10. resume processing
11. re-enable publication last
```

---

# 43. Redis Loss Recovery

Redis loss must not imply loss of durable business state.

Recovery should use PostgreSQL jobs/outbox/current state to rebuild pending work where necessary.

Do not reconstruct publication truth from assumptions about missing Redis messages.

---

# 44. PostgreSQL Restore

After database restore:

```text
verify migrations/schema version
verify FK/integrity checks
verify recent publication states
verify audit records
verify pending jobs/outbox
verify external publication IDs for recent attempts
```

A restored database snapshot may be older than external platform state; ambiguous recent publications require reconciliation before retry.

---

# 45. Credential Rotation

Canonical rotation:

```text
obtain replacement credential securely
validate new credential
update secret mechanism/reference
restart/reload affected service if necessary
run controlled access check
revoke old credential
```

Never place credentials in Git, docs, prompts, ordinary database fields, or logs.

---

# 46. Configuration Changes

Versioned configuration changes should be reviewed and auditable.

Shared semantic changes require synchronized documentation updates according to `CANONICAL_CONTRACTS.md`.

Do not introduce an undocumented runtime override that creates a second conflicting policy source.

---

# 47. Smoke Test After Deployment

Recommended non-publishing smoke test:

```text
1. /health
2. /ready
3. PostgreSQL query
4. Redis ping/stream check
5. local AI health
6. ingest one controlled test article
7. normalize/cluster
8. extract claims
9. request research
10. collect/evaluate evidence
11. generate Fact Sheet
12. generate test content
13. run quality check
14. verify READY_FOR_REVIEW
15. verify no external publication occurs without explicit human approval
```

Use mock/sandbox social mode unless a deliberate live publication test is authorized.

---

# 48. Release Gate

Before normal production operation:

```text
unit tests
integration tests
contract tests
schema/event tests
AI regression tests
evidence tests
editorial/sensitive-topic tests
social idempotency tests
security checks
migration checks
health/readiness
smoke test
```

`TESTING_AND_EVALUATION.md` owns detailed gates.

---

# 49. Rollback Triggers

Consider rollback/pause for:

```text
factual drift
unexpected claim-status behavior
FactCheckLabel confusion
duplicate publication
broken approval gate
broken evidence linkage
database migration failure
worker retry loop
severe performance/thermal regression
social adapter regression
```

---

# 50. Rollback Procedure

```text
activate publication kill switch
stop affected workers if necessary
preserve logs/state
identify last known-good application/config/model/prompt
rollback application/config where safe
handle database compatibility deliberately
restart
run health/readiness
run smoke test
re-enable processing
re-enable publication last
```

Do not blindly reverse a database migration when data compatibility is unclear.

---

# 51. AI Model/Prompt Rollback

Models and prompts are separately versioned.

If regression occurs:

```text
disable bad model/prompt
route to previous approved version
run regression checks
preserve affected ai_run provenance
```

No model change may bypass evidence or human-approval policy.

---

# 52. Research Failure

If research cannot reach adequate evidence:

```text
preserve UNVERIFIED/DISPUTED state as appropriate
record unresolved questions
surface for human review
```

Do not publish certainty merely because the retry/time budget ended.

---

# 53. Breaking News Operations

For rapidly changing stories:

```text
increase collection/research priority
refresh evidence more frequently
version Fact Sheets
invalidate stale approval when material facts change
require human approval before every MVP external publication
```

Speed does not lower factual standards.

---

# 54. Approval Invalidation

Human approval applies to an exact artifact/version.

If material facts/content change:

```text
approval becomes invalid
artifact returns to review
```

Do not reuse a stale approval for materially changed content.

---

# 55. Correction Operations

For factual correction:

```text
research correction
create new Fact Sheet version
create corrected content version
human review
publish correction/update according to platform capability
retain original publication/audit trail
```

Do not rewrite history by deleting the original evidence trail from the database.

---

# 56. Operational States

For whole-system operational state, use descriptive states such as:

```text
HEALTHY
DEGRADED
PAUSED
FAILED
MAINTENANCE
```

These are operational health labels and must not replace domain `RiskLevel`, `ReviewState`, or `PublicationStatus`.

---

# 57. Routine Daily Checks

A lightweight daily check should include:

```text
health command
PostgreSQL status/free space
Redis queue/dead-letter health
AI latency/failures
source failure count
pending review queue
scheduled publications
publisher/account auth status
backup recency
thermal/battery anomalies
```

---

# 58. Routine Weekly Checks

Recommended:

```text
review failed/dead-letter jobs
review source degradation
review disk/log growth
verify backup creation
inspect publication failure patterns
inspect AI regression/latency trends
review credential expiration warnings
```

---

# 59. Periodic Recovery Test

Periodically test:

```text
PostgreSQL restore
Redis/work rebuild from durable state
application restart after host reboot
kill switch
mock publication idempotency
credential rotation procedure
```

Recovery behavior is part of system correctness.

---

# 60. Final Operational Rules

```text
PostgreSQL is durable truth.
Redis can be rebuilt; business state must survive.
Unsafe publication is stopped before troubleshooting continues.
All external MVP publication requires explicit human approval.
Ambiguous publication outcomes are verified before retry.
Backups must be restore-tested.
Secrets never enter Git/logs/prompts.
POCO thermal/resource stability takes priority over local AI throughput.
Breaking news gets priority, not relaxed standards.
AI failure must not become factual invention.
Publication is re-enabled last after incidents/recovery.
```

---

# 61. Documentation Relationship

This document owns operational procedures.

`INFRASTRUCTURE_AND_DEPLOYMENT.md` owns infrastructure design.

`CANONICAL_CONTRACTS.md` owns shared lifecycle semantics.

`EVENTS.md` owns event delivery/retry contracts.

`DATA_MODEL.md` owns persistence.

`AI_PLATFORM.md` owns AI routing/model policy.

`SOURCE_AND_RESEARCH.md` owns research methodology.

`SOCIAL_PUBLISHING.md` owns social-platform execution semantics.

`TESTING_AND_EVALUATION.md` owns detailed release/evaluation criteria.
