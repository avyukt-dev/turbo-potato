"""Provider-neutral image generation through official provider SDKs."""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

import openai
from google import genai
from google.genai import errors, types
from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .credentials import DatabaseCredentialPool, MemoryCredentialPool, resolve_credential_pool
from .pooled import CredentialPool
from .provider import (
    AIInvalidResponseError,
    AIProviderAuthenticationError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)

MAX_GENERATED_IMAGE_BYTES = 20 * 1024 * 1024


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt: str = Field(min_length=1, max_length=12000)
    aspect_ratio: Literal["4:5"] = "4:5"
    input_hash: str = Field(min_length=64, max_length=64)
    sensitivity: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_sensitivity(self) -> ImageGenerationRequest:
        if len(self.sensitivity) != len(set(self.sensitivity)):
            raise ValueError("image-generation sensitivity values must be unique")
        return self


@dataclass(frozen=True, slots=True)
class ImageGenerationResponse:
    image_bytes: bytes
    mime_type: str
    provider: str
    model: str
    latency_ms: int
    provider_request_id: str | None = None


class ImageGenerationProvider(Protocol):
    provider_id: str
    model: str

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResponse: ...


class OpenAIImageProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Literal["openai"] = "openai"
    adapter_type: Literal["openai_images"]
    model: str = "gpt-image-1.5"
    credential_pool_id: str = "openai-production"
    timeout_seconds: float = Field(default=180, gt=0, le=600)


class GeminiImageProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Literal["gemini"] = "gemini"
    adapter_type: Literal["gemini_images"]
    model: str = "gemini-3.1-flash-image"
    credential_pool_id: str = "gemini-production"
    timeout_seconds: float = Field(default=180, gt=0, le=600)


ConfiguredImageProvider = Annotated[
    OpenAIImageProviderConfig | GeminiImageProviderConfig, Field(discriminator="adapter_type")
]


class ImageProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[2] = 2
    methodology_version: str = Field(min_length=1, max_length=64)
    providers: tuple[ConfiguredImageProvider, ...]
    sensitivity_provider_allowlists: dict[str, frozenset[str]]

    @model_validator(mode="after")
    def unique_providers(self) -> ImageProvidersConfig:
        ids = tuple(item.provider_id for item in self.providers)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("image providers must be non-empty and unique")
        configured = set(ids)
        for sensitivity, providers in self.sensitivity_provider_allowlists.items():
            if not sensitivity.strip() or not providers or not providers <= configured:
                raise ValueError("image sensitivity allowlist is invalid")
        return self


class ImageProvidersConfigLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> ImageProvidersConfig:
        return self.loader.load_domain_file(
            ConfigDomain.MODELS, "image-providers.yaml", ImageProvidersConfig
        )


class _PooledImageProvider:
    def __init__(self, *, provider_id: str, model: str, credential_pool: CredentialPool) -> None:
        self.provider_id = provider_id
        self.model = model
        self.credential_pool = credential_pool

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        attempted: set[int] = set()
        last: AIProviderError | None = None
        while (credential := await self.credential_pool.acquire(attempted)) is not None:
            attempted.add(credential.slot)
            try:
                response = await self._generate(request, credential.secret)
            except AIProviderRateLimitError as exc:
                last = exc
                await self.credential_pool.record_rate_limit(credential, exc.retry_after_seconds)
                continue
            except AIProviderAuthenticationError as exc:
                last = exc
                await self.credential_pool.record_auth_failure(credential)
                continue
            await self.credential_pool.record_success(credential)
            return response
        if last is not None:
            raise last
        raise AIProviderRateLimitError(
            "No configured image credential is currently eligible",
            retry_after_seconds=await self.credential_pool.minimum_cooldown_remaining(),
        )

    async def _generate(
        self, request: ImageGenerationRequest, api_key: str
    ) -> ImageGenerationResponse:
        raise NotImplementedError

    @staticmethod
    def _decode(value: object) -> bytes:
        if not isinstance(value, str):
            raise AIInvalidResponseError("image provider omitted generated image bytes")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise AIInvalidResponseError("image provider returned invalid image bytes") from None
        if not decoded or len(decoded) > MAX_GENERATED_IMAGE_BYTES:
            raise AIInvalidResponseError("image provider returned an invalid image size")
        return decoded


