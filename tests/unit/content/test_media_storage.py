from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber
from news_ai_content import (
    MediaStorageConfigurationError,
    MediaStorageIntegrityError,
    MediaStorageTransientError,
    S3GeneratedMediaStore,
    StoreMediaRequest,
    build_s3_client,
    generated_media_storage_key,
)


def _request() -> StoreMediaRequest:
    content = b"canonical-watermarked-jpeg"
    return StoreMediaRequest(
        input_hash="a" * 64,
        file_hash=hashlib.sha256(content).hexdigest(),
        position=2,
        content=content,
    )


class FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict] = []
        self.head_calls: list[dict] = []
        self.put_error: BaseException | None = None
        self.head_override: dict | None = None

    def put_object(self, **arguments):
        self.put_calls.append(arguments)
        if self.put_error is not None:
            raise self.put_error
        return {"ETag": '"etag-one"', "VersionId": "version-one"}

    def head_object(self, **arguments):
        self.head_calls.append(arguments)
        if self.head_override is not None:
            return self.head_override
        request = _request()
        return {
            "ContentLength": len(request.content),
            "ContentType": request.mime_type,
            "Metadata": {"sha256": request.file_hash},
            "ETag": '"etag-one"',
            "VersionId": "version-one",
        }


def _store(client: FakeS3Client) -> S3GeneratedMediaStore:
    return S3GeneratedMediaStore(
        client,
        storage_provider="cloudflare-r2",
        bucket="news-ai-media",
        region="auto",
        public_base_url="https://media.example",
        key_prefix="generated",
        endpoint_url="https://account.r2.cloudflarestorage.com",
    )


def _client_error(code: str, status: int, message: str = "SECRET_SENTINEL") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def test_s3_store_uploads_and_verifies_exact_immutable_object() -> None:
    client = FakeS3Client()
    stored = asyncio.run(_store(client).put(_request()))

    expected_key = generated_media_storage_key(
        input_hash="a" * 64,
        file_hash=_request().file_hash,
        position=2,
        key_prefix="generated",
    )
    assert stored.storage_provider == "cloudflare-r2"
    assert stored.storage_key == expected_key
    assert stored.public_url == f"https://media.example/{expected_key}"
    assert stored.etag == "etag-one"
    assert stored.version_id == "version-one"
    upload = client.put_calls[0]
    assert upload["Bucket"] == "news-ai-media"
    assert upload["Key"] == expected_key
    assert upload["Body"] == _request().content
    assert upload["ContentType"] == "image/jpeg"
    assert upload["CacheControl"] == "public, max-age=31536000, immutable"
    assert upload["Metadata"] == {"sha256": _request().file_hash}
    assert upload["IfNoneMatch"] == "*"
    assert client.head_calls == [{"Bucket": "news-ai-media", "Key": expected_key}]


def test_s3_store_request_is_accepted_by_real_botocore_contract() -> None:
    request = _request()
    client = build_s3_client(
        region="auto",
        endpoint_url="https://account.r2.cloudflarestorage.com",
        access_key_id="access",
        secret_access_key="secret",
        session_token=None,
        connect_timeout_seconds=5,
        read_timeout_seconds=30,
        max_attempts=3,
    )
    key = generated_media_storage_key(
        input_hash=request.input_hash,
        file_hash=request.file_hash,
        position=request.position,
        key_prefix="generated",
    )
    expected = {
        "Bucket": "news-ai-media",
        "Key": key,
        "Body": request.content,
        "ContentLength": len(request.content),
        "ContentType": "image/jpeg",
        "CacheControl": request.cache_control,
        "ContentMD5": base64.b64encode(
            hashlib.md5(request.content, usedforsecurity=False).digest()
        ).decode("ascii"),
        "Metadata": {"sha256": request.file_hash},
        "IfNoneMatch": "*",
    }
    with Stubber(client) as stubber:
        stubber.add_response("put_object", {"ETag": '"etag"', "VersionId": "version"}, expected)
        stubber.add_response(
            "head_object",
            {
                "ContentLength": len(request.content),
                "ContentType": "image/jpeg",
                "Metadata": {"sha256": request.file_hash},
                "ETag": '"etag"',
                "VersionId": "version",
            },
            {"Bucket": "news-ai-media", "Key": key},
        )
        stored = asyncio.run(_store(client).put(request))

    assert stored.etag == "etag"


def test_s3_store_reuses_preconditioned_object_only_when_identity_matches() -> None:
    client = FakeS3Client()
    client.put_error = _client_error("PreconditionFailed", 412)

    stored = asyncio.run(_store(client).put(_request()))
    assert stored.file_hash == _request().file_hash

    client.head_override = {
        "ContentLength": len(_request().content),
        "ContentType": "image/jpeg",
        "Metadata": {"sha256": "f" * 64},
    }
    with pytest.raises(MediaStorageIntegrityError):
        asyncio.run(_store(client).put(_request()))


def test_s3_store_normalizes_transient_and_configuration_errors_without_secrets() -> None:
    client = FakeS3Client()
    client.put_error = EndpointConnectionError(endpoint_url="https://SECRET_SENTINEL.invalid")
    with pytest.raises(MediaStorageTransientError) as transient:
        asyncio.run(_store(client).put(_request()))
    assert "SECRET_SENTINEL" not in str(transient.value)

    client.put_error = _client_error("AccessDenied", 403)
    with pytest.raises(MediaStorageConfigurationError) as permanent:
        asyncio.run(_store(client).put(_request()))
    assert "SECRET_SENTINEL" not in str(permanent.value)


def test_storage_request_and_key_fail_closed_on_bad_identity() -> None:
    with pytest.raises(ValueError, match="hash"):
        StoreMediaRequest(
            input_hash="a" * 64,
            file_hash="b" * 64,
            position=1,
            content=b"different",
        )
    with pytest.raises(ValueError, match="prefix"):
        generated_media_storage_key(
            input_hash="a" * 64,
            file_hash="b" * 64,
            position=1,
            key_prefix="generated/../escape",
        )
