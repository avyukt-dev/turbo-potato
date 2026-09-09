# News AI Social Media Manager

# INFRASTRUCTURE_AND_DEPLOYMENT.md

**Status:** Canonical
**Document Role:** Source of truth for infrastructure, deployment, runtime services, server operations, storage, networking, backups, resource management, secrets, monitoring, and production operations.

---

# 1. Purpose

This document defines how the News AI Social Media Manager is deployed and operated.

The initial production target is:

```text
POCO F1
Xiaomi beryllium
postmarketOS
aarch64
OpenRC
headless CLI
Tailscale
```

The architecture must remain portable so the workload can later move to:

```text
more powerful ARM server
x86 server
cloud VM
dedicated GPU machine
hybrid infrastructure
```

without rewriting the application architecture.

---

# 2. Infrastructure Principle

Start small.

The initial system should not require:

```text
Kubernetes
Kafka
20 microservices
multi-region deployment
multi-node PostgreSQL
multi-node Redis
distributed object storage
GPU cluster
large vector database
```

Initial target:

```text
ONE SERVER
ONE POSTGRESQL
ONE REDIS
ONE LOCAL AI SERVICE
ONE APPLICATION STACK
```

Scale only when measured workload requires it.

---

# 3. Initial Hardware

Primary development/production server:

```text
Device: Xiaomi POCO F1
Codename: beryllium
Architecture: aarch64
OS: postmarketOS
Kernel: 7.1.0-rc1-sdm845
RAM: ~5.5 GB usable
Root filesystem: ~51.5 GB
```

The system must treat these values as deployment characteristics, not application assumptions.

---

# 4. Server Role

The POCO should initially host:

```text id="4k93rq"
PostgreSQL
Redis
FastAPI
Collector
Processor
AI Worker
Publisher
Scheduler
llama.cpp
health monitoring
Tailscale
SSH
```

Media storage may initially use local storage.

As media volume grows, move it to dedicated object storage.

---

# 5. Service Architecture

Initial runtime:

```text
                     ┌──────────────┐
                     │   Internet   │
                     └──────┬───────┘
                            ↓
                    ┌───────────────┐
                    │   Collector   │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │   Processor   │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │   PostgreSQL  │
                    └───────────────┘
                            ↑
                            │
                    ┌───────┴───────┐
                    │ Redis Streams  │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │   AI Worker   │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │   llama.cpp   │
                    └───────────────┘

Content → Publisher → Social Platforms
```

---

# 6. Operating System

Initial operating system:

```text
postmarketOS
```

The application must not depend on graphical desktop components.

The server is:

```text
headless
CLI-first
SSH-managed
```

---

# 7. Init System

Current server uses:

```text
OpenRC
```

Therefore infrastructure service definitions should initially target OpenRC.

Application containers may use their own process supervisors where appropriate.

The architecture should not hard-code OpenRC into application code.

---

# 8. Deployment Layers

Separate:

```text
OPERATING SYSTEM
    ↓
SYSTEM SERVICES
    ↓
INFRASTRUCTURE
    ↓
APPLICATION SERVICES
    ↓
WORKERS
```

Example:

```text
OS
├── networking
├── SSH
├── chrony
└── Tailscale

Infrastructure
├── PostgreSQL
├── Redis
└── media storage

Application
├── API
├── Collector
├── Processor
├── AI Worker
├── Publisher
└── Scheduler
```

---

# 9. Network

Current server network model:

```text
Wi-Fi
 ↓
LAN
 ↓
Internet
```

Remote administration:

```text
Tailscale
 ↓
SSH
```

Tailscale should be preferred over exposing SSH directly to the public Internet.

---

# 10. Network Identity

The application should not depend on a fixed LAN IP.

LAN addresses may change.

Service discovery should use:

```text
localhost
service name
configured hostname
```

where appropriate.

Tailscale provides stable remote connectivity.

---

# 11. DNS

System DNS should remain explicitly configured and monitored.

Current deployment uses:

```text
1.1.1.1
8.8.8.8
```

DNS configuration is infrastructure configuration and must not be embedded in application code.

---

# 12. Time Synchronization

Accurate time is mandatory.

Required:

```text
chrony / NTP
```

because the system depends on:

