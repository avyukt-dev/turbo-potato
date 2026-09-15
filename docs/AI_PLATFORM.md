# News AI Social Media Manager — AI Platform

Content prompt v4 requires application-validated claim presentations under
`certainty-policy-v1`. Quality prompt v4 checks prose against those declarations
and the immutable Fact Sheet and returns typed certainty_escalations. AI cannot
change claim status/label or override deterministic certainty errors. This is not
reasoning escalation, a new AI stage, or deterministic natural-language inference.
`certainty-policy-v1` authorizes only the current deterministic producer pairs:
SUPPORTED/TRUE, PARTIALLY_SUPPORTED/PARTIALLY_TRUE, DISPUTED/UNVERIFIED,
UNVERIFIED/UNVERIFIED, REFUTED/FALSE. All other pairs fail closed, including unused
canonical labels; future semantics require an explicitly versioned producer/policy change.

## 1. Purpose

Current claim extraction uses prompt v3 and methodology v3 for typed statement
classification, not verification. Content prompt v4 / methodology v4 requires
exact claim-semantic presentations as well as PR2 certainty presentations.
Quality prompt v4 / methodology v7 runs secondary prose checks for announcement,
plan, expectation, forecast, attribution and semantic-type laundering. Typed prose
claim_semantic_escalations are separate from certainty_escalations; any escalation
fails application quality. Deterministic semantic-validator-v4 independently checks
persisted metadata against the immutable Fact Sheet under claim-semantics-policy-v1.
No new AI stage/provider, routing change or AI factual authority is introduced.

This document defines the canonical AI architecture for the News AI Social Media Manager.

It owns:

```text
AIProvider abstraction
local/cloud model routing
AI request/response contracts
prompt versioning
structured-output handling
AI provenance
fallback/cost/latency policy
model lifecycle/evaluation
local llama.cpp integration
AI safety boundaries
```

Search/research methodology is owned by `SOURCE_AND_RESEARCH.md`.

Shared runtime/configuration/lifecycle contracts are defined by `CANONICAL_CONTRACTS.md`.

---

# 2. Core AI Principle

The system is not:

```text
Article
  ↓
LLM
  ↓
Post
```

It is:

```text
Article
  ↓
Story
  ↓
Claims
  ↓
Evidence
  ↓
Verification / Fact Check
  ↓
Fact Sheet
  ↓
AI Interpretation / Transformation
  ↓
Content
```

AI operates on structured, evidence-backed context wherever possible.

AI is not itself evidence.

---

# 3. AI Responsibilities

AI may assist:

```text
CLASSIFICATION
LANGUAGE_DETECTION
KEYWORD_EXTRACTION
ENTITY_EXTRACTION
STORY_SIMILARITY
CLAIM_EXTRACTION
SUMMARIZATION
EDITORIAL_SCORING
RESEARCH_SYNTHESIS
FACT_CHECK_ASSISTANCE
FACT_SHEET_GENERATION
CONTENT_GENERATION
TRANSLATION
QUALITY_CHECKING
IMAGE_BRIEF
```

AI must not independently convert a controversial/unsupported assertion into factual truth.

---

# 4. AI Provider Abstraction

```text
AIProvider
├── LocalLlamaProvider
├── LocalQwenProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
└── FutureProvider
```

Application code requests an AI task through the abstraction.

Do not scatter provider SDK calls through collectors, API routes, editorial logic, research services, or publishing code.

---

# 5. Search Provider Is Separate

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── SpecialistSearchProvider
└── FutureProvider
```

Search providers discover candidate sources.

AI providers generate/analyze/transform.

An orchestrator may use both, but they are not one interface.

---

# 6. AI Request Contract

Illustrative Pydantic model:

```python
from typing import Any
from pydantic import BaseModel, Field

class AIRequest(BaseModel):
    task_type: str
    system_prompt: str
    input: Any
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    response_format: str = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)
```

Optional/request-policy fields may include:

```text
timeout
priority
correlation_id
language
sensitivity classification
provider restrictions
```

Do not include secrets in AI requests.

---

# 7. AI Response Contract

```python
class AIResponse(BaseModel):
    text: str | None = None
    structured: dict[str, Any] | None = None
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int
    finish_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

Every material AI operation should link to an `ai_run`.

---

# 8. AI Run Provenance

Record:

```text
provider
model
task type
prompt version
input artifact IDs/hash
output/result reference
latency
token/usage data where available
validation status
failure/fallback state
reasoning effort
reasoning policy version
reasoning escalation reasons where any
timestamp
```

