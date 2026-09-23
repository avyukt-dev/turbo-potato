"""Provider-neutral image generation with OpenAI and Gemini adapters."""

from __future__ import annotations

import base64
import binascii
import os
import time
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

import httpx
from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .provider import (
    AIInvalidResponseError,
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
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-image-1.5"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: float = Field(default=180, gt=0, le=600)


class GeminiImageProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Literal["gemini"] = "gemini"
    adapter_type: Literal["gemini_images"]
    base_url: str = "https://generativelanguage.googleapis.com/v1"
    model: str = "gemini-3.1-flash-image"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_seconds: float = Field(default=180, gt=0, le=600)


ConfiguredImageProvider = Annotated[
    OpenAIImageProviderConfig | GeminiImageProviderConfig,
    Field(discriminator="adapter_type"),
]


class ImageProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
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
            if not sensitivity.strip() or not providers:
                raise ValueError("image sensitivity allowlists must be non-empty")
            if not providers <= configured:
                raise ValueError("image sensitivity allowlist references unknown provider")
        return self


class ImageProvidersConfigLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> ImageProvidersConfig:
        return self.loader.load_domain_file(
            ConfigDomain.MODELS, "image-providers.yaml", ImageProvidersConfig
        )


class _HTTPImageProvider:
    def __init__(self, *, provider_id: str, model: str, api_key: str, timeout: float) -> None:
        if not api_key.strip():
            raise ValueError(f"{provider_id} image credential is unavailable")
        self.provider_id = provider_id
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

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

    async def _post(
        self, url: str, *, headers: dict[str, str], payload: dict
    ) -> tuple[httpx.Response, dict, int]:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            raise AIProviderTimeoutError("image provider request timed out") from None
        except httpx.HTTPError:
            raise AIProviderUnavailableError("image provider endpoint is unavailable") from None
        latency = int((time.monotonic() - started) * 1000)
        try:
            body = response.json()
        except ValueError:
            raise AIInvalidResponseError("image provider returned invalid JSON") from None
        if not isinstance(body, dict):
            raise AIInvalidResponseError("image provider returned an invalid response")
        if response.status_code == 429:
            raise AIProviderRateLimitError("image provider was rate limited")
        if response.status_code in {401, 403}:
            raise AIProviderPolicyError("image provider rejected credentials or policy")
        if response.status_code >= 500:
            raise AIProviderUnavailableError("image provider is temporarily unavailable")
        if response.status_code >= 400:
            raise AIProviderError("image provider rejected the generation request")
        return response, body, latency


class OpenAIImageProvider(_HTTPImageProvider):
    def __init__(self, config: OpenAIImageProviderConfig, *, api_key: str) -> None:
        super().__init__(
            provider_id=config.provider_id,
            model=config.model,
            api_key=api_key,
            timeout=config.timeout_seconds,
        )
        self._base_url = config.base_url.rstrip("/")

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        response, body, latency = await self._post(
            f"{self._base_url}/images/generations",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "model": self.model,
                "prompt": request.prompt,
                "n": 1,
                "size": "1024x1536",
                "quality": "high",
                "output_format": "jpeg",
            },
        )
        data = body.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise AIInvalidResponseError("OpenAI returned an invalid image result")
        return ImageGenerationResponse(
            image_bytes=self._decode(data[0].get("b64_json")),
            mime_type="image/jpeg",
            provider=self.provider_id,
            model=self.model,
            latency_ms=latency,
            provider_request_id=response.headers.get("x-request-id"),
        )


class GeminiImageProvider(_HTTPImageProvider):
    def __init__(self, config: GeminiImageProviderConfig, *, api_key: str) -> None:
        super().__init__(
            provider_id=config.provider_id,
            model=config.model,
            api_key=api_key,
            timeout=config.timeout_seconds,
        )
        self._base_url = config.base_url.rstrip("/")

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        response, body, latency = await self._post(
            f"{self._base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            payload={
                "contents": [{"parts": [{"text": request.prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": request.aspect_ratio},
                },
            },
        )
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise AIInvalidResponseError("Gemini returned no image candidate")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise AIInvalidResponseError("Gemini returned an invalid image result")
        for part in reversed(parts):
            inline = part.get("inlineData") if isinstance(part, dict) else None
            if isinstance(inline, dict) and not part.get("thought"):
                mime = inline.get("mimeType")
                if mime not in {"image/png", "image/jpeg"}:
                    raise AIInvalidResponseError("Gemini returned an unsupported image format")
                return ImageGenerationResponse(
                    image_bytes=self._decode(inline.get("data")),
                    mime_type=mime,
                    provider=self.provider_id,
                    model=self.model,
                    latency_ms=latency,
                    provider_request_id=response.headers.get("x-request-id"),
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
        saw_retryable = False
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
                saw_retryable = saw_retryable or isinstance(
                    exc,
                    (
                        AIProviderRateLimitError,
                        AIProviderTimeoutError,
                        AIProviderUnavailableError,
                    ),
                )
        if not eligible:
            raise AIProviderPolicyError(
                "no configured image provider is authorized for requested sensitivity"
            )
        if saw_retryable:
            raise AIProviderUnavailableError("all configured image providers failed") from last
        assert last is not None
        raise last


def build_image_router(
    loader: ConfigLoader,
    *,
    providers: tuple[ImageGenerationProvider, ...] | None = None,
) -> tuple[ImageGenerationRouter, ImageProvidersConfig]:
    config = ImageProvidersConfigLoader(loader).load()
    if providers is None:
        built: list[ImageGenerationProvider] = []
        for item in config.providers:
            key = os.getenv(item.api_key_env)
            if not key or not key.strip():
                continue
            if isinstance(item, OpenAIImageProviderConfig):
                built.append(OpenAIImageProvider(item, api_key=key))
            else:
                built.append(GeminiImageProvider(item, api_key=key))
        providers = tuple(built)
    return (
        ImageGenerationRouter(
            providers,
            sensitivity_provider_allowlists=config.sensitivity_provider_allowlists,
        ),
        config,
    )