```text
scheduled jobs
OAuth tokens
API authentication
publication timestamps
event ordering
analytics
logs
```

---

# 13. Time Zone

Server system timezone:

```text
Asia/Kolkata
```

Application timestamps should still be stored internally in UTC.

Editorial schedules may explicitly use:

```text
Asia/Kolkata
```

or another configured timezone.

---

# 14. Storage Layout

Recommended:

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

Exact filesystem locations may be changed during implementation.

The application should obtain paths from configuration.

---

# 15. Application Directory

Example:

```text
/opt/news-ai/app
```

contains deployed application code.

Production deployments should use immutable or versioned releases where practical.

---

# 16. Configuration Directory

Example:

```text
/opt/news-ai/config
```

contains:

```text
editorial/
sources/
models/
platforms/
```

Secrets must not be stored here in plaintext.

---

# 17. Model Storage

Local AI models should be stored separately:

```text
/opt/news-ai/models/
```

Recommended structure:

```text
models/
├── llama/
├── qwen/
└── other/
```

Model files should not be committed to Git.

---

# 18. Media Storage

Initial media:

```text
/opt/news-ai/media/
```

Recommended structure:

```text
media/
├── generated/
├── processed/
├── published/
├── thumbnails/
└── temporary/
```

Temporary files must have cleanup policies.

---

# 19. Database Storage

PostgreSQL should use its native managed data directory.

Do not store application-generated database files manually inside the application repository.

---

# 20. Redis Storage

Redis persistence should be configured according to workload.

Redis is primarily:

```text
event bus
queue
short-lived cache
distributed coordination
```

PostgreSQL remains the persistent source of truth.

---

# 21. PostgreSQL Principle

PostgreSQL is authoritative for:

```text
stories
claims
evidence
entities
fact checks
content
publications
accounts
audit records
jobs
```

Redis must never become the only location containing important business state.

---

# 22. Redis Principle

Redis Streams handle asynchronous work.

Example:

```text
article.discovered
article.normalized
story.created
claims.extracted
evidence.requested
story.verified
content.generated
publication.scheduled
publication.executed
```

The exact event contracts are defined by `EVENTS.md`.

---

# 23. FastAPI Service

API responsibilities:

```text
authentication
authorization
story access
review queue
content management
publication management
configuration
health endpoints
administration
```

The API should not perform long-running AI inference synchronously.

---

# 24. Collector Service

Collector responsibilities:

```text
RSS
news feeds
approved web sources
social discovery
source polling
raw article ingestion
```

Collector output:

```text
article.discovered
```

---

# 25. Processor Service

Processor responsibilities:

```text
normalization
canonical URL
language detection
duplicate detection
story clustering
entity extraction
initial classification
```

---

# 26. AI Worker

AI Worker responsibilities:

```text
claim extraction
classification
editorial scoring
summarization
research assistance
fact-sheet generation
content generation
translation
quality checks
```

AI provider architecture is defined by `AI_PLATFORM.md`.

---

# 27. Local AI Service

Initial local AI service:

```text
llama.cpp
```

The model should run as a separate service/process.

Application workers communicate with it through a defined interface.

---

# 28. Local AI Resource Policy

The POCO is resource constrained.

Default local workload should favor:

```text
classification
entity extraction
language detection
similarity
deduplication
short summarization
basic claim extraction
```

Complex reasoning may be routed to cloud models.

---

# 29. Model Size Policy

Initial candidate range:

```text
0.5B – 3B
```

Potential:

```text
4B
```

if benchmarks justify it.

Large:

```text
7B–8B Q4
```

should be tested rather than assumed viable.

Very large models:

```text
13B+
```

are not initial targets for the POCO.

---

# 30. AI Hardware Acceleration

Never assume:

```text
GPU
NPU
DSP
```

acceleration exists or is useful.

Benchmark actual inference backends.

Record:

```text
tokens/sec
latency
memory
CPU utilization
temperature
stability
```

---

# 31. AI Concurrency

Local inference concurrency should be conservative.

Initial principle:

```text
one model
limited concurrent requests
queue excess work
```

The goal is predictable throughput rather than maximum theoretical concurrency.

---

# 32. Memory Protection

The server has limited RAM.

Services must have resource budgets.

Example conceptual allocation:

