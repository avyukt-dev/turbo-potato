"""Typed production AI-provider configuration and composition."""

from __future__ import annotations

import os
from collections.abc import Iterable

from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field

from .llama_cpp import LlamaCppProvider, LlamaCppProviderConfig
from .provider import AIProvider
from .registry import AIProviderRegistry
from .routing import AIRouter, AIRoutingConfigLoader


class AIProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    llama_cpp: tuple[LlamaCppProviderConfig, ...] = ()


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
        for item in configured.llama_cpp:
            secret = os.getenv(item.api_key_env) if item.api_key_env else None
            built.append(LlamaCppProvider(item, api_key=secret))
        providers = built
    registry = AIProviderRegistry(providers)
    return AIRouter(registry, AIRoutingConfigLoader(loader).load())
