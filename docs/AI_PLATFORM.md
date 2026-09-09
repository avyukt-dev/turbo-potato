# News AI Social Media Manager — AI Platform

## 1. Purpose

This document defines the canonical AI architecture for the News AI Social Media Manager.

It covers:

* provider abstraction
* local AI
* cloud AI
* model routing
* research orchestration
* structured outputs
* prompt management
* AI provenance
* fallback
* cost tracking
* latency tracking
* evaluation
* safety controls
* model lifecycle
* POCO server integration

The AI layer MUST remain replaceable.

No business service should depend directly on a specific AI provider SDK.

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

The system is:

```text
Article
  ↓
Story
  ↓
Claims
  ↓
Evidence
  ↓
Fact Check
  ↓
Fact Sheet
  ↓
AI Interpretation
  ↓
Content
```

AI operates on structured information wherever possible.

---

# 3. AI Responsibilities

AI may perform:

```text
CLASSIFICATION
LANGUAGE_DETECTION
ENTITY_EXTRACTION
CLAIM_EXTRACTION
STORY_SIMILARITY
SUMMARIZATION
RESEARCH_SYNTHESIS
FACT_CHECK_ASSISTANCE
FACT_SHEET_GENERATION
EDITORIAL_SCORING
CONTENT_GENERATION
QUALITY_CHECKING
```

AI MUST NOT independently decide:

```text
"this controversial claim is true"
```

without evidence.

---

# 4. Provider Abstraction

Canonical interface:

```text
AIProvider
├── OpenAIProvider
├── GeminiProvider
├── ClaudeProvider
├── LocalLlamaProvider
├── LocalQwenProvider
└── FutureProvider
```

Application code uses:

```python id="d9r8ah"
ai.generate(...)
```

not:

```python id="k1zz5x"
openai_client.responses.create(...)
```

throughout the codebase.

---

# 5. Provider Interface

Conceptual interface:

```python id="zsp8a7"
class AIProvider(Protocol):

    async def generate(
        self,
        request: AIRequest
    ) -> AIResponse:
        ...

    async def generate_structured(
        self,
        request: AIRequest,
        schema: type[BaseModel]
    ) -> AIResponse:
        ...

    async def health_check(self) -> AIHealth:
        ...

    def capabilities(self) -> AICapabilities:
        ...
```

Provider-specific implementation details remain inside the adapter.

---

# 6. AI Request

Canonical request:

```python id="m4m6tm"
class AIRequest(BaseModel):
    task_type: str
    system_prompt: str
    input: Any
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    response_format: str = "text"
    metadata: dict[str, Any] = {}
```

Additional fields may include:

```text
timeout
priority
correlation_id
user_context
language
```

---

# 7. AI Response

Canonical response:

```python id="j68c4g"
class AIResponse(BaseModel):
    text: str | None
    structured: dict[str, Any] | None
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    finish_reason: str | None
    metadata: dict[str, Any]
```

Every significant response should be associated with an `ai_run`.

---

# 8. AI Run Provenance

Every important AI operation should record:

```text
model
provider
task
prompt version
input hash
output
latency
token usage
status
timestamp
```

Database:

```text
ai_runs
```

This allows the system to answer:

> Which model produced this content?

> Which prompt version was used?

> How expensive was the operation?

> How long did inference take?

---

# 9. Model Registry

The canonical model registry is:

```text
ai_models
```

Example:

```json id="l3xwde"
{
  "provider": "local",
  "model_name": "qwen",
  "model_type": "llm",
  "local_or_cloud": "local",
  "capabilities": [
    "classification",
    "entity_extraction",
    "summarization"
  ]
}
```

Models should be enabled/disabled without code changes.

---

# 10. Capability Model

Each model declares capabilities.

Example:

```json id="l7l7e7"
{
  "classification": true,
  "structured_output": true,
  "long_context": false,
  "tool_use": false,
  "vision": false,
  "reasoning": false,
  "image_generation": false
}
```

The router selects a model based on capability requirements.