This allows the system to reconstruct which model/prompt/reasoning policy produced a Fact Sheet,
content variant, or quality assessment.

When an AI route exhausts or is denied fallback, the worker reliability records also preserve a
sanitized `ai_failure_provenance` object on the processing attempt and, when terminal, the dead
letter. The object contains the original task/prompt/input identity, every ordered provider/model
attempt, the final typed failure reason, and the fallback decision:

```json
{
  "task_type": "CLAIM_EXTRACTION",
  "prompt_id": "claim-extraction",
  "prompt_version": "v1",
  "prompt_checksum": "sha256:...",
  "input_artifact_ids": ["article:..."],
  "input_hash": "sha256:...",
  "correlation_id": "uuid",
  "attempts": [
    {
      "provider_id": "local-a",
      "model": "local-model",
      "outcome": "FAILED",
      "failure_reason": "TIMEOUT",
      "reasoning_effort": "MEDIUM"
    },
    {
      "provider_id": "groq",
      "model": "openai/gpt-oss-120b",
      "outcome": "FAILED",
      "failure_reason": "UNAVAILABLE"
    }
  ],
  "final_failure_reason": "UNAVAILABLE",
  "fallback_decision": "EXHAUSTED"
}
```

Raw system prompts, raw AI inputs, request metadata, provider exception text, and provider response
bodies are not persisted in this diagnostic object. The processing-attempt and dead-letter
timestamps remain the authoritative failure timestamps.

---

# 9. Model Registry

Canonical persistence:

```text
ai_models
```

Models declare:

```text
provider
model name/version
local/cloud
capabilities
context limits where known
structured-output support
vision/tool support where relevant
enabled state
cost/latency metadata
```

Models should be enabled/disabled without rewriting business logic.

---

# 10. AI Configuration Ownership

The current primary CLOUD adapter is Groq using `openai/gpt-oss-120b`, with local
llama.cpp as fallback. All four active stages select Groq first in HYBRID mode;
sensitive-topic authorization explicitly allows both providers and remains fail-closed.
Groq uses one bounded HTTP chat-completions request without SDK dependencies or adapter retries.
Only INVALID_RESPONSE, TIMEOUT, RATE_LIMIT, and UNAVAILABLE authorize local fallback;
policy rejection, unknown failures, and context overflow do not.

`AIRequest.reasoning_effort` is a provider-neutral LOW/MEDIUM/HIGH preference. Current factual
production stages normally execute at MEDIUM under `reasoning-routing-policy-v1`. LOW remains
available for explicitly owned routine/constrained tasks. HIGH is never an unexplained stage
default or model self-assessment: current execution requires deterministic policy provenance.
The closed HIGH escalation reasons are:

```text
HIGH_RISK
ATTRIBUTION_OR_INTENT
CAUSAL_REASONING
CREDIBLE_SOURCE_CONFLICT
VALIDATION_FAILURE
```

Application-owned durable facts select these reasons. AI output does not authorize source
authority, independence, claim verification state, evidence truth, or publication. A reasoning
policy change participates in durable semantic identities where AI output can otherwise be
reused, and stale in-flight research work fails closed rather than completing under an old
reasoning policy.

