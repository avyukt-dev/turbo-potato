from uuid import uuid4

import pytest
from news_ai_common.config import AppSettings
from news_ai_e2e.configuration import (
    LIVE_CONFIRMATION,
    MEDIA_CONFIRMATION,
    LiveE2EConfigurationError,
    LiveE2ESettings,
)
from news_ai_social import SocialSettings


def _env(monkeypatch):
    values = {
        "NEWS_AI_E2E_CONFIRM_LIVE": LIVE_CONFIRMATION,
        "NEWS_AI_E2E_CONFIRM_MEDIA_PUBLISHABLE": MEDIA_CONFIRMATION,
        "NEWS_AI_E2E_FEED_URLS": "https://example.org/feed.xml",
        "NEWS_AI_E2E_MEDIA_URLS": "https://cdn.example.org/test.jpg",
        "NEWS_AI_ENVIRONMENT": "production",
        "NEWS_AI_DATABASE_URL": "postgresql+psycopg://postgres:postgres@127.0.0.1/news_ai",
        "NEWS_AI_REDIS_URL": "redis://127.0.0.1:6379/0",
        "NEWS_AI_REVIEW_API_TOKEN": "review-secret",
        "NEWS_AI_REVIEWER_ID": str(uuid4()),
        "NEWS_AI_REVIEW_CAPABILITIES": "view,review,approve,publish",
        "NEWS_AI_SOCIAL_MODE": "LIVE",
        "NEWS_AI_PUBLISHING_ENABLED": "true",
        "NEWS_AI_PUBLISHING_PAUSED": "false",
        "NEWS_AI_INSTAGRAM_ACCOUNT_ID": "123456",
        "NEWS_AI_INSTAGRAM_ACCESS_TOKEN": "instagram-secret",
        "GROQ_API_KEY": "groq-secret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_live_e2e_settings_accept_explicit_safe_configuration(monkeypatch):
    _env(monkeypatch)
    settings = LiveE2ESettings.from_env()
    settings.validate_application(AppSettings(), SocialSettings())
    assert settings.max_articles_total == 3
    assert settings.api_base_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEWS_AI_E2E_CONFIRM_LIVE", "yes"),
        ("NEWS_AI_E2E_CONFIRM_MEDIA_PUBLISHABLE", "no"),
        ("NEWS_AI_E2E_FEED_URLS", "http://example.org/feed.xml"),
        ("NEWS_AI_E2E_MEDIA_URLS", "https://user:pass@example.org/a.jpg"),
        ("NEWS_AI_E2E_FEED_URLS", "https://127.0.0.1/feed.xml"),
        ("NEWS_AI_E2E_FEED_URLS", "https://example.org/feed.xml?access_token=secret"),
        (
            "NEWS_AI_E2E_FEED_URLS",
            "https://example.org/feed.xml,https://example.org/feed.xml",
        ),
        ("NEWS_AI_E2E_API_BASE_URL", "https://review.example.org"),
    ],
)
def test_live_e2e_settings_fail_closed_before_side_effects(monkeypatch, name, value):
    _env(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(LiveE2EConfigurationError):
        LiveE2ESettings.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEWS_AI_ENVIRONMENT", "test"),
        ("NEWS_AI_SOCIAL_MODE", "MOCK"),
        ("NEWS_AI_PUBLISHING_ENABLED", "false"),
        ("NEWS_AI_PUBLISHING_PAUSED", "true"),
        ("NEWS_AI_REVIEW_CAPABILITIES", "view,review,approve"),
        ("NEWS_AI_REVIEW_API_TOKEN", ""),
        ("NEWS_AI_INSTAGRAM_ACCESS_TOKEN", ""),
    ],
)
def test_application_live_prerequisites_are_strict(monkeypatch, name, value):
    _env(monkeypatch)
    settings = LiveE2ESettings.from_env()
    monkeypatch.setenv(name, value)
    with pytest.raises(LiveE2EConfigurationError):
        settings.validate_application(AppSettings(), SocialSettings())


def test_missing_groq_is_rejected_without_reading_or_printing_secret(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("GROQ_API_KEY")
    settings = LiveE2ESettings.from_env()
    with pytest.raises(LiveE2EConfigurationError, match="GROQ_API_KEY"):
        settings.validate_application(AppSettings(), SocialSettings())


def test_loopback_ipv6_is_allowed(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("NEWS_AI_E2E_API_BASE_URL", "http://[::1]:8000")
    assert LiveE2ESettings.from_env().api_base_url == "http://[::1]:8000"

