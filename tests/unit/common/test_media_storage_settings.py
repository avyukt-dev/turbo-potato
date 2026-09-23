from __future__ import annotations

import pytest
from news_ai_common.config import (
    AppSettings,
    GeneratedMediaStorageBackend,
    S3CompatibleProvider,
)
from pydantic import ValidationError


def test_local_generated_media_settings_remain_the_default(tmp_path) -> None:
    settings = AppSettings(
        media_generation_enabled=True,
        generated_media_directory=tmp_path,
        generated_media_public_base_url="https://media.example/generated",
    )

    assert settings.generated_media_storage_backend is GeneratedMediaStorageBackend.LOCAL
    assert settings.generated_media_directory == tmp_path


def test_aws_s3_uses_sdk_endpoint_and_allows_default_credential_chain() -> None:
    settings = AppSettings(
        media_generation_enabled=True,
        generated_media_storage_backend="s3",
        generated_media_public_base_url="https://media.example",
        generated_media_s3_provider="aws",
        generated_media_s3_bucket="news-ai-media",
        generated_media_s3_region="ap-south-1",
    )

    assert settings.generated_media_storage_backend is GeneratedMediaStorageBackend.S3
    assert settings.generated_media_s3_provider is S3CompatibleProvider.AWS
    assert settings.generated_media_s3_endpoint_url is None


def test_cloudflare_r2_requires_auto_region_endpoint_and_explicit_credentials() -> None:
    valid = AppSettings(
        media_generation_enabled=True,
        generated_media_storage_backend="s3",
        generated_media_public_base_url="https://media.example",
        generated_media_s3_provider="cloudflare-r2",
        generated_media_s3_bucket="news-ai-media",
        generated_media_s3_region="auto",
        generated_media_s3_endpoint_url="https://account.r2.cloudflarestorage.com",
        generated_media_s3_access_key_id="access",
        generated_media_s3_secret_access_key="secret",
    )
    assert valid.generated_media_s3_provider is S3CompatibleProvider.CLOUDFLARE_R2

    for override in (
        {"generated_media_s3_region": "us-east-1"},
        {"generated_media_s3_endpoint_url": None},
        {"generated_media_s3_access_key_id": None},
    ):
        arguments = valid.model_dump()
        arguments.update(override)
        with pytest.raises(ValidationError):
            AppSettings(**arguments)


def test_s3_settings_reject_partial_credentials_and_unsafe_prefix() -> None:
    common = {
        "media_generation_enabled": True,
        "generated_media_storage_backend": "s3",
        "generated_media_public_base_url": "https://media.example",
        "generated_media_s3_bucket": "news-ai-media",
        "generated_media_s3_region": "ap-south-1",
    }
    with pytest.raises(ValidationError, match="configured together"):
        AppSettings(**common, generated_media_s3_access_key_id="access")
    with pytest.raises(ValidationError, match="unsafe path"):
        AppSettings(**common, generated_media_key_prefix="generated/../escape")


def test_compatible_s3_requires_https_endpoint() -> None:
    common = {
        "media_generation_enabled": True,
        "generated_media_storage_backend": "s3",
        "generated_media_public_base_url": "https://media.example",
        "generated_media_s3_provider": "compatible",
        "generated_media_s3_bucket": "news-ai-media",
        "generated_media_s3_region": "us-east-1",
    }
    with pytest.raises(ValidationError, match="endpoint is required"):
        AppSettings(**common)
    with pytest.raises(ValidationError, match="must use HTTPS"):
        AppSettings(**common, generated_media_s3_endpoint_url="http://storage.internal")
    with pytest.raises(ValidationError, match="must use HTTPS"):
        AppSettings(
            **common,
            generated_media_s3_endpoint_url="https://user:secret@storage.example",
        )


def test_s3_credentials_are_secret_safe() -> None:
    sentinel = "SUPER_SECRET_S3_TOKEN_123"
    settings = AppSettings(
        media_generation_enabled=True,
        generated_media_storage_backend="s3",
        generated_media_public_base_url="https://media.example",
        generated_media_s3_bucket="news-ai-media",
        generated_media_s3_region="ap-south-1",
        generated_media_s3_access_key_id="access",
        generated_media_s3_secret_access_key=sentinel,
    )

    assert sentinel not in repr(settings)
    assert sentinel not in str(settings)