```text
PostgreSQL      → controlled
Redis           → controlled
API             → small
Workers         → small
llama.cpp       → largest allocation
OS              → reserved
```

Exact limits should be established through measurement.

---

# 33. Swap

A small amount of swap may be used as an emergency safety mechanism.

Swap must not be treated as normal AI memory.

Heavy swapping should trigger monitoring alerts.

---

# 34. CPU Management

Long-running AI workloads can consume CPU continuously.

Workers should support:

```text
concurrency limits
job timeouts
queue backpressure
```

Do not allow an accidental loop to consume all CPU indefinitely.

---

# 35. Temperature Management

The POCO is a mobile device operating as a server.

Monitor:

```text
battery temperature
CPU temperature where exposed
thermal throttling
```

Long AI workloads should be throttled or queued if thermal conditions become unsafe.

---

# 36. Battery Operation

The system must recognize that the server may run from battery.

Monitor:

```text
capacity
charging status
temperature
voltage
current
health
```

The existing server health command should eventually expose these values.

---

# 37. Battery-Aware Workload

If battery falls below a configured threshold:

```text
pause optional workloads
reduce AI concurrency
delay expensive research
delay media generation
```

Critical services should remain available.

Thresholds must be configurable.

---

# 38. Power Loss

The system should recover automatically after power restoration.

Required:

```text
network recovery
database recovery
Redis recovery
application service startup
worker recovery
scheduler recovery
```

---

# 39. Service Startup Order

Recommended logical order:

```text
network
 ↓
time synchronization
 ↓
PostgreSQL
 ↓
Redis
 ↓
llama.cpp
 ↓
API
 ↓
Collector
 ↓
Processor
 ↓
AI Worker
 ↓
Publisher
 ↓
Scheduler
```

Actual OpenRC dependencies should enforce only the necessary relationships.

---

# 40. Service Supervision

Every production worker must have:

```text
automatic restart
failure logging
startup timeout
shutdown handling
health status
```

---

# 41. Graceful Shutdown

Workers must stop accepting new work before shutdown.

Sequence:

```text
stop new jobs
 ↓
finish safe active work
 ↓
acknowledge completed jobs
 ↓
close connections
 ↓
exit
```

Long-running jobs should support cancellation where possible.

---

# 42. Database Migrations

Database schema changes must use migrations.

Never manually edit production tables as the normal deployment method.

Recommended:

```text
Alembic
```

or an equivalent migration framework.

---

# 43. Migration Policy

Every migration must be:

```text
versioned
repeatable in deployment
reviewed
tested
backward-aware where needed
```

---

# 44. Deployment Process

Canonical:

```text
Git
 ↓
Test
 ↓
Build
 ↓
Migration Check
 ↓
Deploy
 ↓
Restart affected services
 ↓
Health Check
 ↓
Smoke Test
```

---

# 45. Zero-Downtime Requirement

Zero-downtime deployment is not initially mandatory on the single POCO server.

Prefer:

```text
safe restart
```

over unnecessary deployment complexity.

As scale increases, introduce rolling deployment.

---

# 46. Versioned Releases

Recommended:

```text
/opt/news-ai/releases/
├── 2026-09-09_001/
├── 2026-09-10_001/
└── current -> ...
```

This allows rollback.

Exact mechanism may evolve.

---

# 47. Rollback

Rollback should support:

```text
application version
configuration version
database migration strategy
AI model version
prompt version
```

Application rollback must not blindly roll database schema backward.

---

# 48. AI Model Rollback

AI models must be versioned.

Example:

```text
model_id
provider
model_name
version
quantization
checksum
```

If a model causes quality regression:

```text
disable
 ↓
route to previous model
```

---

# 49. Prompt Rollback

Prompts are versioned separately.

Example:

```text
prompt_id
task
version
checksum
active
```

A bad prompt must be reversible without application redeployment where practical.

---

# 50. Configuration

Configuration should come from:

```text
environment variables
versioned YAML
secret store
```

with clear precedence.

Recommended:

```text
defaults
 ↓
config files
 ↓
environment
 ↓
runtime administrative settings
```

Secrets must follow a separate secure path.

---

# 51. Environment Separation

At minimum:

```text
development
staging
production
```

Production credentials must never be automatically inherited by development.

---

# 52. Development Mode

Recommended:

```text
AI_MODE=MOCK
SOCIAL_MODE=MOCK
```

This allows development without external costs or accidental publication.

---

# 53. Local Mode

```text
AI_MODE=LOCAL
```

routes supported tasks to llama.cpp.

---

# 54. Cloud Mode

```text
AI_MODE=CLOUD
```

routes selected workloads to configured cloud providers.

---

# 55. Hybrid Mode

Recommended production configuration:

```text
AI_MODE=HYBRID
```

Simple workloads:

```text
local
```

Complex research:

```text
cloud
```

---

# 56. Secrets

Secrets include:

```text
AI API keys
social tokens
database passwords
Telegram bot tokens
OAuth secrets
session secrets
encryption keys
```

They must never be committed to Git.

---

# 57. Secret Isolation

Secrets should be available only to services that need them.

Example:

```text
Collector
→ source credentials only

AI Worker
→ AI credentials

Publisher
→ social credentials

API
→ session/auth secrets
```

---

# 58. Database Credentials

Application services should use dedicated database credentials where practical.

Example:

```text
news_ai_api
news_ai_worker
news_ai_migration
```

Avoid using PostgreSQL superuser credentials from the application.

---

# 59. Redis Security

Redis should not be exposed publicly.

Bind it to:

```text
localhost
```

or an appropriately isolated private network.

---

# 60. PostgreSQL Security

PostgreSQL should not be exposed directly to the public Internet.

Use:

```text
localhost
private network
Tailscale
```

only when remote administrative access is genuinely required.

---

# 61. SSH

SSH should remain restricted.

Preferred model:

```text
Internet
   X
   │
Tailscale
   ↓
SSH
```

Public SSH exposure should not be required for normal administration.

---

# 62. Tailscale

Tailscale provides:

```text
secure remote administration
private service access
stable remote connectivity
```

The application should not expose administrative endpoints publicly merely for convenience.

---

# 63. Firewall

Default posture:

```text
deny unnecessary inbound traffic
allow required local services
allow Tailscale management
```

Only explicitly required public services should be exposed.

---

# 64. Public API

If the API eventually needs public access:

```text
Internet
 ↓
reverse proxy
 ↓
HTTPS
 ↓
FastAPI
```

Do not expose the raw application server directly without appropriate protection.

---

# 65. Reverse Proxy

Possible initial choices:

```text
Caddy
Nginx
Traefik
```

Use the simplest appropriate option.

A reverse proxy is not mandatory for internal Tailscale-only access.

---

# 66. TLS

Any externally accessible application must use HTTPS.

Certificates should be automatically renewed where possible.

---

# 67. Media Public Access

Some social APIs require publicly accessible media.

Media hosting must be separated from:

```text
admin API
database
internal services
```

Only intended media should be publicly accessible.

---

# 68. Media URL Security

Do not expose:

```text
database dumps
configuration
logs
credentials
internal files
```

through the media server.

Use an explicit media root.

---

# 69. Backup Principle

Backups must protect:

```text
PostgreSQL
configuration
editorial rules
source configuration
AI metadata
publication history
audit logs
important media
```

---

# 70. Database Backups

At minimum:

```text
daily PostgreSQL backup
```

Prefer:

```text
scheduled logical backup
+
periodic physical/base backup
```

as the system becomes more important.

---

# 71. Backup Location

Never store the only backup on the same filesystem as the database.

Preferred:

```text
POCO
 ↓
encrypted backup
 ↓
separate storage
```

---

# 72. Backup Encryption

Backups containing:

```text
social account information
publication records
potentially sensitive content
```

should be encrypted.

Encryption keys must be stored separately from backups.

---

# 73. Backup Retention

Initial policy:

```text
daily → short retention
weekly → longer retention
monthly → optional long-term retention
```

Exact retention should be configured according to storage capacity.

---

# 74. Backup Verification

A backup is not considered valid merely because a command succeeded.

Regularly test:

```text
restore
database integrity
configuration restoration
application startup
```

---

# 75. Disaster Recovery

Minimum recovery procedure:

```text
new server
 ↓
install OS/runtime
 ↓
restore configuration
 ↓
restore PostgreSQL
 ↓
restore media
 ↓
restore models
 ↓
install application
 ↓
run migrations
 ↓
start services
 ↓
health check
```