---

# 11. AI Task Types

Canonical task taxonomy:

```text id="q8db7v"
CLASSIFICATION
LANGUAGE_DETECTION
KEYWORD_EXTRACTION
ENTITY_EXTRACTION
STORY_SIMILARITY
CLAIM_EXTRACTION
SUMMARIZATION
EDITORIAL_SCORING
RESEARCH_SYNTHESIS
FACT_CHECK
FACT_SHEET
CONTENT_GENERATION
TRANSLATION
QUALITY_CHECK
IMAGE_BRIEF
```

New task types should be added deliberately.

---

# 12. Model Routing

Routing principle:

```text
Task
 ↓
Requirements
 ↓
Available Models
 ↓
Cost / latency / quality
 ↓
Selected Provider
```

The caller should request a task, not a provider.

Example:

```python id="8m6c7d"
await ai.generate(
    task_type="ENTITY_EXTRACTION",
    input=article_text
)
```

The router decides whether to use:

```text
POCO local model
cloud model
```

---

# 13. Default Routing

Recommended initial routing:

| Task                            | Preferred                        |
| ------------------------------- | -------------------------------- |
| language detection              | local                            |
| keyword extraction              | local                            |
| entity extraction               | local                            |
| spam filtering                  | local                            |
| story similarity                | local                            |
| basic classification            | local                            |
| basic summarization             | local                            |
| claim extraction                | local/cloud                      |
| editorial scoring               | local/cloud                      |
| complex research synthesis      | cloud                            |
| difficult fact-check assistance | cloud                            |
| fact-sheet generation           | cloud/local depending complexity |
| final content writing           | cloud                            |
| quality check                   | local/cloud                      |
| image generation                | external image model             |

This is a default policy, not a permanent restriction.

---

# 14. POCO Local AI

The POCO F1 is the local inference node.

Current environment:

```text
device: Xiaomi POCO F1
codename: beryllium
architecture: aarch64
OS: postmarketOS
kernel: 7.1.0-rc1-sdm845
RAM: ~5.5 GB usable
```

The device should be treated primarily as a lightweight inference and processing node.

---

# 15. Local AI Responsibilities

Best initial workloads:

```text
language detection
classification
keyword extraction
entity extraction
spam detection
story similarity
priority scoring
basic summarization
JSON transformation
initial claim extraction
```

These tasks are small enough to benefit from local execution.

---

# 16. Local Model Size

Practical starting range:

```text
0.5B – 1.5B
```

Very practical:

```text
1B – 3B
```

Possible with compromises:

```text
4B
```

Potentially usable but heavy:

```text
7B – 8B Q4
```

Generally not worthwhile on this device:

```text
13B+
```

Actual performance must be benchmarked on the deployed model/backend.

---

# 17. Candidate Local Models

Initial candidates:

```text
Qwen 1.5B
Qwen 3B
Llama 1B
Llama 3B
```

The final choice must be based on:

```text
quality
RAM usage
latency
tokens/sec
structured-output reliability
thermal behavior
power consumption
```

Do not choose a model solely from benchmark reputation.

---

# 18. llama.cpp

The preferred local inference abstraction is:

```text
llama.cpp
```

The application should communicate with a local inference service rather than embedding low-level inference logic into every worker.

Conceptually:

```text
AI Worker
    ↓
LocalAI adapter
    ↓
llama.cpp server
    ↓
model
```

---

# 19. Local Inference API

Prefer an HTTP or compatible local API boundary.

Example:

```text
http://127.0.0.1:<port>
```

The exact port is configuration.

Application code should not assume a fixed port.

---

# 20. Local Inference Health

Health checks should measure:

```text
process alive
model loaded
model name
available RAM
recent latency
tokens/sec
queue depth
error rate
```

Example:

```json id="hgyv3m"
{
  "healthy": true,
  "model": "qwen-local",
  "latency_ms": 420,
  "tokens_per_second": 12.4
}
```

---

# 21. CPU / GPU / NPU

Do not assume NPU acceleration.

