"""Typed multi-provider/model configuration and production composition."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Annotated, Literal

from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .credentials import (
    CredentialPoolConfig,
    DatabaseCredentialPool,
    MemoryCredentialPool,
    resolve_credential_pool,
)
from .gemini import GeminiProvider, GeminiProviderConfig
from .groq import GroqProvider, GroqProviderConfig
from .llama_cpp import LlamaCppProvider, LlamaCppProviderConfig
from .openai_provider import OpenAIProvider, OpenAIProviderConfig
from .provider import AIProvider
from .registry import AIProviderRegistry
from .routing import AIPolicyConfigLoader, AIRouter, AIStageConfigLoader


class ConfiguredLlamaCppProvider(LlamaCppProviderConfig):
    adapter_type: Literal["llama_cpp"]


class ConfiguredGroqProvider(GroqProviderConfig):
    adapter_type: Literal["groq"]


class ConfiguredOpenAIProvider(OpenAIProviderConfig):
    adapter_type: Literal["openai"]


class ConfiguredGeminiProvider(GeminiProviderConfig):
    adapter_type: Literal["gemini"]


ConfiguredAIProvider = Annotated[
    ConfiguredLlamaCppProvider
    | ConfiguredGroqProvider
    | ConfiguredOpenAIProvider
    | ConfiguredGeminiProvider,
    Field(discriminator="adapter_type"),
]


class AIProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=2, ge=2, le=2)
    credential_pools: tuple[CredentialPoolConfig, ...] = ()
    providers: tuple[ConfiguredAIProvider, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> AIProvidersConfig:
        if not self.providers:
            raise ValueError("at least one AI provider must be configured")
        provider_ids = [item.provider_id for item in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("configured AI provider IDs must be unique")
        pool_ids = [item.pool_id for item in self.credential_pools]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("configured credential pool IDs must be unique")
        pools = {item.pool_id: item for item in self.credential_pools}
        for provider in self.providers:
            pool_id = provider.credential_pool_id
            if isinstance(provider, ConfiguredLlamaCppProvider) and pool_id is None:
                continue
            if pool_id is None or pool_id not in pools:
                raise ValueError(
                    f"provider {provider.provider_id!r} references an unknown credential pool"
                )
            if pools[pool_id].provider != provider.adapter_type:
                raise ValueError("credential pool provider must match adapter type")
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
    session_factory: Callable[[], Session] | None = None,
) -> AIRouter:
    """Build production adapters; injected providers retain the same registry contract."""

    if providers is None:
        configured = AIProvidersConfigLoader(loader).load()
        pool_configs = {item.pool_id: item for item in configured.credential_pools}
        built: list[AIProvider] = []

        def pool_for(pool_id: str):
            config = pool_configs[pool_id]
            credentials = resolve_credential_pool(config)
            if session_factory is None:
                return MemoryCredentialPool(config, credentials)
            return DatabaseCredentialPool(config, credentials, session_factory)

        for item in configured.providers:
            values = item.model_dump(exclude={"adapter_type"})
            if isinstance(item, ConfiguredLlamaCppProvider):
                secret = None
                if item.credential_pool_id is not None:
                    resolved = resolve_credential_pool(pool_configs[item.credential_pool_id])
                    if len(resolved) != 1:
                        raise ValueError(
                            "llama.cpp currently accepts exactly one configured credential"
                        )
                    secret = resolved[0].secret
                built.append(
                    LlamaCppProvider(LlamaCppProviderConfig.model_validate(values), api_key=secret)
                )
            elif isinstance(item, ConfiguredGroqProvider):
                assert item.credential_pool_id is not None
                built.append(
                    GroqProvider(
                        GroqProviderConfig.model_validate(values),
                        credential_pool=pool_for(item.credential_pool_id),
                    )
                )
            elif isinstance(item, ConfiguredOpenAIProvider):
                built.append(
                    OpenAIProvider(
                        OpenAIProviderConfig.model_validate(values),
                        pool_for(item.credential_pool_id),
                    )
                )
            else:
                built.append(
                    GeminiProvider(
                        GeminiProviderConfig.model_validate(values),
                        pool_for(item.credential_pool_id),
                    )
                )
        providers = built
    return AIRouter(
        AIProviderRegistry(providers),
        AIPolicyConfigLoader(loader).load(),
        AIStageConfigLoader(loader).load_all(),
    )
