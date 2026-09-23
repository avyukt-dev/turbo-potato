# News AI Social Media Manager

# INFRASTRUCTURE_AND_DEPLOYMENT.md

**Status:** Canonical
**Document Role:** Source of truth for infrastructure, runtime portability, capability detection, deployment, service-management abstraction, storage, networking, backups, secrets, monitoring, and production operations.

Shared runtime/configuration contracts are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

Deploy and operate the application without making business/application code depend on one operating system, CPU architecture, init system, container runtime, or service manager.

Current physical target:

```text
Xiaomi POCO F1 / beryllium
postmarketOS
aarch64
headless
currently OpenRC
Tailscale-managed
```

These are runtime-profile characteristics, not application assumptions.

---

# 2. Infrastructure Principle

Start small:

```text
ONE HOST
ONE POSTGRESQL
ONE REDIS
ONE LOCAL AI SERVICE
ONE APPLICATION STACK
```

Do not require Kubernetes, Kafka, multi-region deployment, clustered databases, distributed object storage, GPU clusters, or a large vector database until measured workload justifies them.

---

# 3. Platform Independence

Application/domain/business code must not directly invoke host-specific service utilities.

Examples that may only appear inside runtime adapters/diagnostic documentation:

```text
rc-service
rc-update
systemctl
service
launchctl
Windows service commands
```

Canonical runtime boundary:

```text
Application / Operator
        ↓
newsctl / RuntimeController
        ↓
RuntimeDetector
        ↓
ServiceManager protocol
        ↓
capability-selected adapter
        ↓
host utility
```

Only the runtime/infrastructure adapter knows native host commands.

---

# 4. Capability Detection

Runtime detection should consider:

```text
platform.system()
platform.machine()
os.name
/etc/os-release where available
executable discovery such as shutil.which(...)
container/runtime metadata
service-manager usability checks
explicit operator override
```

Selection examples:

```text
OpenRC utilities found + usable → OpenRC adapter
systemd utilities found + usable → systemd adapter
SysV utility found + usable → SysV adapter
launchd found + usable → launchd adapter
Windows service capability usable → Windows adapter
none supported → explicit unmanaged/unsupported mode
```

Do not infer a service manager from the OS name alone.

Do not guess and execute a fallback command.

---

# 5. Runtime Abstraction

Conceptual interfaces:

```python
class RuntimeDetector(Protocol):
    def detect(self) -> RuntimeProfile: ...

class ServiceManager(Protocol):
    def status(self, service: str) -> ServiceStatus: ...
    def start(self, service: str) -> ServiceResult: ...
    def stop(self, service: str) -> ServiceResult: ...
    def restart(self, service: str) -> ServiceResult: ...
    def enable(self, service: str) -> ServiceResult: ...
    def disable(self, service: str) -> ServiceResult: ...
```

Business services never import native adapters directly.

---

# 6. Generic Operator Surface

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

Scripts/automation should target this normalized surface rather than native host commands.

---

# 7. Runtime Profiles

Hardware/OS-specific facts are represented as data, not code branches.

Recommended:

```text
infra/runtime/profiles/
├── poco-beryllium.yaml
├── generic-linux-arm64.yaml
└── future-profile.yaml
```

A profile may describe:

```text
profile name
architecture
OS metadata expectations
resource class
known capability hints
preferred adapter order
filesystem defaults/overrides
thermal/battery sensors where available
```

Profiles may provide hints but runtime capability detection still verifies actual utilities.

Do not create application code directories such as `infra/postmarketos/` or make one OS tree canonical.

---

# 8. Runtime/Infrastructure Layout

```text
infra/
├── postgres/
├── redis/
└── runtime/
    ├── adapters/
    │   ├── openrc/
    │   ├── systemd/
    │   ├── sysv/
    │   ├── launchd/
    │   └── windows/
    ├── templates/
    └── profiles/
```

Platform-neutral Python runtime code:

```text
packages/runtime/
├── detector.py
├── service_manager.py
├── profile.py
├── registry.py
└── errors.py
```

Native adapters are isolated infrastructure implementation details.

---

# 9. Current POCO Profile

Current expected profile:

```text
Device: Xiaomi POCO F1
Codename: beryllium
Architecture: aarch64
OS: postmarketOS
Mode: headless CLI
Current detected service manager: OpenRC
Remote management: Tailscale + SSH
```