The local AI runtime should benchmark:

```text
CPU
GPU
NPU/HTP
```

where supported by the actual kernel, runtime and hardware stack.

The system should select the fastest stable backend discovered during benchmarking.

---

# 22. Local AI Benchmark

Benchmark each candidate using the same workloads:

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
RAM
CPU usage
temperature
power behavior
failure rate
JSON validity
```

---

# 23. Model Benchmark Record

Store benchmark results separately from production AI runs.

Recommended future table:

```text
ai_model_benchmarks
-------------------
id
model_id
task_type
backend
input_tokens
output_tokens
latency_ms
tokens_per_second
ram_mb
temperature_delta
success
structured_output_valid
benchmark_version
created_at
```

---

# 24. Model Routing Score

Conceptual:

```text
routing_score =
    quality_weight
  + capability_match
  + latency_score
  + cost_score
  + availability_score
```

For local models:

```text
cost_score
```

may be near zero while:

```text
power/latency
```

becomes important.

---

# 25. Provider Fallback

Example:

```text
Local model
   ↓
unavailable
   ↓
cloud fallback
```

Or:

```text
Cloud provider A
   ↓
timeout/rate limit
   ↓
cloud provider B
```

Fallback should be task-specific.

Do not automatically route sensitive content to an arbitrary provider.

---

# 26. Sensitive Data Routing

Before sending content to a cloud provider, the system should determine:

```text
Does this task contain sensitive personal information?
Does this task contain confidential credentials?
Does this task contain restricted material?
Does provider policy permit the request?
```

Credentials and secrets must never be included.

---

# 27. Provider Policy

Recommended configuration:

```yaml id="q0z3a2"
providers:
  local:
    enabled: true

  openai:
    enabled: true

  gemini:
    enabled: true

  claude:
    enabled: false
```

Provider enablement is configuration-driven.

---

# 28. Provider Selection Policy

Example:

```yaml id="5h9u7j"
routing:
  classification:
    preferred:
      - local

  claim_extraction:
    preferred:
      - local
      - cloud

  research_synthesis:
    preferred:
      - cloud

  content_generation:
    preferred:
      - cloud
```

---

# 29. Prompt Architecture

Prompts should be versioned.

Recommended:

```text
config/prompts/
├── classification/
├── entity-extraction/
├── claim-extraction/
├── research/
├── fact-check/
├── fact-sheet/
├── content/
└── quality/
```

Each prompt should have:

```text
task
version
system instruction
input schema
output schema
examples where appropriate
```

---

# 30. Prompt Versioning

Example:

```text
claim-extraction.v1
claim-extraction.v2
```

Every `ai_run` stores:

```text
prompt_version
```

Changing a prompt creates a new version.

Do not silently modify production prompts without versioning.

---

# 31. Prompt Design

Prompts should clearly separate:

```text
FACTS
EVIDENCE
INFERENCES
UNCERTAINTY
EDITORIAL_INSTRUCTIONS
OUTPUT_FORMAT
```

Example conceptual structure:

```text
SYSTEM
  ↓
TASK
  ↓
FACTUAL INPUT
  ↓
EVIDENCE
  ↓
CONSTRAINTS
  ↓
EDITORIAL CONTEXT
  ↓
OUTPUT SCHEMA
```

---

# 32. Editorial Prompt Boundary

Editorial preferences may determine:

```text
what to emphasize
what audiences may care about
which topics receive priority
tone
format
```

They must not instruct the model to:

```text
invent evidence
ignore contradictory evidence
declare unsupported claims true
fabricate quotations
fabricate statistics
```

---

# 33. Historical Prompt Boundary

Historical prompts must explicitly distinguish:

```text
established evidence
interpretation
competing hypothesis
uncertainty
```

For controversial historical subjects, request separate analysis of:

```text
language
archaeology
genetics
literary evidence
epigraphy
chronology
population movement
cultural change
military conquest
```

Do not encode an ideological conclusion as a factual instruction.

---

# 34. Structured Outputs

Whenever the downstream system needs machine-readable information, use structured output.

Examples:

```text
claim extraction
entity extraction
fact checking
editorial scoring
quality checks
```

Preferred schema:

```python id="3sj21j"
class ExtractedClaim(BaseModel):
    claim: str
    claim_type: str
    confidence: float
