"""Immutable generated-media storage across local and S3-compatible backends."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_RETRYABLE_CODES = frozenset(
    {
        "InternalError",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "ThrottlingException",
    }
)


class MediaStorageError(RuntimeError):
    """Safe normalized base error for generated-media persistence."""


class MediaStorageTransientError(MediaStorageError):
    """The same content-addressed operation may be retried."""


class MediaStorageConfigurationError(MediaStorageError):
    """Credentials, bucket, endpoint, or permissions require operator correction."""


class MediaStorageIntegrityError(MediaStorageError):
    """Stored bytes or immutable identity conflict with the requested object."""


@dataclass(frozen=True, slots=True)
class StoreMediaRequest:
    input_hash: str
    file_hash: str
    position: int
    content: bytes
    mime_type: str = "image/jpeg"
    cache_control: str = "public, max-age=31536000, immutable"

    def __post_init__(self) -> None:
        if (
            _HASH_PATTERN.fullmatch(self.input_hash) is None
            or _HASH_PATTERN.fullmatch(self.file_hash) is None
            or self.position < 1
            or not self.content
            or self.mime_type != "image/jpeg"
        ):
            raise ValueError("generated media storage request is invalid")
        if hashlib.sha256(self.content).hexdigest() != self.file_hash:
            raise ValueError("generated media content hash does not match its bytes")


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    storage_provider: str
    storage_key: str
    public_url: str
    size_bytes: int
    file_hash: str
    etag: str | None = None
    version_id: str | None = None


class GeneratedMediaStore(Protocol):
    storage_provider: str

    @property
    def identity(self) -> dict[str, Any]: ...

    async def put(self, request: StoreMediaRequest) -> StoredMediaObject: ...


def generated_media_storage_key(
    *, input_hash: str, file_hash: str, position: int, key_prefix: str = ""
) -> str:
    if (
        _HASH_PATTERN.fullmatch(input_hash) is None
        or _HASH_PATTERN.fullmatch(file_hash) is None
        or position < 1
    ):
        raise ValueError("generated media storage identity is invalid")
    prefix = key_prefix.strip().strip("/")
    if prefix and any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise ValueError("generated media key prefix contains an unsafe path component")
    suffix = f"{input_hash[:2]}/{input_hash}/{file_hash}/slide-{position}.jpg"
    return f"{prefix}/{suffix}" if prefix else suffix


def _public_url(public_base_url: str, key: str) -> str:
    return f"{public_base_url.rstrip('/')}/{quote(key, safe='/')}"


class LocalGeneratedMediaStore:
    """Persist exact JPEG bytes locally; public delivery is configured separately."""

    storage_provider = "local-generated-media"

    def __init__(self, root: Path, public_base_url: str, *, key_prefix: str = "") -> None:
        self.root = root.resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.key_prefix = key_prefix.strip().strip("/")

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "backend": "local",
            "storage_provider": self.storage_provider,
            "public_base_url": self.public_base_url,
            "key_prefix": self.key_prefix,
        }

    async def put(self, request: StoreMediaRequest) -> StoredMediaObject:
        return await asyncio.to_thread(self._put_sync, request)

    def _put_sync(self, request: StoreMediaRequest) -> StoredMediaObject:
        key = generated_media_storage_key(
            input_hash=request.input_hash,
            file_hash=request.file_hash,
            position=request.position,
            key_prefix=self.key_prefix,
        )
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise MediaStorageConfigurationError(
                "generated media storage key escaped its configured root"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".tmp-{os.getpid()}-{uuid4().hex}")
        try:
            temporary.write_bytes(request.content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredMediaObject(
            storage_provider=self.storage_provider,
            storage_key=key,
            public_url=_public_url(self.public_base_url, key),
            size_bytes=len(request.content),
            file_hash=request.file_hash,
        )


class S3GeneratedMediaStore:
    """Store immutable JPEGs through the portable S3 PutObject/HeadObject subset."""

    def __init__(
        self,
        client: Any,
        *,
        storage_provider: str,
        bucket: str,
        region: str,
        public_base_url: str,
        key_prefix: str = "",
        endpoint_url: str | None = None,
    ) -> None:
        if not bucket.strip() or not region.strip():
            raise ValueError("generated media S3 bucket and region must not be blank")
        self.client = client
        self.storage_provider = storage_provider
        self.bucket = bucket
        self.region = region
        self.public_base_url = public_base_url.rstrip("/")
        self.key_prefix = key_prefix.strip().strip("/")
        self.endpoint_url = endpoint_url

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "backend": "s3",
            "storage_provider": self.storage_provider,
            "bucket": self.bucket,
            "region": self.region,
            "public_base_url": self.public_base_url,
            "key_prefix": self.key_prefix,
            "endpoint_url": self.endpoint_url,
        }

    async def put(self, request: StoreMediaRequest) -> StoredMediaObject:
        return await asyncio.to_thread(self._put_sync, request)

    def _put_sync(self, request: StoreMediaRequest) -> StoredMediaObject:
        key = generated_media_storage_key(
            input_hash=request.input_hash,
            file_hash=request.file_hash,
            position=request.position,
            key_prefix=self.key_prefix,
        )
        content_md5 = base64.b64encode(
            hashlib.md5(request.content, usedforsecurity=False).digest()
        ).decode("ascii")
        response: dict[str, Any] = {}
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=request.content,
                ContentLength=len(request.content),
                ContentType=request.mime_type,
                CacheControl=request.cache_control,
                ContentMD5=content_md5,
                Metadata={"sha256": request.file_hash},
                IfNoneMatch="*",
            )
        except ClientError as exc:
            code, status = _client_error_identity(exc)
            if code not in {"PreconditionFailed", "ConditionalRequestConflict"} and status not in {
                409,
                412,
            }:
                _raise_normalized_client_error(code, status)
        except (
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ):
            raise MediaStorageTransientError(
                "generated media object storage is temporarily unavailable"
            ) from None
        except BotoCoreError:
            raise MediaStorageConfigurationError(
                "generated media object storage configuration was rejected"
            ) from None
        head = self._verified_head(key, request)
        return StoredMediaObject(
            storage_provider=self.storage_provider,
            storage_key=key,
            public_url=_public_url(self.public_base_url, key),
            size_bytes=len(request.content),
            file_hash=request.file_hash,
            etag=_normalized_optional(response.get("ETag") or head.get("ETag"), strip_quotes=True),
            version_id=_normalized_optional(response.get("VersionId") or head.get("VersionId")),
        )

    def _verified_head(self, key: str, request: StoreMediaRequest) -> dict[str, Any]:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            _raise_normalized_client_error(*_client_error_identity(exc))
        except (
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ):
            raise MediaStorageTransientError(
                "generated media object verification is temporarily unavailable"
            ) from None
        except BotoCoreError:
            raise MediaStorageConfigurationError(
                "generated media object storage configuration was rejected"
            ) from None
        metadata = head.get("Metadata")
        if (
            head.get("ContentLength") != len(request.content)
            or head.get("ContentType") != request.mime_type
            or not isinstance(metadata, dict)
            or metadata.get("sha256") != request.file_hash
        ):
            raise MediaStorageIntegrityError(
                "generated media object conflicts with its immutable identity"
            )
        return head


def build_s3_client(
    *,
    region: str,
    endpoint_url: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    session_token: str | None,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    max_attempts: int,
) -> Any:
    arguments: dict[str, Any] = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(
            signature_version="s3v4",
            connect_timeout=connect_timeout_seconds,
            read_timeout=read_timeout_seconds,
            retries={"mode": "standard", "max_attempts": max_attempts},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    }
    if endpoint_url is not None:
        arguments["endpoint_url"] = endpoint_url
    if access_key_id is not None and secret_access_key is not None:
        arguments["aws_access_key_id"] = access_key_id
        arguments["aws_secret_access_key"] = secret_access_key
        if session_token is not None:
            arguments["aws_session_token"] = session_token
    return boto3.client(**arguments)


def _client_error_identity(exc: ClientError) -> tuple[str, int | None]:
    error = exc.response.get("Error") if isinstance(exc.response, dict) else None
    metadata = exc.response.get("ResponseMetadata") if isinstance(exc.response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    return str(code or "UNKNOWN"), status if isinstance(status, int) else None


def _raise_normalized_client_error(code: str, status: int | None) -> None:
    if code in _RETRYABLE_CODES or status == 429 or (status is not None and status >= 500):
        raise MediaStorageTransientError(
            "generated media object storage is temporarily unavailable"
        ) from None
    if code in {
        "AccessDenied",
        "AuthorizationHeaderMalformed",
        "InvalidAccessKeyId",
        "NoSuchBucket",
        "SignatureDoesNotMatch",
    }:
        raise MediaStorageConfigurationError(
            "generated media object storage configuration was rejected"
        ) from None
    raise MediaStorageError("generated media object storage rejected the request") from None


def _normalized_optional(value: object, *, strip_quotes: bool = False) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    return normalized.strip('"') if strip_quotes else normalized