---

# 76. Recovery Priority

Recommended:

```text
1. PostgreSQL
2. Redis/event processing
3. API
4. AI
5. Collector
6. Publisher
7. Scheduler
8. historical media
```

Publication state and audit history are more important than cached data.

---

# 77. Cache Recovery

Redis data may be reconstructable.

Therefore:

```text
PostgreSQL > Redis
```

for persistence priority.

---

# 78. Queue Recovery

After Redis restart:

```text
inspect pending streams
 ↓
recover consumer groups
 ↓
retry incomplete jobs
 ↓
detect stale jobs
```

Do not duplicate completed work.

---

# 79. Job State

Persistent job state belongs in PostgreSQL.

Redis contains execution-oriented state.

Example:

```text
PostgreSQL:
job_id = 123
status = RETRYING

Redis:
message available for worker
```

---

# 80. Health Monitoring

Health monitoring must eventually cover:

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
├── Facebook
└── Telegram
```

---

# 81. Existing Server Health

The existing `/usr/local/bin/health` command should be extended rather than replaced unnecessarily.

Current health areas include:

```text
system
memory
filesystem
battery
network
Tailscale
SSH
warnings
```

The News AI section should be added when the application services are deployed.

---

# 82. Health Exit Codes

Recommended:

```text
0 → healthy
1 → warning
2 → critical
```

This allows scripting and monitoring.

---

# 83. Health Categories

Each subsystem should report:

```text
OK
WARNING
CRITICAL
UNKNOWN
```

Example:

```text
PostgreSQL: OK
Redis: OK
llama.cpp: WARNING
Publisher: CRITICAL
```

---

# 84. Metrics

Minimum metrics:

```text
CPU utilization
RAM utilization
disk utilization
temperature
battery
database connections
Redis memory
queue depth
AI latency
AI throughput
worker failures
publication failures
```

---

# 85. Logs

Each service should emit structured logs.

Recommended fields:

```text
timestamp
service
level
message
request_id
job_id
story_id
publication_id
error_code
duration
```

---

# 86. Log Levels

Use:

```text
DEBUG
INFO
WARNING
ERROR
CRITICAL
```

Production should normally run at:

```text
INFO
```

with configurable escalation.

---

# 87. Log Rotation

Logs must rotate.

Do not allow:

```text
/opt/news-ai/logs/
```

to consume the entire filesystem.

---

# 88. Disk Monitoring

Monitor:

```text
root filesystem
media
database
logs
models
backups
```

Suggested warning thresholds:

```text
70% → warning
85% → high warning
95% → critical
```

Exact values are configurable.

---

# 89. Disk Cleanup

Automatic cleanup may remove:

```text
temporary files
old worker artifacts
expired caches
old generated intermediates
```

It must never automatically remove:

```text
database backups
publication records
audit logs
evidence
fact sheets
```

without explicit retention policy.

---

# 90. Database Maintenance

PostgreSQL should receive:

```text
vacuum
analyze
index maintenance
connection monitoring
backup
```

according to workload.

---

# 91. Redis Maintenance

Monitor:

```text
memory
stream length
consumer lag
blocked clients
connection count
```

Old event retention should be configured.

---

# 92. Event Retention

Events should not grow without bound.

Use retention policies appropriate to:

```text
debugging needs
audit requirements
storage capacity
reprocessing requirements
```

Business truth remains in PostgreSQL.

---

# 93. Queue Backpressure

If workers cannot keep up:

```text
queue grows
 ↓
backpressure
 ↓
reduce optional work
 ↓
increase worker capacity if safe
```

Do not allow unlimited memory growth.

---

# 94. Priority Queues

High-value work may receive higher priority.

Example:

```text
P0
breaking high-impact event

P1
major India / geopolitical story

P2
normal news