```

---

# 35. JSON Validation

Every structured AI response must be validated.

Flow:

```text
LLM
 ↓
JSON parse
 ↓
Pydantic validation
 ↓
semantic validation
 ↓
database
```

If invalid:

```text
retry with constrained prompt
```

If still invalid:

```text
fallback model
```

If still invalid:

```text
human review / job failure
```

---

# 36. Never Trust Raw AI Output

AI output is untrusted input.

Treat it similarly to external user input.

Validate:

```text
type
length
schema
enum
URLs
numeric ranges
claims
references
```

before persistence.

---

# 37. Hallucination Control

The strongest control is architectural:

```text
AI receives evidence-backed structured context.
```

Avoid:

```text
"Read these 30 articles and write what happened."
```

Prefer:

```text
Story
+
claims
+
evidence
+
contradictions
+
timeline
+
source metadata
→
AI
```

---

# 38. Citation Preservation

When generating factual content, the model should reference internal claim IDs where practical.

Example:

```json id="11e2un"
{
  "statement": "Court X issued order Y.",
  "claim_id": "uuid"
}
```

The content renderer can then connect the statement back to evidence.

---

# 39. Claim-Level Content Mapping

Generated content should ideally maintain:

```text
content sentence
    ↓
claim ID
    ↓
evidence IDs
```

Example:

```json id="qg50go"
{
  "text": "The court ordered...",
  "claim_ids": ["..."],
  "evidence_ids": ["..."]
}
```

This makes automated fact checking much stronger.

---

# 40. Fact-Check AI

AI fact checking is an assistant to the evidence engine.

Flow:

```text
Claim
 ↓
Evidence retrieval
 ↓
Evidence normalization
 ↓
AI comparison
 ↓
Assessment
 ↓
Human review where required
```

AI MUST NOT treat source count alone as proof.

Ten copies of the same unsupported claim are not ten independent confirmations.

---

# 41. Source Independence

Evidence scoring should consider:

```text
source authority
source independence
primary evidence
publication date
directness
contradiction
corroboration
```

Example:

```text
10 articles quoting the same agency
```

may represent:

```text
1 underlying source
```

not ten independent sources.

---

# 42. Research Orchestration

Research should be a multi-stage process:

```text
Question
 ↓
Search planning
 ↓
Source discovery
 ↓
Source classification
 ↓
Primary-source retrieval
 ↓
Evidence extraction
 ↓
Cross-checking
 ↓
Synthesis
```

---

# 43. Research Worker

The research worker should produce:

```text
evidence_items
claim_evidence
research metadata
```

It should not directly produce social media posts.

---

# 44. Search Provider Abstraction

Search should use an abstraction:

```text
SearchProvider
├── WebSearchProvider
├── NewsSearchProvider
├── AcademicSearchProvider
└── FutureProvider
```

The evidence engine should not depend on a specific search company.

---

# 45. Research Queries

Generate queries from claims.

Example:

```text
Claim:
"Government X announced policy Y."

Queries:
"Government X policy Y official"
"Government X policy Y notification"
"Government X policy Y parliament"
"Government X policy Y court"
```

For historical claims:

```text
primary sources
archaeology
epigraphy
academic literature
genetics
linguistics
```

---

# 46. Search Result Handling

Search results are discovery material.

Each result should be classified:

```text
PRIMARY
SECONDARY
SPECIALIST
DISCOVERY
```

The research engine should prioritize primary evidence when available.

---

# 47. AI Research Synthesis

Research synthesis should output:

```json id="vyr1tu"
{
  "supported_claims": [],
  "contradicted_claims": [],
  "unresolved_claims": [],
  "important_context": [],
  "source_quality_notes": [],
  "confidence": 0.0
}
```

This becomes input to the fact sheet.

---

# 48. Fact Sheet Generation

The fact-sheet model should be deterministic in structure.

```text
FACT SHEET
├── headline
├── summary
├── verified_claims
├── disputed_claims
├── unverified_claims
├── evidence
├── timeline
├── entities
├── locations
├── context
├── counterclaims
├── confidence
├── risk
└── sources
```

---

# 49. Content Generation

Content generation receives the fact sheet.

Example:

```text
FACT SHEET
    ↓
