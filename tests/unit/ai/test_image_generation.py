from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import httpx
import openai
import pytest
from news_ai_ai import (
    AIInvalidResponseError,
    AIProviderPolicyError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    GeminiImageProvider,
    GeminiImageProviderConfig,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageGenerationRouter,
    OpenAIImageProvider,
    OpenAIImageProviderConfig,
)
from news_ai_ai.credentials import (
    CredentialPoolConfig,
    CredentialStateUnavailableError,
    MemoryCredentialPool,
    ResolvedCredential,
)


def _pool(provider: str, secret: str = "secret") -> MemoryCredentialPool:
    config = CredentialPoolConfig(
        pool_id=f"{provider}-test",
        provider=provider,
        env_prefix=f"{provider.upper()}_API_KEY",
    )
    return MemoryCredentialPool(
        config,
        (
            ResolvedCredential(
                pool_id=config.pool_id,
                provider_type=provider,
                slot=1,
                fingerprint="fingerprint",
                secret=secret,
            ),
        ),
    )


def _request() -> ImageGenerationRequest:
    return ImageGenerationRequest(prompt="A safe editorial illustration", input_hash="a" * 64)


def test_openai_image_provider_uses_official_image_contract() -> None:
    calls = []

    class Images:
        async def generate(self, **payload):
            calls.append(payload)
            return SimpleNamespace(
                id="openai-request",
                data=[SimpleNamespace(b64_json=base64.b64encode(b"jpeg-bytes").decode())],
            )

    provider = OpenAIImageProvider(
        OpenAIImageProviderConfig(adapter_type="openai_images"),
        credential_pool=_pool("openai"),
        client_factory=lambda _: SimpleNamespace(images=Images()),
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.image_bytes == b"jpeg-bytes"
    assert result.provider_request_id == "openai-request"
    payload = calls[0]
    assert payload["output_format"] == "jpeg"
    assert payload["size"] == "1024x1536"
    assert "secret" not in repr(result)


def test_gemini_image_provider_extracts_non_thought_inline_image() -> None:
    calls = []

    class Models:
        async def generate_content(self, **payload):
            calls.append(payload)
            return SimpleNamespace(
                response_id="gemini-request",
                parts=[
                    SimpleNamespace(thought=True, inline_data=None),
                    SimpleNamespace(
                        thought=False,
                        inline_data=SimpleNamespace(mime_type="image/png", data=b"png-bytes"),
                    ),
                ],
            )

    provider = GeminiImageProvider(
        GeminiImageProviderConfig(adapter_type="gemini_images"),
        credential_pool=_pool("gemini"),
        client_factory=lambda _: SimpleNamespace(aio=SimpleNamespace(models=Models())),
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.image_bytes == b"png-bytes"
    assert calls[0]["config"].response_modalities == ["IMAGE"]
    assert calls[0]["config"].image_config.aspect_ratio == "4:5"


def test_provider_failures_are_normalized_without_secret_leakage() -> None:
    secret = "SUPER_SECRET_IMAGE_TOKEN"

    class Images:
        async def generate(self, **_payload):
            raise openai.APITimeoutError(
                request=httpx.Request("POST", "https://api.openai.com/v1/images/generations")
            )

    provider = OpenAIImageProvider(
        OpenAIImageProviderConfig(adapter_type="openai_images"),
        credential_pool=_pool("openai", secret),
        client_factory=lambda _: SimpleNamespace(images=Images()),
    )

    with pytest.raises(AIProviderTimeoutError) as caught:
        asyncio.run(provider.generate(_request()))
    assert secret not in str(caught.value)


def test_credential_state_failure_is_normalized_for_image_provider() -> None:
    class UnavailablePool:
        async def acquire(self, _attempted):
            raise CredentialStateUnavailableError("SUPER_SECRET_DATABASE_DETAIL")

    provider = OpenAIImageProvider(
        OpenAIImageProviderConfig(adapter_type="openai_images"),
        credential_pool=UnavailablePool(),
    )

    with pytest.raises(AIProviderUnavailableError) as caught:
        asyncio.run(provider.generate(_request()))
    assert str(caught.value) == "AI credential state is unavailable"
    assert "SUPER_SECRET" not in str(caught.value)


def test_router_falls_back_and_fails_closed_for_permanent_errors() -> None:
    class Provider:
        def __init__(self, provider_id, result=None, error=None):
            self.provider_id = provider_id
            self.model = "image-model"
            self.result = result
            self.error = error
            self.calls = 0

        async def generate(self, _request):
            self.calls += 1
            if self.error:
                raise self.error
            return self.result

    first = Provider("openai", error=AIProviderTimeoutError("timeout"))
    expected = ImageGenerationResponse(b"image", "image/jpeg", "gemini", "image-model", 1)
    second = Provider("gemini", result=expected)
    assert asyncio.run(ImageGenerationRouter((first, second)).generate(_request())) == expected
    assert first.calls == second.calls == 1

    permanent = ImageGenerationRouter(
        (
            Provider("openai", error=AIProviderPolicyError("denied")),
            Provider("gemini", error=AIInvalidResponseError("invalid")),
        )
    )
    with pytest.raises(AIInvalidResponseError):
        asyncio.run(permanent.generate(_request()))


def test_router_enforces_sensitivity_policy_before_provider_call() -> None:
    class Provider:
        provider_id = "openai"
        model = "image-model"
        calls = 0

        async def generate(self, _request):
            self.calls += 1
            return ImageGenerationResponse(b"image", "image/jpeg", "openai", self.model, 1)

    provider = Provider()
    request = ImageGenerationRequest(
        prompt="A safe editorial illustration",
        input_hash="a" * 64,
        sensitivity=("RELIGIOUS_VIOLENCE",),
    )
    blocked = ImageGenerationRouter(
        (provider,),
        sensitivity_provider_allowlists={"RELIGIOUS_VIOLENCE": frozenset({"local-llama"})},
    )

    with pytest.raises(AIProviderPolicyError):
        asyncio.run(blocked.generate(request))
    assert provider.calls == 0

    allowed = ImageGenerationRouter(
        (provider,),
        sensitivity_provider_allowlists={"RELIGIOUS_VIOLENCE": frozenset({"openai"})},
    )
    assert asyncio.run(allowed.generate(request)).provider == "openai"
    assert provider.calls == 1


def test_router_rejects_unconfigured_sensitivity_without_provider_call() -> None:
    class Provider:
        provider_id = "openai"
        model = "image-model"
        calls = 0

        async def generate(self, _request):
            self.calls += 1

    provider = Provider()
    router = ImageGenerationRouter((provider,), sensitivity_provider_allowlists={})
    request = ImageGenerationRequest(
        prompt="A safe editorial illustration",
        input_hash="a" * 64,
        sensitivity=("UNKNOWN_CATEGORY",),
    )

    with pytest.raises(AIProviderPolicyError):
        asyncio.run(router.generate(request))
    assert provider.calls == 0


def test_router_rejects_provider_provenance_mismatch() -> None:
    class Provider:
        provider_id = "openai"
        model = "expected-model"

        async def generate(self, _request):
            return ImageGenerationResponse(
                b"image", "image/jpeg", "forged-provider", "unexpected-model", 1
            )

    with pytest.raises(AIInvalidResponseError):
        asyncio.run(ImageGenerationRouter((Provider(),)).generate(_request()))