P3
background historical research
```

The exact priority policy remains configurable.

---

# 95. Resource-Aware Scheduling

On constrained hardware:

```text
high-priority task
```

may temporarily preempt:

```text
low-priority AI task
```

Example:

```text
breaking-news research
>
historical batch analysis
```

---

# 96. Network Failure

If Internet connectivity fails:

```text
collector pauses
external research pauses
social publishing pauses
```

but:

```text
database
local processing
already queued work
```

should remain operational where possible.

---

# 97. Social API Failure

If one platform is unavailable:

```text
Instagram DOWN
```

the system should continue:

```text
X
Telegram
Facebook
```

where those platforms remain healthy.

---

# 98. Cloud AI Failure

If cloud AI is unavailable:

```text
simple tasks → local
complex tasks → queued
```

Do not silently substitute a low-quality model for high-risk reasoning.

---

# 99. Local AI Failure

If llama.cpp fails:

```text
simple tasks may route elsewhere
```

according to AI routing policy.

High-risk content may instead:

```text
queue
require human research
route to cloud
```

depending on configured policy.

---

# 100. Database Failure

If PostgreSQL is unavailable:

```text
publication must stop
business-state mutations must stop
```

Workers should fail safely rather than operate against an imaginary source of truth.

---

# 101. Redis Failure

If Redis is unavailable:

```text
new asynchronous jobs may pause
```

but persistent PostgreSQL data remains intact.

Services should reconnect automatically.

---

# 102. Security Monitoring

Monitor:

```text
failed SSH authentication
unexpected open ports
credential failures
API authentication failures
unusual publishing activity
unexpected service restarts
filesystem anomalies
```

---

# 103. Audit Monitoring

Alert on unusual patterns:

```text
large number of publications
mass deletion
repeated approval overrides
repeated failed logins
unexpected account changes
```

---

# 104. Production Access

Production access should be limited to authorized administrators.

Use:

```text
Tailscale
SSH keys
least privilege
```

Password-based SSH should not be the preferred administrative mechanism.

---

# 105. Environment Variables

Example:

```text
APP_ENV=production

DATABASE_URL=...
REDIS_URL=...

AI_MODE=hybrid

LOCAL_AI_URL=...
OPENAI_API_KEY=...
OTHER_AI_PROVIDER_KEY=...

INSTAGRAM_CREDENTIAL_REF=...
X_CREDENTIAL_REF=...
TELEGRAM_CREDENTIAL_REF=...
```

Actual secrets must not appear in this document or Git.

---

# 106. Service Configuration

Each service should have explicit configuration.

Example:

```text
news-ai-api
news-ai-collector
news-ai-processor
news-ai-ai-worker
news-ai-publisher
news-ai-scheduler
```

---

# 107. Service Names

Canonical logical names:

```text
news-ai-api
news-ai-collector
news-ai-processor
news-ai-ai-worker
news-ai-publisher
news-ai-scheduler
news-ai-media-worker
news-ai-llama
```

---

# 108. OpenRC Integration

OpenRC services should support:

```text
start
stop
restart
status
```

and appropriate dependency ordering.

---

# 109. Containerization

Docker/Podman may be used where practical.

Do not containerize merely for architectural fashion.

On the POCO, container overhead and ARM compatibility must be considered.

---

# 110. Native vs Container Decision

Initial recommendation:

```text
PostgreSQL → native package where stable
Redis → native package where stable
Tailscale → native package
OpenRC services → native application processes
llama.cpp → native optimized binary
```

Containers can be introduced selectively if they simplify reproducibility.

---

# 111. Build Architecture

All production binaries must match:

```text
aarch64
```

The build pipeline should explicitly support ARM64.

---

# 112. Cross-Compilation

Where local compilation is too slow:

```text
development workstation
 ↓
ARM64 build
 ↓
artifact
 ↓
POCO deployment
```

The runtime must still be tested on the actual POCO.

---

# 113. Python Runtime

The Python environment should be isolated.

Recommended:

```text
venv
```

or another reproducible environment.

Do not rely on arbitrary system Python packages.

---

# 114. Dependency Locking

Production dependencies must be version-pinned or lockfile-controlled.

This applies to:

```text
Python
Node
system packages where practical
AI libraries
```

---

# 115. Database Connection Pooling

FastAPI and workers should use controlled connection pools.

Do not allow every worker to create unlimited PostgreSQL connections.

---

# 116. API Resource Limits

API should enforce:

```text
request size
upload size
request timeout
authentication
rate limiting where appropriate
```

---

# 117. Worker Timeouts

Long-running tasks need explicit timeouts.

Examples:

```text
HTTP request timeout
AI inference timeout
research timeout
publication timeout
media generation timeout
```

Timeout values are task-specific.

---

# 118. Graceful Failure

A service should fail with:

```text
clear error
structured log
retry decision
persistent job state
```

rather than silently losing work.

---

# 119. Error Correlation

Use IDs:

```text
request_id
job_id
story_id
claim_id
publication_id
ai_run_id
```

This allows one event to be traced through the system.

---

# 120. Observability Flow

Example:

```text
story_123
 ↓