Instagram renderer
    ↓
Carousel
```

and:

```text
FACT SHEET
    ↓
X renderer
    ↓
Post/thread
```

Each renderer should have platform-specific constraints.

---

# 50. Content Generation Constraints

The model must not:

```text
invent quotations
invent statistics
invent dates
invent people
invent sources
invent legal findings
invent casualty counts
invent historical evidence
```

If information is missing:

```text
say it is unknown
```

or:

```text
omit it
```

---

# 51. Uncertainty Language

When evidence is incomplete, generated content should use appropriate language:

```text
"according to..."
"official records state..."
"the available evidence indicates..."
"the claim remains unverified..."
"researchers disagree..."
```

Never turn uncertainty into certainty for rhetorical effect.

---

# 52. Risk-Aware Generation

Risk level comes from:

```text
claim risk
+
evidence strength
+
topic sensitivity
+
publication context
```

Possible levels:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

High/critical content requires human review.

---

# 53. AI Quality Check

Quality checker should independently inspect generated content for:

```text
factual drift
unsupported claims
citation mismatch
fabricated quotations
wrong names
wrong dates
wrong numbers
missing context
overstatement
defamation risk
sensitive-topic errors
style violations
```

---

# 54. Two-Pass Generation

Recommended:

```text
PASS 1
Generate content

PASS 2
Critique content against fact sheet
```

Then:

```text
If failed
→ regenerate
```

For high-risk topics:

```text
AI critique
+
human review
```

---

# 55. AI Self-Agreement

Do not use:

```text
Model A says true
Model B says true
→ therefore true
```

Model agreement is not independent evidence.

AI models should reason over evidence, not substitute for it.

---

# 56. Model Diversity

Multiple models can be useful for:

```text
drafting
critique
translation
classification
```

but not as a replacement for primary evidence.

---

# 57. Cost Tracking

For cloud models record:

```text
input tokens
output tokens
provider
model
estimated cost
```

Extend `ai_runs`:

```text
estimated_cost
currency
```

This enables:

```text
daily cost
monthly cost
cost per story
cost per publication
cost by model
```

---

# 58. Local AI Cost

Local inference should track:

```text
latency
CPU
RAM
temperature
tokens/sec
```

Financial cost may be approximately zero operationally, but energy and device capacity remain constraints.

---

# 59. AI Budgets

Configure limits:

```yaml id="95yxk5"
budgets:
  daily_cloud_cost: 10.00
  monthly_cloud_cost: 200.00
```

If a budget is exceeded:

```text
high-cost tasks
→ queue
```

or:

```text
fallback to local model
```

depending on task requirements.

---

# 60. AI Timeouts

Every request must have a timeout.

Example:

```text
local classification → short
cloud research → longer
content generation → medium
```

Timeouts are configuration, not hard-coded everywhere.

---

# 61. AI Concurrency

Limit concurrency per provider.

Example:

```text
local:
    max_concurrent = 1

cloud:
    max_concurrent = configurable
