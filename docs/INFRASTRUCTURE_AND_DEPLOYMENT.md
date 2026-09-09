# News AI Social Media Manager

# INFRASTRUCTURE_AND_DEPLOYMENT.md

**Status:** Canonical
**Document Role:** Source of truth for infrastructure, runtime portability, deployment, service management abstraction, storage, networking, backups, secrets, monitoring, and production operations.

Shared runtime/configuration contracts are defined by `CANONICAL_CONTRACTS.md`.

---

# 1. Purpose

This document defines how the News AI Social Media Manager is deployed and operated without making business/application code dependent on a specific operating system, init system, container runtime, or service manager.

The initial physical target is:

```text
Xiaomi POCO F1 / beryllium
postmarketOS
aarch64
headless
currently OpenRC
Tailscale-managed
```

Those are deployment characteristics, not application assumptions.

---

# 2. Infrastructure Principle

Start small and remain portable.

Initial target:

```text
ONE HOST
ONE POSTGRESQL
ONE REDIS
ONE LOCAL AI SERVICE
ONE APPLICATION STACK
```

Do not require:

```text
Kubernetes
Kafka
multi-region deployment
multi-node PostgreSQL
multi-node Redis
distributed object storage
GPU cluster
large vector database
```

until measured workload justifies them.

---

# 3. Platform Independence

Application/domain/business logic must not directly invoke system-specific utilities.

Prohibited in application code:

```text
rc-service
rc-update
systemctl
service
launchctl
sc.exe
PowerShell service-management commands
```

The runtime boundary is:

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

Only the infrastructure/runtime adapter knows the actual host utility.

---

# 4. Capability-Based Runtime Detection

Detection should prefer host capabilities over hard-coded OS assumptions.

Useful signals:

```text
Python platform.system()/os.name
/etc/os-release where available
executable discovery such as shutil.which(...)
container/runtime metadata
process/service-manager environment
explicit operator override
```

Conceptual selection:

```text
rc-service + rc-update found
    → OpenRC adapter

systemctl found and usable
    → systemd adapter

service/init scripts found
    → SysV adapter

launchctl found
    → launchd adapter

Windows service capability found
    → Windows adapter

none found
    → explicit unmanaged/unsupported mode
```

Detection must verify the utility is usable rather than infer purely from filename or OS family.

Never guess a command and execute it.

---

# 5. Runtime Abstraction

Conceptual Python interfaces:

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

Implementation adapters may be platform-specific. Their use is isolated to the runtime package/infrastructure layer.

Business services never import those adapters directly.

---

# 6. Generic Operator Surface

The intended operator surface is platform-neutral:

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

The exact CLI implementation can evolve, but scripts and automation should target this abstraction rather than native utilities directly.

---

# 7. Current POCO Runtime Profile

Current target profile:

```text
Device: Xiaomi POCO F1
Codename: beryllium
Architecture: aarch64
OS: postmarketOS
Mode: headless CLI
Service manager currently detected: OpenRC
Remote administration: Tailscale + SSH
```

OpenRC is therefore the initial adapter to implement and test.

It is not the architectural default for all future systems.

---

# 8. Future Runtime Profiles

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

Adding support for another runtime should primarily mean adding/testing a runtime adapter, not rewriting application services.

---

# 9. Repository Infrastructure Layout

Recommended:

```text
infra/
├── postgres/
├── redis/
├── runtime/
│   ├── adapters/
│   │   ├── openrc/
│   │   ├── systemd/
│   │   ├── sysv/
│   │   ├── launchd/
│   │   └── windows/
│   └── templates/
└── postmarketos/
```

And application-neutral runtime logic:

```text
packages/runtime/
├── detector.py
├── service_manager.py
├── profile.py
├── errors.py
└── registry.py
```

Platform-specific deployment material may exist under `infra/runtime/adapters/`, but application/domain packages must remain platform-independent.

---

# 10. Initial Services

Logical application services:

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

Infrastructure services:

```text
PostgreSQL
Redis
llama.cpp
```

Not every logical component must be its own OS process during the MVP.

---

# 11. Startup Dependencies

Logical dependency order:

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

Runtime adapters should enforce only necessary host-level dependencies.

---

# 12. Networking

Remote management:

```text
Tailscale
   ↓
SSH
```

Prefer private/Tailscale administration to direct public SSH exposure.

Application services should bind privately by default unless a public endpoint is intentionally required.

The application must not depend on a fixed LAN IP.

---

# 13. Time Synchronization

Accurate time is mandatory for:

```text
OAuth/token validation
TLS
scheduled publication
event timestamps
audit logs
analytics
Tailscale
```

Time synchronization is a host capability.

The runtime/health layer may detect the available time service/tool, but application data continues to use UTC/PostgreSQL `TIMESTAMPTZ` regardless of host timezone.

Current operator timezone may be `Asia/Kolkata`.

---

# 14. Storage Layout

Recommended configurable layout:

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

Paths are configuration values, not hard-coded application constants.

Future platforms may use different paths.

---

# 15. PostgreSQL

PostgreSQL is the durable source of truth.

It owns persistent state for stories, claims, evidence, fact checks, Fact Sheets, content, reviews, jobs, publications, external IDs, and audit history.

Do not place database data files inside the application repository.

Use versioned migrations.