OpenRC is the first runtime adapter to implement because it is required by the current host, not because the architecture depends on it.

---

# 10. Future Runtime Targets

The application should remain portable to:

```text
other Linux distributions
ARM Linux servers
x86 Linux servers
containers
macOS development hosts
Windows development hosts
cloud VMs
GPU inference nodes
```

Supporting a new host should primarily require configuration/profile/adapter work, not changes to domain/business logic.

---

# 11. Logical Services

Application services:

```text
news-api
news-collector
news-processor
news-ai-worker
news-research-worker
news-publisher
news-scheduler
news-media-worker
```

Infrastructure:

```text
PostgreSQL
Redis
llama.cpp
```

Not every logical service needs its own host process in the MVP.

The production upstream owner is **`news-pipeline`**, runnable as
`news-pipeline` or `python -m news_ai_pipeline`. It hosts concurrent bounded
tasks for collection, ordinary outbox dispatch, normalization, clustering,
claim extraction, research planning, evidence collection, fact checking,
story verification, Fact Sheet generation, content generation, and quality.
These retain their domain boundaries and canonical Redis consumer groups;
`news-collector`, `news-processor`, `news-ai-worker`, and `news-research-worker`
are logical stages, not separately expected host services in this deployment.
The API, scheduler, and publisher remain separate production processes.

`config/runtime/pipeline.yaml` owns typed, bounded wake/block/recovery,
component-error backoff, startup, and shutdown settings. Startup validates
source/runtime/AI routing configuration, checks PostgreSQL and Redis, and
ensures canonical consumer groups at ID `0` before reporting readiness.
It does not perform generative inference as a readiness test.
The owner shares one engine/session factory, Redis client, and AI router
across the existing production composition factories. Short domain
transactions and external AI/search work remain owned by those components.

Each worker serializes its own stale-pending reclaim and new-message reads;
different stages run concurrently. The default pending idle threshold is
15 minutes and must remain longer than healthy processing on the deployment
to avoid reclaiming legitimate in-flight work. Reclaim uses existing durable
idempotency/current-state checks. Redis-loss reconciliation remains an
explicit operator command, never automatic startup behavior.

Transient component errors have bounded normalized logging/backoff. Repeated
infrastructure failure or unexpected component termination exits the owner
nonzero rather than leaving a silently partial pipeline. SIGTERM/SIGINT stops
new ticks, permits bounded in-flight completion, then cancels remaining tasks
and closes owned clients and the engine. Shutdown never manufactures ACKs.
Restart preserves pending deliveries for ordinary reclaim.

`news-pipeline` is expected, manageable, and critical in the production
service registry: its absence stops upstream progress. Host service
installation remains deployment-owned behind the existing runtime adapters.
Publishing pause stops publication, not collection/research/content/quality.

---

# 12. Startup Dependencies

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

Only necessary host-level dependencies should be encoded in runtime adapter deployment material.

---

# 13. Networking

Preferred remote management:

```text
Tailscale
   ↓
SSH
```

Application services bind privately by default unless intentional public access is required.

Do not depend on a fixed LAN address.

---

# 14. Time

Accurate time is required for OAuth, TLS, scheduling, event timestamps, analytics, audit logs, and Tailscale.

The health/runtime layer detects available time-sync capability.

Canonical application/database timestamps remain UTC/`TIMESTAMPTZ` regardless of host timezone.

---

# 15. Storage

Recommended configurable default:

```text
/opt/news-ai/
├── app/
├── config/
├── models/
├── media/
├── logs/
├── backups/
└── runtime/
```

This is a profile/config default, not a cross-platform hard-coded path.

Other hosts may override it.

---

# 16. PostgreSQL

PostgreSQL is durable truth for stories, claims, evidence, Fact Sheets, reviews, jobs, content, publications, external IDs, and audit history.

Use versioned migrations.

Do not store database data files in the application repository.

---

# 17. Redis

Redis provides:

```text
Streams/event delivery
short-lived cache
coordination/locks where appropriate
```

Redis loss must not erase authoritative workflow state.

---

# 18. Local AI Service

Initial local inference service:

```text
llama.cpp
```

Run behind the AI provider abstraction.