Groq consumes LOW/MEDIUM/HIGH reasoning effort. llama.cpp currently remains reasoning-agnostic
without losing otherwise-authorized fallback eligibility. Provider capability controls only
whether a same-provider HIGH retry is useful; it does not control whether the route itself is
escalated. Every current attempt records effective reasoning effort, policy version, and any
escalation reasons in routing-attempt provenance. Groq requests `include_reasoning: false`;
returned private reasoning fields are not normalized, logged, or persisted. Request mapping
follows the official [Groq reasoning contract](https://console.groq.com/docs/reasoning) and
[chat-completions API](https://console.groq.com/docs/api-reference).

A MEDIUM response rejected as `INVALID_RESPONSE` by structural/domain validation promotes the
route exactly once to HIGH with `VALIDATION_FAILURE`. If the current provider honors reasoning
effort, it receives one same-provider HIGH retry. If it does not, that useless retry is skipped
and the next policy-authorized fallback receives the already-escalated HIGH request. A HIGH
response cannot trigger another reasoning escalation. Deterministic validators remain
operator/application authority regardless of model effort.

Groq receives only existing pipeline context. Built-in browsing, retrieval, and tools are not
enabled; the Research Engine remains the evidence authority. Prompts and semantic validators
are shared across providers. The configured credential reference is `GROQ_API_KEY`, never a
committed key value; missing credentials fail startup/readiness. General adaptive retries,
backoff, and resilience loops remain reserved for a later resilience layer; the single bounded
MEDIUM-to-HIGH validation escalation above is reasoning-policy behavior, not general retry logic.

Model/provider configuration belongs under:

```text
config/models/
├── providers.yaml
├── policy.yaml
└── stages/
    ├── claim-extraction.yaml
    ├── evidence-assessment.yaml
    ├── content-generation.yaml
    └── quality-checking.yaml
```

`providers.yaml` owns provider infrastructure and adapter construction only. `policy.yaml`
owns cross-cutting routing authorization such as locality mode and sensitive-topic provider
allowlists. Each closed production stage file owns its task identity, versioned provider-neutral
prompt reference, ordered provider/model selections, and explicitly authorized fallback reasons.

The production pipeline constructs one provider registry/router and shares it across stage
stacks. For each attempt, the router applies the model paired with that stage's selected
provider. Domain services remain provider-neutral and never branch on adapter type. Future
providers are added through a closed adapter factory plus provider and stage configuration,
without moving provider details into stage services.

Production stage configuration is authoritative for model selection. A caller-supplied model
must agree with the stage's primary selection; fallback attempts still use the model paired with
each selected provider. Conflicting request-level model input fails closed rather than silently
changing the route.

Prompt templates/versions belong under:

```text
config/prompts/
```

Stage YAML references these prompt files; prompt text is not copied into model configuration or
forked per provider. Prompt identity, version, and checksum remain part of AI provenance.

Research evidence policy does not belong in AI model routing files.

Editorial priority does not belong in provider configuration.

---

# 11. Prompt Layout

Recommended:

```text
config/prompts/
├── classification/
├── entity-extraction/
├── claim-extraction/
├── research-synthesis/
├── fact-check/
├── fact-sheet/
├── editorial/
├── content/
├── translation/
└── quality/
```

Prompts are execution instructions/templates, not the canonical home of research/editorial policy.

They should reference effective policy/artifacts rather than duplicate policy definitions.

---

# 12. Prompt Versioning

Every production prompt has:

```text
task
version
checksum
input schema
output schema
active/enabled state
```

Example:

```text
claim-extraction.v1
claim-extraction.v2
```

Changing material prompt behavior creates a new version.

Do not silently edit a production prompt while retaining the same version identifier.

---

# 13. Prompt Boundary

Prompts should clearly separate:

```text
TASK
FACTUAL INPUT
EVIDENCE
UNCERTAINTY
EDITORIAL CONTEXT
CONSTRAINTS
OUTPUT SCHEMA
```

Editorial context may choose emphasis/tone.

It must not instruct the model to ignore evidence, invent facts, fabricate quotations/statistics, or change canonical claim/fact-check state without evidence evaluation.

---

# 14. Structured Output

Use structured output whenever downstream code needs machine-readable artifacts.

Examples:

```text
classification
entity extraction
claim extraction
research synthesis
fact-check assistance
Fact Sheet generation
editorial scoring
quality checks
```

Validation pipeline:

```text
model output
    ↓
parse
    ↓
Pydantic validation
    ↓
semantic/domain validation
    ↓
reference validation
    ↓
persist
```

Raw AI output is untrusted input.

---

# 15. Structured-Output Failure

Current reasoning-policy handling is bounded and deterministic:

```text
MEDIUM output rejected as INVALID_RESPONSE
  ↓
route becomes HIGH + VALIDATION_FAILURE
  ↓
provider honors reasoning effort? ── yes → one same-provider HIGH retry
             │
             no
             ↓
       allowed fallback receives HIGH
  ↓
success OR normal policy-authorized fallback/failure
  ↓
STOP — no HIGH→HIGH reasoning escalation loop
```

Other provider failures continue to use their existing task-specific fallback policy; PR6 does
not introduce general adaptive retry/backoff. Malformed output must never enter the next pipeline
stage merely because it “looks close enough,” and HIGH reasoning cannot override deterministic
validation errors.

---

# 16. Hallucination Control

Primary control is architectural:

```text
AI receives structured evidence-backed context
```

Avoid:

```text
Read 30 raw articles and write what happened.
```

Prefer:

```text
Fact Sheet
+
claims
+
evidence references
+
contradictions
+
timeline
+
source metadata
→ AI
```

Generated factual statements must remain traceable to the Fact Sheet.

---

# 17. Citation/Reference Preservation

When an AI task uses source/evidence references, IDs should be supplied explicitly and preserved through the output when required.

The model must not invent new citation IDs or URLs as if they were evidence.

Reference existence must be followed by entailment validation: the cited evidence must actually support the associated claim.

---

# 18. Prompt Injection

Retrieved web/social/document content is untrusted.

Embedded instructions inside sources must be treated as content, not model/system/runtime instructions.

Source text must never authorize:

```text
secret access
host commands
configuration changes
policy changes
publication
external side effects
```

Tool permissions and task scope come from the application, not retrieved text.

---

# 19. Model Routing

Routing considers:

```text
task capability
quality requirements
latency
cost
local resource pressure
provider availability
privacy/sensitivity policy
structured-output reliability
```

The caller requests a task, not a provider.

---

# 20. Initial Routing Policy

Recommended starting point:

| Task | Preferred route |
| --- | --- |
| language detection | local |
| keyword extraction | local |
| entity extraction | local |
| spam/basic classification | local |
| story similarity | local |
| short summarization | local |
| initial claim extraction | local/cloud |
| editorial scoring | local/cloud |
| complex research synthesis | cloud |
| difficult fact-check assistance | cloud |
| Fact Sheet generation | cloud/local depending complexity |
| final content generation | cloud |
| quality checking | local/cloud |
| image generation | external image model |

This is configuration, not a permanent architectural restriction.

---

# 21. Provider Fallback

Fallback is task-specific and policy-controlled.

Examples:

```text
local unavailable
   ↓
allowed cloud fallback
```

or:

```text
cloud provider A timeout/rate limit
   ↓
allowed provider B
```

Do not route sensitive material to arbitrary providers merely because the preferred provider failed.

---

# 22. Sensitive Data Routing

Before cloud execution, determine:

```text
contains confidential credentials? → never send
contains unnecessary personal data? → minimize/remove
contains sensitive material? → check provider/routing policy
provider permitted for task? → enforce
```

Secrets never enter prompts.

---

# 23. Local AI Service

Preferred local inference boundary:

```text
AI Worker
    ↓
Local AI adapter
    ↓
llama.cpp service
    ↓
GGUF model
```

Use a configured local endpoint rather than embedding low-level inference logic in every worker.

---

# 24. Current POCO Profile

Current local node characteristics include:

```text
Xiaomi POCO F1 / beryllium
aarch64
postmarketOS
~5.5 GB usable RAM
```

These are deployment characteristics, not application assumptions.

The AI layer itself remains platform-independent and communicates through provider abstractions.

---

# 25. Candidate Local Model Range

Guidance to benchmark:

```text
0.5B–3B    primary candidate range
4B         possible with compromises
7B–8B Q4   heavy/experimental
13B+       not an initial POCO target
```

Actual selection is based on measured quality, latency, memory, structured-output reliability, thermal behavior, and power.

---

# 26. Hardware Acceleration

Do not assume:

```text
GPU
NPU
DSP/HTP
```

support.

Benchmark actual available backends on the deployed runtime.

Runtime/platform detection belongs to the infrastructure/runtime layer; the AI provider should consume the resulting configured backend capability rather than hard-code one OS/device path.

---

# 27. Local Benchmarking

Benchmark candidate models with identical representative tasks:

```text
classification
entity extraction
claim extraction
summarization
structured JSON
```

Record:

```text
first-token latency
total latency
tokens/sec
RAM/CPU
thermal behavior
power behavior
failure rate
structured-output validity
```

---

# 28. AI Budgets

Track/configure:

```text
provider spend
request/token budgets
latency targets
concurrency
retry limits
per-task provider allowlists
```

Budgets must not alter evidence truth.

If budget prevents sufficient research/verification, the story may remain incomplete/unverified.

---

# 29. AI Caching

Cache deterministic/reusable AI results only when input artifacts, model, prompt version, and policy-relevant context are represented in the cache key.

Do not reuse stale outputs across material Fact Sheet/source changes.

---

# 30. Two-Pass Generation

For important generated content:

```text
Pass 1 → draft from Fact Sheet
Pass 2 → quality/grounding check against Fact Sheet
```

Different models/providers may be used, but model agreement is not proof.

---

# 31. Quality Check Responsibilities

AI-assisted quality checking may flag:

```text
factual drift
unsupported claims
citation mismatch
fabricated quotes
wrong names/dates/numbers
missing context
overstatement
sensitive-topic errors
defamation risk
```

A quality pass does not authorize external publication in the MVP.

The application also runs deterministic quality-domain semantic validation. It
uses scoped canonical claim/evidence spans and explicit artifact relationships,
not another AI call or provider-specific self-grading prompt. AI assessment still
runs for diagnosis but cannot override deterministic ERROR findings. The existing
deterministic checks are not delegated to an AI prompt and grant no AI evidence
authority. Current quality prompt v4 adds only the secondary prose checks.

---

# 32. Historical Research Boundary

Historical prompts must preserve separate evidence domains:

```text
linguistics
archaeology
genetics
literary sources
epigraphy
chronology
population movement
material culture
cultural transmission
political expansion
military conflict
```

Research methodology comes from `SOURCE_AND_RESEARCH.md` / `config/research/historical-research.yaml`.

Prompts must not encode a predetermined ideological conclusion as fact.

---

# 33. Demographic/Caste/Legal Boundary

AI must preserve:

```text
observed data vs interpretation
correlation vs causation
allegation vs finding/conviction
individual behavior vs group inference
caste identity vs behavioral generalization
```

The AI layer must not erase uncertainty/procedural status contained in the Fact Sheet.

---

# 34. Translation

Translation must preserve:

```text
names/entities
legal procedural status
uncertainty
numbers/units
source attribution
quotation status
```

Material translation uncertainty should be surfaced rather than silently normalized.

---

# 35. Image Briefs

AI may generate image briefs from verified Fact Sheet context.

Synthetic image prompts must not invent details that could be mistaken for documentary evidence.

Visual generation remains downstream from factual grounding.

---

# 36. Failure Handling

AI failure classes may include:

```text
provider unavailable
rate limit
timeout
invalid structured output
safety/policy rejection
context too large
local resource exhaustion
```

Failures create durable job/provenance state and bounded retry/fallback behavior.

They do not produce fabricated placeholder facts.

---

# 37. Development Modes

Recommended:

```text
AI_MODE=MOCK
AI_MODE=LOCAL
AI_MODE=CLOUD
AI_MODE=HYBRID
```

Ordinary unit tests should not require paid/cloud providers.

---

# 38. AI Evaluation

Maintain golden/regression datasets for:

```text
classification
entity extraction
claim extraction
research synthesis
fact-check assistance
Fact Sheet generation
content generation
translation
quality checking
historical evidence handling
```

Compare candidate model/prompt changes against the accepted baseline before promotion.

---

# 39. Model Promotion and Rollback

Model promotion requires measured improvement/acceptability for target tasks.

If a model/prompt causes regression:

```text
disable new model/prompt
route to previous accepted version
preserve provenance
```

Do not require application redeployment solely to change an enabled model/prompt where configuration supports it.

---

# 40. Observability

Track:

```text
ai_runs_total
ai_failures_total
latency by task/model/provider
structured-output validation failure
fallback count
provider rate limits
local queue depth
local RAM/thermal pressure
cost/usage where available
```

Do not log secrets or unnecessary raw sensitive inputs.

---

# 41. Runtime Independence

AI business/application code must not execute host service-manager utilities.

Starting/stopping/checking the local llama.cpp service is performed through `RuntimeController/ServiceManager` in the runtime infrastructure layer.

The AI provider checks its configured endpoint/health contract; it does not assume OpenRC, systemd, or another manager.

---

# 42. Final AI Rules

```text
AIProvider and SearchProvider are separate.
AI output is not evidence.
Fact Sheet is the normal factual input to content generation.
Raw AI output is untrusted until validated.
Provider SDKs stay behind adapters.
Prompts are versioned and do not duplicate research/editorial policy.
Model/provider config belongs in config/models/.
Prompt config belongs in config/prompts/.
Research methodology belongs in config/research/.
Use safe Pydantic defaults in examples.
Do not assume local hardware acceleration.
AI service control is runtime-adapter based, not OS-specific.
Quality pass does not replace human approval in the MVP.
Reasoning escalation is deterministic, versioned, bounded, and never factual authority.
```

---

## Current value annotation and transformation stages

Extraction prompt v3 / methodology v3 requires source-bound typed value candidates.
Content prompt v4 / methodology v4 requires complete anchor presentation declarations.
Quality prompt v4 / methodology v7 returns additional typed prose value_escalations.
The application, not AI, owns exact normalization, stable anchor IDs, conversions,
value-integrity-policy-v1 and semantic-validator-v4 decisions.
No new AI stage/provider/routing/fallback behavior is introduced.
Policy versions and exact anchors/presentations participate in extraction, content
and quality semantic identities alongside exact source/Fact Sheet/Brief/artifact inputs.
The AI secondary prose comparison is not deterministic natural-language proof,
does not calculate new facts and cannot override deterministic ERRORs or alter truth.