---

# 16. Redis

Redis is used for:

```text
Redis Streams / event delivery
short-lived cache
coordination/locks where appropriate
```

Redis is not durable business truth.

Redis loss must not erase the authoritative workflow state stored in PostgreSQL.

---

# 17. Local AI Service

Initial local runtime:

```text
llama.cpp
```

Run it as an isolated service/process behind the AI adapter.

Application workers should communicate through a configured local endpoint rather than embedding backend-specific inference logic everywhere.

---

# 18. POCO Resource Policy

The POCO is resource constrained.

Monitor:

```text
RAM
CPU
storage
temperature
battery/power
inference latency
queue depth
```

Do not assume usable GPU/NPU acceleration.

Benchmark actual runtime support.

If local AI destabilizes the host:

```text
reduce concurrency
reduce model size
reduce context length
delay optional work
route allowed tasks to cloud
```

---

# 19. Media Storage and Public Delivery

Media persistence and public delivery are separate.

Initial persistence may use:

```text
/opt/news-ai/media/
```

If a social platform requires a fetchable URL:

```text
selected asset
   ↓
deliberate media delivery layer/object storage
   ↓
HTTPS URL
   ↓
platform API
```

Never expose arbitrary filesystem paths, database ports, admin APIs, backups, or credentials to make media fetchable.

---

# 20. Configuration Layout

Canonical configuration roots:

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

No policy key should have two configuration owners.

---

# 21. Configuration Precedence

Recommended:

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

Startup validation should reject conflicting/ambiguous definitions where practical.

---

# 22. Secrets

Never store secrets in:

```text
Git
ordinary plaintext database fields
AI prompts
Redis events
logs
frontend state
```

Use environment secrets, encrypted credential storage, or a secret manager according to deployment maturity.

Log credential references/IDs only when necessary.

---

# 23. Deployment Modes

Supported conceptual modes:

```text
DEVELOPMENT
STAGING
PRODUCTION
```

AI/social modes may additionally be:

```text
MOCK
LOCAL
CLOUD
HYBRID
SANDBOX where supported
LIVE
```

Production credentials must never be inherited automatically by development/test environments.

---

# 24. Native vs Container Deployment

Containers are optional.

On the POCO, native services selected through the runtime abstraction are the default initial approach because of resource constraints and the current host environment.

Development or future deployment may use containers where they improve reproducibility.

Application code must not depend on whether it runs inside a container.

---

# 25. Deployment Process

Canonical sequence:

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
health/readiness checks
  ↓
smoke tests
  ↓
resume normal work/publication
```

Zero-downtime deployment is not an MVP requirement on the single POCO.

Safe, observable restart is preferred over unnecessary complexity.

---

# 26. Database Migrations

All schema changes use versioned migrations such as Alembic.

Do not manually edit production tables as the normal deployment method.

Migration process:

```text
backup when appropriate
verify migration against representative data
apply
validate schema/invariants
start application
smoke test
```

Do not blindly roll migrations backward if data compatibility is uncertain.

---

# 27. Backups

Back up at minimum:

```text
PostgreSQL
versioned configuration
required media metadata/assets according to retention policy
deployment/runtime configuration
```

Secrets must not be copied into unsecured backups.

A backup is not considered operationally verified until restore has been tested.

---

# 28. Restore

Recovery should be documented and tested independently of backup creation.

Typical order:

```text
pause publication
restore PostgreSQL
restore required configuration/assets
validate schema
restore event delivery/runtime
run health checks
run smoke pipeline
re-enable publication only after review
```

---

# 29. Observability

Whole-system health should expose:

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
├── model health
├── latency
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
└── publication failures
```

The implementation should query services through abstractions rather than hard-coded OS commands.

---

# 30. Runtime/Platform Tests

Every supported runtime adapter requires contract tests for:

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

Detection tests must include conflicting signals and explicit override behavior.

Application tests should run against a fake `ServiceManager` so ordinary business tests require no host-specific manager.

---

# 31. Failure Isolation

A platform/social provider outage must not stop:

```text
collection
research
fact checking
content preparation
```

A local AI outage must not corrupt evidence state.

A Redis outage must not erase durable PostgreSQL state.

A service-manager detection failure must not cause the application to execute an arbitrary fallback command.

---

# 32. Publication Kill Switch

The system must support a global publication pause independent of service-manager implementation.

When active:

```text
collection may continue
research may continue
content may continue
review may continue
new external publication calls stop
```

The control belongs at the application/publishing-policy level, not solely to stopping an OS process.

---

# 33. Security Boundaries

Do not publicly expose by default:

```text
PostgreSQL
Redis
llama.cpp
admin-only API endpoints
runtime/service-manager control
backup storage
internal media filesystem
```

Use least privilege and private management paths.

---

# 34. Final Infrastructure Rules

```text
Application/domain code is platform independent.
Host utilities are isolated behind capability-detected runtime adapters.
The current POCO/OpenRC profile is not a universal assumption.
Docker is optional, not required.
PostgreSQL is durable truth.
Redis is transport/coordination.
Secrets never enter Git, prompts, events, or logs.
Media persistence and public delivery are separate.
Configuration has one owner per concern.
Backups must be restore-tested.
Publishing can be paused independently of host service manager.
Scale only when measured workload requires it.
```
