"""Closed provider model declarations shared by all adapter types."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import AIResponseFormat, AITaskType, ModelId


class ProviderModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: ModelId
    task_types: frozenset[AITaskType]
    response_formats: frozenset[AIResponseFormat] = frozenset(
        {AIResponseFormat.TEXT, AIResponseFormat.STRUCTURED}
    )
    max_context_tokens: int | None = Field(default=None, ge=1)
    default_max_completion_tokens: int = Field(default=4096, ge=1, le=131072)
    supports_vision: bool = False
    supports_tools: bool = False
    honors_reasoning_effort: bool = False

    @field_validator("task_types", "response_formats")
    @classmethod
    def require_capabilities(cls, value: frozenset) -> frozenset:
        if not value:
            raise ValueError("model capability declarations must not be empty")
        return value
