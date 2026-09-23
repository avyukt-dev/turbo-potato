from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from news_ai_ai import (
    AIInvalidResponseError,
    AIProviderPolicyError,
    AIProviderTimeoutError,
    GeminiImageProvider,
    GeminiImageProviderConfig,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageGenerationRouter,
    OpenAIImageProvider,
    OpenAIImageProviderConfig,
)


class FakeClient:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_arguments):
        return None

    async def post(self, url, **arguments):
        self.request = (url, arguments)
        if self.error:
            raise self.error
        return self.response


def _request() -> ImageGenerationRequest:
    return ImageGenerationRequest(prompt="A safe editorial illustration", input_hash="a" * 64)


def test_openai_image_provider_uses_official_image_contract(monkeypatch) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    response = httpx.Response(
        200,
        request=request,
        headers={"x-request-id": "openai-request"},
        json={"data": [{"b64_json": base64.b64encode(b"jpeg-bytes").decode()}]},
    )
    client = FakeClient(response)
    monkeypatch.setattr("news_ai_ai.image_generation.httpx.AsyncClient", lambda **_: client)
    provider = OpenAIImageProvider(
        OpenAIImageProviderConfig(adapter_type="openai_images"), api_key="secret"
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.image_bytes == b"jpeg-bytes"
    assert result.provider_request_id == "openai-request"
    assert client.request[0].endswith("/images/generations")
    payload = client.request[1]["json"]
    assert payload["output_format"] == "jpeg"
    assert payload["size"] == "1024x1536"
    assert "secret" not in repr(result)


def test_gemini_image_provider_extracts_non_thought_inline_image(monkeypatch) -> None:
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com")
    response = httpx.Response(
        200,
        request=request,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "reasoning", "thought": True},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"png-bytes").decode(),
                                }
                            },
                        ]
                    }
                }
            ]
        },
    )
    client = FakeClient(response)
    monkeypatch.setattr("news_ai_ai.image_generation.httpx.AsyncClient", lambda **_: client)
    provider = GeminiImageProvider(
        GeminiImageProviderConfig(adapter_type="gemini_images"), api_key="secret"
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.image_bytes == b"png-bytes"
    payload = client.request[1]["json"]
    assert payload["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert payload["generationConfig"]["imageConfig"]["aspectRatio"] == "4:5"


def test_provider_failures_are_normalized_without_secret_leakage(monkeypatch) -> None:
    secret = "SUPER_SECRET_IMAGE_TOKEN"
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    client = FakeClient(error=httpx.ReadTimeout(f"failed with {secret}", request=request))
    monkeypatch.setattr("news_ai_ai.image_generation.httpx.AsyncClient", lambda **_: client)
    provider = OpenAIImageProvider(
        OpenAIImageProviderConfig(adapter_type="openai_images"), api_key=secret
    )

    with pytest.raises(AIProviderTimeoutError) as caught:
        asyncio.run(provider.generate(_request()))
    assert secret not in str(caught.value)


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
