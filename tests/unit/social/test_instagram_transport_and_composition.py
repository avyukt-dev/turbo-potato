from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import yaml
from news_ai_publisher import build_instagram_adapter
from news_ai_social import (
    GraphTransportError,
    HttpxInstagramGraphTransport,
    InstagramAdapter,
    MockInstagramAdapter,
    SocialMode,
    SocialSettings,
    load_instagram_config,
)
from pydantic import SecretStr, ValidationError

TOKEN = "SUPER_SECRET_INSTAGRAM_TOKEN_123"


def _write_config(tmp_path: Path, *, enabled: bool = True) -> Path:
    source = yaml.safe_load(Path("config/platforms/instagram.yaml").read_text())
    source["enabled"] = enabled
    target = tmp_path / "platforms"
    target.mkdir(parents=True)
    (target / "instagram.yaml").write_text(yaml.safe_dump(source), encoding="utf-8")
    return tmp_path


def test_typed_platform_config_rejects_unknown_and_unimplemented_capability(tmp_path: Path) -> None:
    root = _write_config(tmp_path)
    path = root / "platforms" / "instagram.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["unexpected"] = True
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError):
        load_instagram_config(root)

    root = _write_config(tmp_path / "other")
    path = root / "platforms" / "instagram.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["capabilities"]["video"] = True
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError, match="unimplemented"):
        load_instagram_config(root)

    root = _write_config(tmp_path / "permissions")
    path = root / "platforms" / "instagram.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["required_permissions"] = ["instagram_business_basic"]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError, match="permissions"):
        load_instagram_config(root)


def test_mock_is_default_and_credentials_cannot_enable_network() -> None:
    adapter = build_instagram_adapter(
        SocialSettings(instagram_access_token=TOKEN, instagram_account_id="123")
    )
    assert isinstance(adapter, MockInstagramAdapter)
    assert SocialSettings().social_mode is SocialMode.MOCK


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            SocialSettings(
                environment="development",
                social_mode="LIVE",
                publishing_enabled=True,
                instagram_account_id="123",
                instagram_access_token=TOKEN,
            ),
            "environment",
        ),
        (
            SocialSettings(
                environment="production",
                social_mode="LIVE",
                publishing_enabled=False,
                instagram_account_id="123",
                instagram_access_token=TOKEN,
            ),
            "enabled",
        ),
        (
            SocialSettings(
                environment="production",
                social_mode="LIVE",
                publishing_enabled=True,
                instagram_access_token=TOKEN,
            ),
            "account ID",
        ),
        (
            SocialSettings(
                environment="production",
                social_mode="LIVE",
                publishing_enabled=True,
                instagram_account_id="123",
            ),
            "access token",
        ),
    ],
)
def test_live_composition_fails_closed(
    tmp_path: Path, settings: SocialSettings, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        build_instagram_adapter(settings, config_root=_write_config(tmp_path))


def test_platform_must_be_enabled_and_valid_explicit_live_can_compose(tmp_path: Path) -> None:
    settings = SocialSettings(
        environment="production",
        social_mode="LIVE",
        publishing_enabled=True,
        instagram_account_id="123",
        instagram_access_token=TOKEN,
    )
    with pytest.raises(RuntimeError, match="disabled"):
        build_instagram_adapter(settings, config_root=_write_config(tmp_path, enabled=False))

    class Transport:
        async def get(self, path: str, *, params: dict[str, str]):
            raise AssertionError("not called during composition")

        async def post(self, path: str, *, data: dict[str, str]):
            raise AssertionError("not called during composition")

    adapter = build_instagram_adapter(
        settings,
        config_root=_write_config(tmp_path / "enabled"),
        transport=Transport(),
    )
    assert isinstance(adapter, InstagramAdapter)


def test_http_transport_uses_versioned_path_and_authorization_header_without_leak() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "101"}, request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = HttpxInstagramGraphTransport(
                graph_base_url="https://graph.instagram.com",
                api_version="v26.0",
                access_token=SecretStr(TOKEN),
                timeout_seconds=5,
                client=client,
            )
            response = await transport.post("123/media", data={"image_url": "https://x.invalid"})
            assert response.payload == {"id": "101"}
            assert TOKEN not in repr(transport)

    asyncio.run(run())
    assert seen["url"] == "https://graph.instagram.com/v26.0/123/media"
    assert seen["authorization"] == f"Bearer {TOKEN}"


def test_transport_errors_and_invalid_json_never_leak_token() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"lost {TOKEN}", request=request)

    async def invalid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"invalid {TOKEN}", request=request)

    async def run(handler: httpx.MockTransport) -> GraphTransportError:
        async with httpx.AsyncClient(transport=handler) as client:
            transport = HttpxInstagramGraphTransport(
                graph_base_url="https://graph.instagram.com",
                api_version="v26.0",
                access_token=SecretStr(TOKEN),
                timeout_seconds=5,
                client=client,
            )
            with pytest.raises(GraphTransportError) as caught:
                await transport.post("123/media_publish", data={"creation_id": "x"})
            return caught.value

    for handler in (httpx.MockTransport(timeout_handler), httpx.MockTransport(invalid_handler)):
        error = asyncio.run(run(handler))
        assert error.outcome_may_be_ambiguous is True
        assert TOKEN not in str(error)
        assert TOKEN not in repr(error)