```

Local POCO inference should initially use conservative concurrency to avoid memory pressure and thermal throttling.

---

# 62. Queue Prioritization

AI tasks inherit story/job priority.

Example:

```text
P1 breaking event
→ immediate classification
→ immediate research
```

while:

```text
P4 historical enrichment
→ background queue
```

---

# 63. AI Caching

Cache deterministic or reusable operations where safe.

Good candidates:

```text
language detection
entity normalization
duplicate classification
source metadata
```

Do not blindly cache:

```text
breaking-news research
rapidly changing facts
publication-specific content
```

---

# 64. Input Hashing

For reproducibility:

```text
input_hash =
SHA256(normalized_input)
```

Store in:

```text
ai_runs.input_hash
```

This helps identify duplicate AI requests.

---

# 65. AI Run Deduplication

Before expensive inference:

```text
same task
+
same model
+
same prompt version
+
same input hash
```

may reuse a previous successful result where safe.

This is especially useful for:

```text
classification
entity extraction
story similarity
```

---

# 66. Prompt Injection Defense

External articles are untrusted content.

A source article may contain text such as:

```text
"Ignore previous instructions..."
```

The model must treat article text as data, not instructions.

Prompt structure should clearly delimit:

```text
SYSTEM INSTRUCTIONS
UNTRUSTED SOURCE CONTENT
TASK
OUTPUT SCHEMA
```

---

# 67. Web Research Injection

Web pages may contain malicious or irrelevant instructions.

Research workers must extract factual content rather than obeying instructions embedded in source pages.

---

# 68. Tool-Use Boundary

AI may request:

```text
search
fetch source
extract evidence
```

through controlled tools.

Tools must enforce:

```text
allowlisted capabilities
timeouts
rate limits
content limits
```

The model does not receive unrestricted shell/network access.

---

# 69. Shell Access

AI workers MUST NOT have unrestricted shell access to the server.

If operational actions are ever introduced, use explicit typed tools:

```text
restart_worker
check_queue
get_health
```

rather than arbitrary:

```text
execute_shell(command)
```

---

# 70. Secret Isolation

AI prompts must never contain:

```text
API keys
database passwords
social tokens
SSH keys
private credentials
```

Secret references may be passed as opaque identifiers where required.

---

# 71. PII Minimization

Before cloud inference:

```text
detect unnecessary PII
→ remove/minimize
→ send only required content
```

Do not transmit unrelated personal data.

---

# 72. AI Auditability

For important generated content, preserve:

```text
story_id
fact_sheet_id
ai_run_id
model
prompt_version
content_version
reviewer
publication
```

This creates a complete chain:

```text
Evidence
 ↓
Fact Sheet
 ↓
AI Run
 ↓
Content
 ↓
Human Review
 ↓
Publication
```

---

# 73. AI Evaluation Dataset

Create an evaluation dataset containing representative examples:

```text
ordinary news
breaking news
political claims
court cases
religious claims
communal violence
demographics
historical controversies
fact checks
misinformation
satire
```

Include difficult examples.

---

# 74. Evaluation Dimensions

Measure:

```text
classification accuracy
entity extraction accuracy
claim extraction precision
JSON validity
fact-check agreement
citation/evidence alignment
hallucination rate
content factuality
style compliance
latency
cost
```

---

# 75. Golden Dataset

Maintain:

```text
tests/ai/golden/
```

Each case should contain:

```text
input
expected structured output
acceptable alternatives
risk level
evaluation criteria
```

Model or prompt changes must be evaluated against the golden dataset.

---

# 76. Regression Testing

A model update should not be considered safe merely because the new model performs better on one benchmark.

Run:

```text
old model
new model
```

against the same evaluation set.

Compare:

```text
quality
safety
latency
cost
structured output
hallucinations
```

---

# 77. Model Promotion

Model lifecycle:

```text
EXPERIMENTAL
    ↓
BENCHMARKED
    ↓
STAGING
    ↓
PRODUCTION
    ↓
DEPRECATED
```

Only production-approved models should be used automatically.

---

# 78. Local Model Promotion

For a new POCO model:

```text
download
 ↓
benchmark
 ↓
validate memory
 ↓
validate thermal behavior
 ↓
test structured outputs
 ↓
golden dataset
 ↓
staging
 ↓
