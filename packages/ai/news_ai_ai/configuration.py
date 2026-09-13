"""Typed production AI-provider configuration and composition."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Literal

from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .llama_cpp import LlamaCppProvider, LlamaCppProviderConfig
from .provider import AIProvider
from .registry import AIProviderRegistry
from .routing import AIPolicyConfigLoader, AIRouter, AIStageConfigLoader


class ConfiguredLlamaCppProvider(LlamaCppProviderConfig):
    """Closed provider-factory input for the llama.cpp adapter."""

    adapter_type: Literal["llama_cpp"]


class AIProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    providers: tuple[ConfiguredLlamaCppProvider, ...]

    @model_validator(mode="after")
    def require_unique_providers(self) -> AIProvidersConfig:
        if not self.providers:
            raise ValueError("at least one AI provider must be configured")
        ids = self.provider_ids
        if len(ids) != len(set(ids)):
            raise ValueError("configured AI provider IDs must be unique")
        return self

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(item.provider_id for item in self.providers)


class AIProvidersConfigLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> AIProvidersConfig:
        return self.loader.load_domain_file(
            ConfigDomain.MODELS, "providers.yaml", AIProvidersConfig
        )


def build_ai_router(
    loader: ConfigLoader,
    *,
    providers: Iterable[AIProvider] | None = None,
) -> AIRouter:
    """Build the configured router; injected providers use the identical registry boundary."""

    if providers is None:
        configured = AIProvidersConfigLoader(loader).load()
        built: list[AIProvider] = []
        for item in configured.providers:
            secret = os.getenv(item.api_key_env) if item.api_key_env else None
            built.append(
                LlamaCppProvider(
                    LlamaCppProviderConfig.model_validate(
                        item.model_dump(exclude={"adapter_type"})
                    ),
                    api_key=secret,
                )
            )
        providers = built
    registry = AIProviderRegistry(providers)
    return AIRouter(
        registry,
        AIPolicyConfigLoader(loader).load(),
        AIStageConfigLoader(loader).load_all(),
    )
