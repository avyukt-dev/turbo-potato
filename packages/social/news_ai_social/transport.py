"""Narrow, secret-safe HTTP transport for Instagram Graph operations."""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class GraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int = Field(ge=100, le=599)
    payload: dict[str, Any]
    retry_after_seconds: int | None = Field(default=None, ge=0)


class GraphTransportError(RuntimeError):
    def __init__(self, message: str, *, outcome_may_be_ambiguous: bool) -> None:
        super().__init__(message)
        self.outcome_may_be_ambiguous = outcome_may_be_ambiguous


class InstagramGraphTransport(Protocol):
    async def get(self, path: str, *, params: dict[str, str]) -> GraphResponse: ...

    async def post(self, path: str, *, data: dict[str, str]) -> GraphResponse: ...


class HttpxInstagramGraphTransport:
    """Authenticated Graph client. It performs no retries at the side-effect boundary."""

    def __init__(
        self,
        *,
        graph_base_url: str,
        api_version: str,
        access_token: SecretStr,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = f"{graph_base_url.rstrip('/')}/{api_version}"
        self._access_token = access_token
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client
        self._owns_client = client is None

    def __repr__(self) -> str:
        return f"HttpxInstagramGraphTransport(base_url={self._base_url!r})"

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token.get_secret_value()}"}

    async def get(self, path: str, *, params: dict[str, str]) -> GraphResponse:
        return await self._request("GET", path, params=params, side_effect=False)

    async def post(self, path: str, *, data: dict[str, str]) -> GraphResponse:
        return await self._request("POST", path, data=data, side_effect=True)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        side_effect: bool,
    ) -> GraphResponse:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            response = await self._http_client().request(
                method,
                url,
                params=params,
                data=data,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GraphTransportError(
                "Instagram Graph endpoint is unavailable", outcome_may_be_ambiguous=False
            ) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            raise GraphTransportError(
                "Instagram Graph response outcome is unknown",
                outcome_may_be_ambiguous=side_effect,
            ) from exc
        except httpx.RequestError as exc:
            raise GraphTransportError(
                "Instagram Graph request failed",
                outcome_may_be_ambiguous=side_effect,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphTransportError(
                "Instagram Graph returned invalid JSON",
                outcome_may_be_ambiguous=side_effect,
            ) from exc
        if not isinstance(payload, dict):
            raise GraphTransportError(
                "Instagram Graph returned an invalid response object",
                outcome_may_be_ambiguous=side_effect,
            )
        retry_after = _parse_retry_after(response.headers.get("retry-after"))
        return GraphResponse(
            status_code=response.status_code,
            payload=payload,
            retry_after_seconds=retry_after,
        )


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