Starting/stopping/checking the host process occurs through the runtime abstraction, not inside AI business logic.

---

# 19. POCO Resource Policy

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

Do not assume usable GPU/NPU acceleration.

If local inference destabilizes the host:

```text
reduce concurrency
reduce model size
reduce context length
delay optional work
route allowed work to cloud
```

---

# 20. Media Storage and Delivery

Persistence and public delivery are separate.

Initial profile may persist media locally.

Generated media selects a typed backend through
`NEWS_AI_GENERATED_MEDIA_STORAGE_BACKEND=local|s3`. The local backend writes content-addressed
objects beneath the configured directory; a static server and stable HTTPS ingress (for example a
named Cloudflare Tunnel) must expose the configured public base. The S3 backend uses the portable
`PutObject`/`HeadObject` subset for AWS S3, Cloudflare R2, or another explicitly configured
S3-compatible endpoint. AWS may use the normal SDK credential chain. R2 uses region `auto`, its
account endpoint, and bucket-scoped credentials. Secrets remain environment/deployment owned.

Objects use immutable content-addressed keys, `image/jpeg`, long-lived immutable cache headers,
transport MD5, and application SHA-256 metadata. An existing key is reusable only when a verified
HEAD response matches the expected size, MIME type, and SHA-256. The persisted public URL is a
stable HTTPS/CDN/custom-domain URL, not an expiring presigned URL. Local disk, S3 API endpoints,
and credentials are never exposed as publication URLs.

If a social platform requires a fetchable URL:

```text
selected asset
   ↓
deliberate media-delivery layer/object storage
   ↓
HTTPS URL
   ↓
platform API
```

Never expose arbitrary filesystem paths, database/admin ports, backups, or credentials.

---

# 21. Configuration Layout

```text
config/
├── sources/
│   ├── registry.yaml
│   ├── feeds.yaml
│   └── collection.yaml
├── research/
│   ├── source-policy.yaml
│   ├── search-policy.yaml
│   ├── corroboration.yaml
│   ├── fact-check.yaml
│   └── historical-research.yaml
├── editorial/
│   ├── priorities.yaml
│   ├── taxonomy.yaml
│   ├── content-style.yaml
│   ├── risk-policy.yaml
│   └── publishing-policy.yaml
├── models/
├── prompts/
└── platforms/
```

Secrets follow a separate secure path.

---

# 22. Configuration Precedence

```text
code defaults
  ↓
versioned config
  ↓
environment-specific overrides
  ↓
secure secret references
  ↓
audited runtime override where explicitly allowed
```

Reject ambiguous duplicate policy definitions where practical.

---

# 23. Secrets

Never store secrets in Git, ordinary plaintext DB fields, prompts, Redis events, logs, or frontend state.

Use environment secrets, encrypted credential storage, or a secret manager according to deployment maturity.

For optional Telegram human approval, deployment supplies
`NEWS_AI_TELEGRAM_BOT_TOKEN`, `NEWS_AI_TELEGRAM_WEBHOOK_SECRET`,
`NEWS_AI_TELEGRAM_REVIEW_CHAT_ID`, and `NEWS_AI_TELEGRAM_REVIEWER_USER_ID`, then explicitly sets
`NEWS_AI_TELEGRAM_REVIEW_ENABLED=true`. The webhook URL is
`https://<api-host>/api/v1/integrations/telegram/review/webhook`. Configure Telegram `setWebhook`
with that HTTPS URL, `allowed_updates=["message","callback_query"]`, and the same secret token.
The API validates Telegram's `X-Telegram-Bot-Api-Secret-Token` header on every update. Bot and
webhook secrets remain environment/credential-store owned and must not appear in YAML, logs,
metrics, audit metadata, or callback data.

---

# 24. Deployment Modes

Conceptual environments:

```text
DEVELOPMENT
STAGING
PRODUCTION
```

AI/social execution modes may include:

```text
MOCK
LOCAL
CLOUD
HYBRID
SANDBOX where supported
LIVE
```

Production credentials never flow automatically into development/test.

---

# 25. Native vs Containers

Containers are optional.

Current POCO deployment favors native services through runtime adapters because of resource constraints/current environment.

Development/future hosts may use containers when useful.

Application code must not depend on container presence.

---

# 26. Deployment Process

