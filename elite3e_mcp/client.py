"""Thin asynchronous HTTP client for the Elite 3E REST API."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from elite3e_mcp.config import Settings

REST_CALLS_PER_MINUTE = 30
MAX_PAGE_SIZE = 5_000


class Elite3EAPIError(RuntimeError):
    """An HTTP or payload error returned by Elite 3E or its identity provider."""

    def __init__(self, status_code: int, message: str, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Elite 3E API error ({status_code}): {message}")


class SlidingWindowRateLimiter:
    """Keep REST traffic within the documented 30-call-per-minute Cloud limit."""

    def __init__(self, calls: int = REST_CALLS_PER_MINUTE, period_seconds: float = 60.0) -> None:
        self.calls = calls
        self.period_seconds = period_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until one call is available in the current sliding window."""
        async with self._lock:
            while True:
                now = time.monotonic()
                cutoff = now - self.period_seconds
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.calls:
                    self._timestamps.append(now)
                    return
                await asyncio.sleep(max(0.0, self.period_seconds - (now - self._timestamps[0])))


def _error_details(response: httpx.Response) -> tuple[str, Any]:
    payload: Any = None
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        text = response.text.strip()
        return text or response.reason_phrase or "Request failed", None

    if isinstance(payload, Mapping):
        for key in ("detail", "title", "message", "error_description", "error"):
            value = payload.get(key)
            if value:
                if isinstance(value, str):
                    return value, payload
                return json.dumps(value, ensure_ascii=False), payload
    return json.dumps(payload, ensure_ascii=False), payload


class Elite3EClient:
    """Authenticate, retry, rate-limit, and decode Elite 3E HTTP requests."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self._http = http_client or httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            headers={"Accept": "application/json"},
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter()
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> Elite3EClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned HTTP connection pool."""
        if self._owns_http_client:
            await self._http.aclose()

    async def _send_with_retries(
        self,
        method: str,
        url: str,
        *,
        rate_limited: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(self.settings.max_retries + 1):
            if rate_limited:
                await self._rate_limiter.acquire()
            try:
                response = await self._http.request(method, url, **kwargs)
            except httpx.RequestError:
                if attempt >= self.settings.max_retries:
                    raise
                await asyncio.sleep(self.settings.backoff_seconds * (2**attempt))
                continue

            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt >= self.settings.max_retries:
                return response

            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after is not None else None
            except ValueError:
                delay = None
            if delay is None:
                delay = self.settings.backoff_seconds * (2**attempt)
            await asyncio.sleep(min(max(delay, 0.0), 60.0))

        raise AssertionError("retry loop exited without a response")  # pragma: no cover

    async def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        async with self._token_lock:
            now = time.monotonic()
            if self._access_token and now < self._token_expires_at:
                return self._access_token

            form = {
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "client_credentials",
                self.settings.auth_field: self.settings.auth_value,
            }
            response = await self._send_with_retries(
                "POST",
                self.settings.token_url,
                rate_limited=False,
                data=form,
                headers={"Accept": "application/json"},
            )
            if response.is_error:
                message, payload = _error_details(response)
                raise Elite3EAPIError(response.status_code, message, payload)
            try:
                token_payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise Elite3EAPIError(
                    response.status_code,
                    response.text.strip() or "Token response was not valid JSON",
                ) from exc

            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise Elite3EAPIError(
                    response.status_code,
                    str(
                        token_payload.get("error_description")
                        or "Token response omitted access_token"
                    ),
                    token_payload,
                )

            expires_in = token_payload.get("expires_in")
            try:
                lifetime = max(0.0, float(expires_in))
            except (TypeError, ValueError):
                lifetime = 0.0
            self._access_token = access_token
            self._token_expires_at = time.monotonic() + max(
                0.0, lifetime - min(30.0, lifetime / 10)
            )
            return access_token

    def _api_url(self, path_or_url: str) -> str:
        base = self.settings.base_url + "/"
        candidate = urljoin(base, path_or_url.lstrip("/"))
        if path_or_url.startswith(("http://", "https://")):
            candidate = path_or_url

        base_parts = urlparse(base)
        candidate_parts = urlparse(candidate)
        if (candidate_parts.scheme, candidate_parts.netloc) != (
            base_parts.scheme,
            base_parts.netloc,
        ):
            raise ValueError("Elite 3E continuation URL must use the configured API origin")
        return candidate

    async def request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make one authenticated REST request and return its structured JSON body."""
        for key, value in (headers or {}).items():
            if key.lower() == "x-3e-userid" and value not in self.settings.allowed_user_ids:
                raise ValueError("X-3E-UserId must be allowed by ELITE3E_ALLOWED_USER_IDS.")
        token = await self._get_access_token()
        request_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            request_headers.update(headers)
        kwargs: dict[str, Any] = {"headers": request_headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body

        response = await self._send_with_retries(
            method,
            self._api_url(path_or_url),
            rate_limited=True,
            **kwargs,
        )
        if response.is_error:
            message, payload = _error_details(response)
            raise Elite3EAPIError(response.status_code, message, payload)
        if response.status_code == 204 or not response.content:
            return {"success": True, "message": None, "status": response.status_code}
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise Elite3EAPIError(
                response.status_code,
                response.text.strip() or "Response was not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise Elite3EAPIError(
                response.status_code,
                "Expected a JSON object response",
                payload,
            )
        return payload

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send a GET request."""
        return await self.request_json("GET", path, params=params)

    async def paginate(
        self,
        path: str,
        *,
        items_field: str,
        next_link_field: str,
        params: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield explicitly configured continuation pages without assuming 3E's link property."""
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        next_url: str | None = path
        next_params = params
        yielded = 0
        while next_url and yielded < limit:
            page = await self.get(next_url, params=next_params)
            next_params = None
            items = page.get(items_field, [])
            if not isinstance(items, list):
                raise Elite3EAPIError(200, f"Response field {items_field!r} was not a list", page)
            for item in items:
                if yielded >= limit:
                    return
                if not isinstance(item, dict):
                    raise Elite3EAPIError(
                        200, f"Response field {items_field!r} contained a non-object", page
                    )
                yielded += 1
                yield item
            raw_next = page.get(next_link_field)
            if raw_next is None:
                return
            if not isinstance(raw_next, str) or not raw_next:
                raise Elite3EAPIError(
                    200, f"Response field {next_link_field!r} was not a URL", page
                )
            next_url = self._api_url(raw_next)