job_456
 ↓
ai_run_789
 ↓
content_variant_321
 ↓
publication_654
 ↓
platform_post_987
```

Every layer should remain traceable.

---

# 121. Maintenance Windows

Routine maintenance should avoid:

```text
breaking-news windows
scheduled publication windows
backup conflicts
high-load AI jobs
```

where practical.

---

# 122. Manual Maintenance Mode

Support:

```text
MAINTENANCE_MODE=true
```

which may:

```text
pause collectors
pause publishers
pause scheduled jobs
```

while keeping:

```text
API
database
SSH
health monitoring
```

available.

---

# 123. Emergency Operations

Administrators must be able to:

```text
stop publishing
stop collectors
stop AI workers
pause scheduler
inspect queues
inspect failed jobs
restore database
rollback application
```

without modifying source code.

---

# 124. Production Checklist

Before enabling live publishing:

```text
[ ] PostgreSQL healthy
[ ] Redis healthy
[ ] backups configured
[ ] backup restore tested
[ ] llama.cpp healthy
[ ] AI routing tested
[ ] source collection tested
[ ] fact-check pipeline tested
[ ] content quality gate tested
[ ] social accounts connected
[ ] credentials secured
[ ] mock publishing tested
[ ] live publishing tested safely
[ ] duplicate protection tested
[ ] retry handling tested
[ ] emergency pause tested
[ ] health monitoring enabled
[ ] logs rotating
[ ] disk monitoring enabled
[ ] Tailscale access verified
```

---

# 125. Initial Production Topology

```text
                         INTERNET
                            │
             ┌──────────────┼──────────────┐
             │              │              │
           NEWS           AI APIs       SOCIAL APIs
             │              │              │
             └──────────────┼──────────────┘
                            │
                     ┌──────▼──────┐
                     │   POCO F1   │
                     │ postmarketOS│
                     └──────┬──────┘
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
 ┌─────▼─────┐        ┌─────▼─────┐        ┌─────▼─────┐
 │ PostgreSQL│        │   Redis    │        │ llama.cpp │
 └─────┬─────┘        └─────┬─────┘        └─────┬─────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                     ┌──────▼──────┐
                     │ Application │
                     │   Workers   │
                     └──────┬──────┘
                            │
                ┌───────────┼───────────┐
                ↓           ↓           ↓
             Collector    AI        Publisher
                                      │
                              ┌───────┼───────┐
                              ↓       ↓       ↓
                          Instagram   X   Telegram
```

---

# 126. Scaling Path

When the POCO becomes insufficient:

```text
PHASE 1
POCO
everything local

        ↓

PHASE 2
POCO
PostgreSQL + Redis + lightweight workers

        ↓

PHASE 3
Dedicated server
PostgreSQL + Redis + API/workers

        ↓

PHASE 4
Dedicated AI machine
GPU / larger local model

        ↓

PHASE 5
Hybrid
database/server + dedicated AI + cloud AI
```

---

# 127. What Should Move First

When resources become constrained:

```text
1. media storage
2. AI inference
3. heavy research workers
4. analytics processing
5. PostgreSQL
```

The exact migration order depends on measured bottlenecks.

---

# 128. Do Not Prematurely Scale

Do not introduce:

```text
Kubernetes
Kafka
service mesh
multi-region
complex orchestration
```

until actual workload justifies them.

---

# 129. Infrastructure Benchmarking

Measure before scaling.

Important measurements:

```text
CPU utilization
RAM utilization
database latency
Redis throughput
AI tokens/sec
AI queue latency
article throughput
research throughput
publication throughput
disk growth
network bandwidth
thermal behavior
```

---

# 130. Capacity Planning

The system should estimate:

```text
articles/day
stories/day
claims/day
AI requests/day
content variants/day
publications/day
media GB/day
database growth/month
```

This determines when the POCO must be upgraded.

---

# 131. Initial Resource Targets

Initial operational target:

```text
stable 24/7 operation
low queue latency
no sustained thermal overload
no uncontrolled memory growth
no database saturation
no publication duplication
```

Exact throughput targets should be benchmark-derived.

---

# 132. Reliability Target

Initial target:

```text
recover automatically from ordinary process failures
```

Examples:

```text
worker crash
Redis restart
temporary network loss
AI process restart
social API timeout
```

---

# 133. Data Integrity Target

Highest priority:

```text
PostgreSQL integrity
publication history
evidence provenance
audit history
```

A temporary queue delay is preferable to losing factual provenance.

---

# 134. Operational Philosophy

The infrastructure should optimize for:

```text
simplicity
recoverability
observability
security
resource efficiency
auditability
```

not architectural complexity.

---

# 135. Final Infrastructure Rules

```text
PostgreSQL is persistent truth.

