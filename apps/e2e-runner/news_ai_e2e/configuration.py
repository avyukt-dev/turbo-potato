"""Fail-closed configuration for the opt-in live end-to-end runner."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

from news_ai_common.config import AppSettings
from news_ai_social import SocialMode, SocialSettings
from sqlalchemy.engine import make_url

LIVE_CONFIRMATION = "LIVE_INSTAGRAM_E2E"
MEDIA_CONFIRMATION = "PUBLISHABLE_JPEG"
ISOLATED_DATA_CONFIRMATION = "ISOLATED_E2E_DATA"
_REQUIRED_REVIEW_CAPABILITIES = frozenset({"view", "review", "approve", "publish"})
_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "apikey", "password", "secret", "token"}
)


class LiveE2EConfigurationError(RuntimeError):
    """Raised before side effects when live E2E prerequisites are unsafe or incomplete."""


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _positive_int_env(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError:
        raise LiveE2EConfigurationError(f"{name} must be an integer") from None
    if value < 1 or value > maximum:
        raise LiveE2EConfigurationError(f"{name} must be between 1 and {maximum}")
    return value


def _positive_float_env(name: str, default: float, *, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError:
        raise LiveE2EConfigurationError(f"{name} must be numeric") from None
    if value <= 0 or value > maximum:
        raise LiveE2EConfigurationError(f"{name} must be > 0 and <= {maximum}")
    return value


def _require_https_urls(
    name: str,
    values: tuple[str, ...],
    *,
    unique: bool = False,
) -> None:
    if not values:
        raise LiveE2EConfigurationError(f"{name} must contain at least one URL")
    if unique and len(values) != len(set(values)):
        raise LiveE2EConfigurationError(f"{name} must not contain duplicate URLs")
    for value in values:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise LiveE2EConfigurationError(
                f"{name} entries must be credential-free public HTTPS URLs"
            )
        host = parsed.hostname.casefold().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise LiveE2EConfigurationError(f"{name} entries must use a public host")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise LiveE2EConfigurationError(f"{name} entries must use a public host") from None
        else:
            if not address.is_global:
                raise LiveE2EConfigurationError(f"{name} entries must use a public host")
        query_keys = {key.casefold().replace("-", "_") for key, _ in parse_qsl(parsed.query)}
        if query_keys & _SENSITIVE_QUERY_KEYS:
            raise LiveE2EConfigurationError(
                f"{name} entries must not contain credential query parameters"
            )


def _require_loopback_api(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise LiveE2EConfigurationError("NEWS_AI_E2E_API_BASE_URL must use HTTP or HTTPS")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LiveE2EConfigurationError(
            "NEWS_AI_E2E_API_BASE_URL must be loopback so the review token is never sent remotely"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LiveE2EConfigurationError(
            "NEWS_AI_E2E_API_BASE_URL must not contain credentials, query, or fragment"
        )
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class LiveE2ESettings:
    """Non-secret harness controls. Application/provider secrets stay in their existing settings."""

    feed_urls: tuple[str, ...]
    media_urls: tuple[str, ...]
    api_base_url: str = "http://127.0.0.1:8000"
    article_title_contains: str | None = None
    max_articles_total: int = 3
    timeout_seconds: float = 900.0
    poll_interval_seconds: float = 0.5
    media_max_bytes: int = 10_000_000
    account_name: str = "live-e2e-test-account"

    @classmethod
    def from_env(cls) -> LiveE2ESettings:
        if os.getenv("NEWS_AI_E2E_CONFIRM_LIVE") != LIVE_CONFIRMATION:
            raise LiveE2EConfigurationError(
                f"NEWS_AI_E2E_CONFIRM_LIVE must equal {LIVE_CONFIRMATION}"
            )
        if os.getenv("NEWS_AI_E2E_CONFIRM_MEDIA_PUBLISHABLE") != MEDIA_CONFIRMATION:
            raise LiveE2EConfigurationError(
                f"NEWS_AI_E2E_CONFIRM_MEDIA_PUBLISHABLE must equal {MEDIA_CONFIRMATION}"
            )
        if os.getenv("NEWS_AI_E2E_CONFIRM_ISOLATED_DATA") != ISOLATED_DATA_CONFIRMATION:
            raise LiveE2EConfigurationError(
                "NEWS_AI_E2E_CONFIRM_ISOLATED_DATA must explicitly confirm disposable E2E data"
            )
        feed_urls = _csv_env("NEWS_AI_E2E_FEED_URLS")
        media_urls = _csv_env("NEWS_AI_E2E_MEDIA_URLS")
        _require_https_urls("NEWS_AI_E2E_FEED_URLS", feed_urls, unique=True)
        _require_https_urls("NEWS_AI_E2E_MEDIA_URLS", media_urls)
        api_base_url = _require_loopback_api(
            os.getenv("NEWS_AI_E2E_API_BASE_URL", "http://127.0.0.1:8000")
        )
        title_filter = os.getenv("NEWS_AI_E2E_ARTICLE_TITLE_CONTAINS")
        title_filter = title_filter.strip() if title_filter and title_filter.strip() else None
        account_name = os.getenv("NEWS_AI_E2E_ACCOUNT_NAME", "live-e2e-test-account").strip()
        if not account_name or len(account_name) > 200:
            raise LiveE2EConfigurationError(
                "NEWS_AI_E2E_ACCOUNT_NAME must be between 1 and 200 characters"
            )
        return cls(
            feed_urls=feed_urls,
            media_urls=media_urls,
            api_base_url=api_base_url,
            article_title_contains=title_filter,
            max_articles_total=_positive_int_env("NEWS_AI_E2E_MAX_ARTICLES_TOTAL", 3, maximum=20),
            timeout_seconds=_positive_float_env(
                "NEWS_AI_E2E_TIMEOUT_SECONDS", 900.0, maximum=7200.0
            ),
            poll_interval_seconds=_positive_float_env(
                "NEWS_AI_E2E_POLL_INTERVAL_SECONDS", 0.5, maximum=10.0
            ),
            media_max_bytes=_positive_int_env(
                "NEWS_AI_E2E_MEDIA_MAX_BYTES", 10_000_000, maximum=20_000_000
            ),
            account_name=account_name,
        )

    def validate_application(
        self,
        app: AppSettings,
        social: SocialSettings,
        *,
        groq_api_key_present: bool | None = None,
        publishing_pause_value: str | None = None,
    ) -> None:
        failures: list[str] = []
        if app.environment.strip().lower() != "production":
            failures.append("NEWS_AI_ENVIRONMENT must be production for LIVE Instagram")
        if not app.database_url:
            failures.append("NEWS_AI_DATABASE_URL is required")
        else:
            database_name = (make_url(app.database_url).database or "").casefold()
            if not any(marker in database_name for marker in ("e2e", "test", "disposable")):
                failures.append("NEWS_AI_DATABASE_URL must name a disposable E2E/test database")
        if not app.redis_url:
            failures.append("NEWS_AI_REDIS_URL is required")
        else:
            redis_path = urlparse(app.redis_url).path.strip("/")
            if not redis_path.isdigit() or int(redis_path) == 0:
                failures.append("NEWS_AI_REDIS_URL must use a dedicated nonzero Redis database")
        if app.review_api_token is None or not app.review_api_token.get_secret_value().strip():
            failures.append("NEWS_AI_REVIEW_API_TOKEN is required")
        if app.reviewer_id is None:
            failures.append("NEWS_AI_REVIEWER_ID is required")
        capabilities = {item.strip() for item in app.review_capabilities.split(",") if item.strip()}
        missing = sorted(_REQUIRED_REVIEW_CAPABILITIES - capabilities)
        if missing:
            failures.append("NEWS_AI_REVIEW_CAPABILITIES is missing: " + ",".join(missing))
        if social.social_mode is not SocialMode.LIVE:
            failures.append("NEWS_AI_SOCIAL_MODE must be LIVE")
        if not social.publishing_enabled:
            failures.append("NEWS_AI_PUBLISHING_ENABLED must be true")
        if social.instagram_account_id is None:
            failures.append("NEWS_AI_INSTAGRAM_ACCOUNT_ID is required")
        elif os.getenv("NEWS_AI_E2E_CONFIRM_INSTAGRAM_ACCOUNT_ID") != str(
            social.instagram_account_id
        ):
            failures.append(
                "NEWS_AI_E2E_CONFIRM_INSTAGRAM_ACCOUNT_ID must match the dedicated test account"
            )
        if (
            social.instagram_access_token is None
            or not social.instagram_access_token.get_secret_value().strip()
        ):
            failures.append("NEWS_AI_INSTAGRAM_ACCESS_TOKEN is required")
        if groq_api_key_present is None:
            groq_api_key_present = any(
                os.getenv(name, "").strip()
                for name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3")
            )
        if not groq_api_key_present:
            failures.append("at least one configured GROQ_API_KEY credential is required")
        if publishing_pause_value is None:
            publishing_pause_value = os.getenv("NEWS_AI_PUBLISHING_PAUSED")
        if publishing_pause_value is not None and publishing_pause_value.casefold() != "false":
            failures.append(
                "NEWS_AI_PUBLISHING_PAUSED must be unset or false; the runner owns the DB pause"
            )
        if failures:
            raise LiveE2EConfigurationError("; ".join(failures))