```text
approved code
  ↓
tests
  ↓
dependency/build preparation
  ↓
migration validation
  ↓
backup when warranted
  ↓
deploy
  ↓
restart affected services through RuntimeController
  ↓
health/readiness
  ↓
smoke tests
  ↓
resume normal work/publication
```

Zero downtime is not an MVP requirement on the single host.

---

# 27. Migrations

Use versioned migrations such as Alembic.

Do not manually modify production schema as the normal method.

Test migrations on representative data and avoid blind rollback when data compatibility is uncertain.

---

# 28. Backup and Restore

Back up:

```text
PostgreSQL
versioned configuration
required media/assets according to retention policy
runtime/deployment configuration
```

Do not place secrets into unsecured backups.

A backup is not verified until restore has been tested.

Recovery sequence should pause publication, restore durable state, restore event/runtime services, run health/smoke checks, then re-enable publication.

If Redis streams or consumer groups were lost while PostgreSQL survived, a PUBLISHED
outbox row is not proof that Redis still retains the work. Use the bounded `newsctl
events reconcile` dry-run/apply procedure in `OPERATIONS_RUNBOOK.md`; do not mass-reset
PUBLISHED rows to PENDING. Reconciliation uses durable consumer and domain state and
must run with effective publication pause enabled.

---

# 29. Observability

Whole-system health should cover:

```text
SYSTEM: CPU, RAM, disk, temperature, uptime
NETWORK: connectivity, DNS, Tailscale
SERVICES: PostgreSQL, Redis, API, workers, scheduler, publisher
AI: model health, latency, queue depth, failures
JOBS: pending, running, failed, retrying
SOCIAL: adapter/account health, rate limits, publication errors
```

Service checks use runtime abstractions, not hard-coded native commands.

---

# 30. Runtime Adapter Tests

Every supported adapter requires contract tests for:

```text
detect
status
start
stop
restart
enable
disable
missing utility
permission denied
unsupported manager
command timeout
unexpected output
```

Application tests should use a fake `ServiceManager`.

---

# 31. Failure Isolation

```text
social platform outage → collection/research may continue
local AI outage → evidence state remains intact
Redis outage → PostgreSQL durable state remains intact
runtime detection failure → no guessed host command
```

Fail safe rather than invent state or execute unsafe fallbacks.

---

# 32. Publication Kill Switch

Publication pause is an application-level control, not merely an OS service stop.

When active:

```text
collection may continue
research may continue
content may continue
review may continue
new external publication calls stop
```

The production control is a PostgreSQL `runtime_controls` row updated through
`newsctl publish pause|resume`. Its value is combined with the
`NEWS_AI_PUBLISHING_PAUSED=true` deployment hard pause; a database resume cannot
override that environment pause. Scheduler and publisher re-read the shared
control, and inability to read it denies external publishing.

`newsctl runtime detect`, service operations, and health reporting use the typed
service registry and `RuntimeController`. Native commands remain inside runtime
adapters, and runtime profiles are ordering hints rather than capability proof;
omitted native managers remain detection candidates and manual mode is only a
fallback unless explicitly selected by an operator. The production registry marks
the runnable API, scheduler, and publisher processes as expected. API unavailability
is critical; scheduler or publisher unavailability degrades health while durable
publication work remains recoverable.

Prometheus-compatible `/metrics` is read-only aggregate telemetry. Metric labels
exclude durable IDs, account identifiers, content, URLs, credentials, and raw
provider errors.

---

# 33. Security Boundaries

Do not expose by default:

```text
PostgreSQL
Redis
llama.cpp
runtime/service-manager control
admin-only API endpoints
backup storage
internal media filesystem
```

Use least privilege and private management paths.

---

# 34. Final Infrastructure Rules

```text
Application/domain code is platform independent.
CPU/OS/service-manager capabilities are detected at runtime.
Host utilities are isolated behind adapters.
Current POCO/postmarketOS/OpenRC details live in a runtime profile, not OS-named application code.
Docker/containers are optional.
PostgreSQL is durable truth.
Redis is transport/coordination.
Secrets never enter Git/prompts/events/logs.
Media persistence and public delivery are separate.
Configuration has one owner per concern.
Backups must be restore-tested.
Publishing can be paused independently of the host service manager.
Scale only when measured workload requires it.
```
