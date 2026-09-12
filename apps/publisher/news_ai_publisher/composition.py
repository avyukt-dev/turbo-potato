"""Fail-closed MOCK/LIVE Instagram adapter composition."""

from __future__ import annotations

from news_ai_social import (
    HttpxInstagramGraphTransport,
    InstagramAdapter,
    InstagramGraphTransport,
    MockInstagramAdapter,
    SocialMode,
    SocialPlatformAdapter,
    SocialSettings,
    load_instagram_config,
)


def build_instagram_adapter(
    settings: SocialSettings,
    *,
    config_root: str = "config",
    transport: InstagramGraphTransport | None = None,
) -> SocialPlatformAdapter:
    config = load_instagram_config(config_root)
    if settings.social_mode is SocialMode.MOCK:
        return MockInstagramAdapter(config)

    environment = settings.environment.strip().lower()
    if environment not in config.live_environments:
        raise RuntimeError("LIVE social mode is not allowed in this environment")
    if not settings.publishing_enabled:
        raise RuntimeError("LIVE social mode requires publishing to be explicitly enabled")
    if not config.enabled:
        raise RuntimeError("Instagram platform configuration is disabled")
    if settings.instagram_account_id is None:
        raise RuntimeError("LIVE Instagram mode requires an account ID")
    if settings.instagram_access_token is None:
        raise RuntimeError("LIVE Instagram mode requires an access token")
    graph_transport = transport or HttpxInstagramGraphTransport(
        graph_base_url=str(config.graph_base_url),
        api_version=config.api_version,
        access_token=settings.instagram_access_token,
        timeout_seconds=config.request_timeout_seconds,
    )
    return InstagramAdapter(
        config,
        account_id=settings.instagram_account_id,
        transport=graph_transport,
    )