class OpenAIImageProvider(_PooledImageProvider):
    def __init__(
        self,
        config: OpenAIImageProviderConfig,
        *,
        credential_pool: CredentialPool,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(
            provider_id=config.provider_id, model=config.model, credential_pool=credential_pool
        )
        self.config = config
        self._client_factory = client_factory or (
            lambda key: openai.AsyncOpenAI(
                api_key=key, timeout=config.timeout_seconds, max_retries=0
            )
        )

    async def _generate(
        self, request: ImageGenerationRequest, api_key: str
    ) -> ImageGenerationResponse:
        started = time.monotonic()
        try:
            response = await self._client_factory(api_key).images.generate(
                model=self.model,
                prompt=request.prompt,
                n=1,
                size="1024x1536",
                quality="high",
                output_format="jpeg",
            )
        except openai.AuthenticationError as exc:
            raise AIProviderAuthenticationError("OpenAI image credential was rejected") from exc
        except openai.PermissionDeniedError as exc:
            raise AIProviderPolicyError("OpenAI rejected image policy") from exc
        except openai.RateLimitError as exc:
            raise AIProviderRateLimitError("OpenAI image request was rate limited") from exc
        except openai.APITimeoutError as exc:
            raise AIProviderTimeoutError("OpenAI image request timed out") from exc
        except openai.APIConnectionError as exc:
            raise AIProviderUnavailableError("OpenAI image endpoint is unavailable") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("OpenAI image service is unavailable") from exc
            raise AIProviderError("OpenAI rejected image generation") from exc
        if len(response.data) != 1:
            raise AIInvalidResponseError("OpenAI returned an invalid image result")
        return ImageGenerationResponse(
            image_bytes=self._decode(response.data[0].b64_json),
            mime_type="image/jpeg",
            provider=self.provider_id,
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_request_id=getattr(response, "id", None),
        )


class GeminiImageProvider(_PooledImageProvider):
    def __init__(
        self,
        config: GeminiImageProviderConfig,
        *,
        credential_pool: CredentialPool,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(
            provider_id=config.provider_id, model=config.model, credential_pool=credential_pool
        )
        self.config = config
        self._client_factory = client_factory or (
            lambda key: genai.Client(
                api_key=key,
                http_options=types.HttpOptions(timeout=int(config.timeout_seconds * 1000)),
            )
        )

    async def _generate(
        self, request: ImageGenerationRequest, api_key: str
    ) -> ImageGenerationResponse:
        started = time.monotonic()
        try:
            response = await self._client_factory(api_key).aio.models.generate_content(
                model=self.model,
                contents=request.prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=request.aspect_ratio),
                ),
            )
        except errors.APIError as exc:
            if exc.code == 401:
                raise AIProviderAuthenticationError("Gemini image credential was rejected") from exc
            if exc.code == 403:
                raise AIProviderPolicyError("Gemini rejected image policy") from exc
            if exc.code == 429:
                raise AIProviderRateLimitError("Gemini image request was rate limited") from exc
            if exc.code in {408, 504}:
                raise AIProviderTimeoutError("Gemini image request timed out") from exc
            if exc.code >= 500:
                raise AIProviderUnavailableError("Gemini image service is unavailable") from exc
            raise AIProviderError("Gemini rejected image generation") from exc
        for part in reversed(response.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline is not None and not getattr(part, "thought", False):
                mime = inline.mime_type
                if mime not in {"image/png", "image/jpeg"}:
                    raise AIInvalidResponseError("Gemini returned an unsupported image format")
                raw = inline.data
                if not isinstance(raw, bytes) or not raw or len(raw) > MAX_GENERATED_IMAGE_BYTES:
                    raise AIInvalidResponseError("Gemini returned invalid image bytes")
                return ImageGenerationResponse(
                    image_bytes=raw,
                    mime_type=mime,
                    provider=self.provider_id,
                    model=self.model,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    provider_request_id=getattr(response, "response_id", None),
                )
        raise AIInvalidResponseError("Gemini omitted generated image bytes")


class ImageGenerationRouter:
    def __init__(
        self,
        providers: tuple[ImageGenerationProvider, ...],
        *,
        sensitivity_provider_allowlists: dict[str, frozenset[str]] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one image provider is required")
        self.providers = providers
        self.sensitivity_provider_allowlists = sensitivity_provider_allowlists or {}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        if any(item not in self.sensitivity_provider_allowlists for item in request.sensitivity):
            raise AIProviderPolicyError(
                "no image provider allowlist configured for requested sensitivity"
            )
        last: AIProviderError | None = None
        retryable = False
        eligible = 0
        for provider in self.providers:
            if any(
                provider.provider_id not in self.sensitivity_provider_allowlists[item]
                for item in request.sensitivity
            ):
                continue
            eligible += 1
            try:
                response = await provider.generate(request)
                if (
                    response.provider != provider.provider_id
                    or response.model != provider.model
                    or response.mime_type not in {"image/jpeg", "image/png"}
                    or not response.image_bytes
                    or len(response.image_bytes) > MAX_GENERATED_IMAGE_BYTES
                ):
                    raise AIInvalidResponseError(
                        "image provider returned inconsistent provenance or media"
                    )
                return response
            except AIProviderError as exc:
                last = exc
                retryable = retryable or isinstance(
                    exc,
                    (AIProviderRateLimitError, AIProviderTimeoutError, AIProviderUnavailableError),
                )
        if not eligible:
            raise AIProviderPolicyError(
                "no configured image provider is authorized for requested sensitivity"
            )
        if retryable:
            raise AIProviderUnavailableError("all configured image providers failed") from last
        assert last is not None
        raise last


def build_image_router(
    loader: ConfigLoader,
    *,
    providers: tuple[ImageGenerationProvider, ...] | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> tuple[ImageGenerationRouter, ImageProvidersConfig]:
    config = ImageProvidersConfigLoader(loader).load()
    if providers is None:
        from .configuration import AIProvidersConfigLoader

        pools = {
            item.pool_id: item for item in AIProvidersConfigLoader(loader).load().credential_pools
        }
        built: list[ImageGenerationProvider] = []
        for item in config.providers:
            pool_config = pools[item.credential_pool_id]
            resolved = resolve_credential_pool(pool_config)
            pool: CredentialPool = (
                DatabaseCredentialPool(pool_config, resolved, session_factory)
                if session_factory is not None
                else MemoryCredentialPool(pool_config, resolved)
            )
            built.append(
                OpenAIImageProvider(item, credential_pool=pool)
                if isinstance(item, OpenAIImageProviderConfig)
                else GeminiImageProvider(item, credential_pool=pool)
            )
        providers = tuple(built)
    return ImageGenerationRouter(
        providers, sensitivity_provider_allowlists=config.sensitivity_provider_allowlists
    ), config