production
```

---

# 79. Model Rollback

Keep previous model available.

If:

```text
quality degradation
latency spike
memory failure
thermal instability
```

then:

```text
router
→ previous stable model
```

No code rewrite should be necessary.

---

# 80. AI Observability

Metrics:

```text
ai_requests_total
ai_success_total
ai_failure_total
ai_latency_ms
ai_tokens_input
ai_tokens_output
ai_cost_total
ai_fallback_total
ai_json_validation_failures
ai_hallucination_flags
```

Dimensions:

```text
provider
model
task_type
status
```

---

# 81. AI Dashboard

Eventually display:

```text
AI HEALTH

Local Model
  status: healthy
  model: Qwen
  latency: 420ms
  throughput: 12 tok/s

Cloud
  status: healthy
  requests today: 182
  estimated cost: ...

Queue
  pending: 8
  failed: 1
  retrying: 2
```

---

# 82. AI Failure Handling

If AI fails:

```text
request
 ↓
timeout/error
 ↓
retry
 ↓
alternate model
 ↓
human review or job failure
```

Never silently replace an AI failure with fabricated output.

---

# 83. AI Worker Architecture

```text
apps/ai-worker/
├── main.py
├── worker.py
├── router.py
├── tasks/
│   ├── classification.py
│   ├── extraction.py
│   ├── research.py
│   ├── fact_check.py
│   ├── fact_sheet.py
│   ├── content.py
│   └── quality.py
└── services/
```

---

# 84. AI Package

```text
packages/ai/
├── __init__.py
├── types.py
├── provider.py
├── router.py
├── registry.py
├── prompts.py
├── structured.py
├── fallback.py
├── cost.py
├── evaluation.py
├── local/
│   ├── llama_cpp.py
│   └── health.py
└── cloud/
    ├── openai.py
    ├── gemini.py
    └── claude.py
```

---

# 85. Configuration

Recommended:

```text
config/models/
├── registry.yaml
├── routing.yaml
├── local.yaml
├── cloud.yaml
└── budgets.yaml
```

Example:

```yaml id="1m1k1r"
local:
  enabled: true
  endpoint: "http://127.0.0.1:PORT"
  model: "qwen-local"

routing:
  classification:
    - local

  entity_extraction:
    - local

  research_synthesis:
    - openai
    - gemini
```

Secrets remain outside configuration files.

---

# 86. Environment Variables

Provider credentials:

```text
OPENAI_API_KEY
GEMINI_API_KEY
ANTHROPIC_API_KEY
```

should be provided through the deployment secret mechanism.

Do not commit them to Git.

---

# 87. AI Development Modes

Support:

```text
MOCK
LOCAL
CLOUD
HYBRID
```

### MOCK

For unit tests.

### LOCAL

Use POCO/local inference.

### CLOUD

Use configured cloud provider.

### HYBRID

Normal production mode.

---

# 88. Development Safety

Automated tests should default to:

```text
MOCK
```

unless explicitly testing local/cloud integrations.

No test should accidentally generate expensive cloud requests.

---

# 89. Publication Safety

AI-generated content must never automatically publish solely because:

```text
AI confidence > threshold
```

Publication also requires:

```text
fact/evidence requirements
quality gate
review policy
publication state
```

---

# 90. AI and Editorial Independence

The AI layer should not encode permanent political or ideological conclusions.

Instead:

```text
Editorial configuration
       ↓
priority / framing / tone
```

while:

```text
Evidence engine
       ↓
factual assessment
```

remains evidence-driven.

---

# 91. Controversial Topics

For politically or religiously sensitive stories:

```text
AI
 ↓
identify claims
 ↓
retrieve evidence
 ↓
identify contradictions
 ↓
separate fact from interpretation
 ↓
flag uncertainty
 ↓