Redis is execution infrastructure.

The POCO is the initial server, not a permanent architectural limitation.

The application must remain portable.

All timestamps are internally UTC.

Time synchronization is mandatory.

Secrets never enter Git or logs.

SSH should use Tailscale/private access.

Internal databases must not be publicly exposed.

AI workloads must respect hardware limits.

Thermal behavior must be monitored.

Battery state must be monitored.

Workers must be supervised.

Jobs must be recoverable.

Publishing must be pausable globally.

Backups must exist outside the primary disk.

Backups must be tested by restoration.

Logs must rotate.

Disk usage must be monitored.

External API limits must be configuration-driven.

Production failures must be observable.

No single Redis failure may destroy business truth.

No worker crash may silently lose a job.

No deployment should knowingly destroy publication or evidence history.
```

---

# 136. Source of Truth

This document is authoritative for:

```text
server deployment
OS/runtime assumptions
service topology
resource management
networking
Tailscale
storage
backups
secrets
health monitoring
logging
deployment
rollback
disaster recovery
operational scaling
```

Application behavior remains governed by:

```text
ARCHITECTURE.md
DATA_MODEL.md
EVENTS.md
AI_PLATFORM.md
CONTENT_AND_EDITORIAL.md
SOCIAL_PUBLISHING.md
```

---

# 137. Final Infrastructure Architecture

```text
                         ┌──────────────────────┐
                         │       INTERNET       │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────┼────────────────┐
                    │               │                │
                  NEWS            AI APIs        SOCIAL APIs
                    │               │                │
                    └───────────────┼────────────────┘
                                    │
                          ┌─────────▼─────────┐
                          │      POCO F1      │
                          │   postmarketOS    │
                          │     OpenRC        │
                          └─────────┬─────────┘
                                    │
       ┌────────────────────────────┼────────────────────────────┐
       │                            │                            │
┌──────▼──────┐              ┌──────▼──────┐              ┌──────▼──────┐
│ PostgreSQL  │              │ Redis       │              │ llama.cpp   │
│ Source      │              │ Event Bus   │              │ Local AI    │
│ of Truth    │              │ + Queue     │              │             │
└──────┬──────┘              └──────┬──────┘              └──────┬──────┘
       │                            │                            │
       └────────────────────────────┼────────────────────────────┘
                                    │
                           ┌────────▼────────┐
                           │ Application     │
                           │ Services        │
                           ├─────────────────┤
                           │ API             │
                           │ Collector       │
                           │ Processor       │
                           │ AI Worker       │
                           │ Publisher       │
                           │ Scheduler       │
                           │ Media Worker    │
                           └────────┬────────┘
                                    │
                         ┌──────────┼──────────┐
                         ↓          ↓          ↓
                    Instagram       X       Telegram
                         │          │          │
                         └──────────┼──────────┘
                                    ↓
                               Analytics

                     Tailscale + SSH
                            │
                            ↓
                     Administration

                     Backup System
                            │
                            ↓
                    External Storage
```

---

# 138. Canonical Operational Principle

The infrastructure exists to make the editorial system **reliable, recoverable, observable, and secure**.

It must never become the reason the application needs unnecessary complexity.

**Start with the POCO. Measure everything. Keep PostgreSQL authoritative. Keep Redis disposable. Keep AI replaceable. Keep social adapters isolated. Keep backups external. Keep high-risk publishing human-controlled. Scale only when evidence says to scale.**