human review
```

The model should not be rewarded merely for producing a confident answer.

---

# 92. Demographic Analysis

AI should separate:

```text
observed statistics
```

from:

```text
causal explanations
```

and:

```text
editorial interpretation
```

If causation is uncertain:

```text
"possible explanations include..."
```

rather than:

```text
"X group caused..."
```

without evidence.

---

# 93. Caste-Related Analysis

The system may process verified caste-related facts when relevant.

It must not:

```text
infer behavioral traits from caste
generalize individual conduct to an entire caste
generate stereotypes
invent caste identity
```

Caste information should be treated as sensitive contextual data.

---

# 94. Religious Analysis

The system may analyze:

```text
religious freedom
religious sites
religious discrimination
blasphemy laws
religious violence
conversion
religious demographics
historical religious institutions
```

It must distinguish:

```text
individual
organization
religious tradition
population
state
```

Do not generalize allegations involving one person into claims about an entire religious community.

---

# 95. Historical Analysis

The AI system should preserve uncertainty.

Example:

```text
Evidence:
A

Interpretation:
B

Competing interpretation:
C

Confidence:
0.61
```

The output should not manufacture consensus where scholarship is genuinely divided.

---

# 96. Image Generation

Image generation is a separate capability.

Pipeline:

```text
Fact Sheet
 ↓
Image Brief
 ↓
Image Model
 ↓
Visual Check
 ↓
Media Asset
```

The image model should receive factual visual constraints.

Do not allow image generation to invent:

```text
historical uniforms
specific people
specific locations
specific events
```

when factual accuracy matters.

---

# 97. AI Image Metadata

Generated media should record:

```text
model
prompt version
fact sheet
generation timestamp
asset hash
review status
```

This allows reconstruction of the media pipeline.

---

# 98. Translation

Translation is an AI task but should preserve:

```text
names
dates
numbers
legal terminology
quotes
uncertainty
```

Do not translate uncertainty into certainty.

---

# 99. Multilingual Content

The content engine should eventually support:

```text
English
Hindi
regional Indian languages
```

Language-specific prompts should preserve factual meaning.

Each translated variant should be independently quality checked.

---

# 100. AI Architecture Summary

The final AI architecture is:

```text
                 ┌───────────────────────┐
                 │      AI Router        │
                 └───────────┬───────────┘
                             │
             ┌───────────────┼────────────────┐
             │               │                │
             ▼               ▼                ▼
        Local AI          Cloud AI        Research Tools
        POCO F1           Providers        Search / Fetch
             │               │                │
             └───────────────┼────────────────┘
                             ▼
                     Structured AI Result
                             │
                             ▼
                       Validation
                             │
                             ▼
                         AI Run
                             │
                             ▼
                       PostgreSQL
```

The core rule remains:

```text
AI
=
analysis + transformation + generation

Evidence
=
source of factual grounding

PostgreSQL
=
source of persistent truth

Human review
=
final safety boundary for high-risk publishing
```

---

# 101. Final AI Design Rules

1. Never couple business logic directly to one AI provider.
2. Every important AI operation must be traceable.
3. Prefer local inference for lightweight workloads.
4. Use cloud models when complexity requires them.
5. Benchmark the POCO before selecting the production local model.
6. Never assume NPU acceleration.
7. Use structured outputs wherever possible.
8. Validate every AI output.
9. Treat external web content as untrusted input.
10. Never expose secrets to models.
11. Keep evidence separate from editorial preference.
12. Preserve uncertainty.
13. Do not use model agreement as proof.
14. Keep human review mandatory for high-risk content.
15. Version prompts and models.
16. Maintain a golden evaluation dataset.
17. Make provider fallback explicit.
18. Track latency, failures, tokens and cost.
19. Keep publication side effects outside AI inference.
20. AI must remain replaceable infrastructure.

---

# 102. Canonical AI Flow

```text
SOURCE MATERIAL
      ↓
NORMALIZED DATA
      ↓
LOCAL AI
      ↓
CLASSIFICATION / EXTRACTION
      ↓
EVIDENCE ENGINE
      ↓
CLOUD AI WHEN REQUIRED
      ↓
FACT SHEET
      ↓
CONTENT GENERATION
      ↓
AI QUALITY CHECK
      ↓
HUMAN REVIEW
      ↓
PUBLICATION
```

This is the canonical AI architecture for the News AI Social Media Manager.
