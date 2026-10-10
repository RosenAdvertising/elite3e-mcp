"""Offline HTTP-client behavior tests."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from elite3e_mcp.client import Elite3EAPIError, Elite3EClient
from elite3e_mcp.config import Settings


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_authenticates_and_returns_json(settings: Settings) -> None:
    token_route = respx.post(settings.token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
    )
    api_route = respx.get(f"{settings.base_url}/api/v1/matter").mock(
        return_value=httpx.Response(200, json={"matters": [{"id": "m-1"}], "success": True})
    )

    async with Elite3EClient(settings) as client:
        result = await client.get("api/v1/matter", params={"MatterId": "m-1"})

    assert result["matters"] == [{"id": "m-1"}]
    token_form = parse_qs(token_route.calls[0].request.content.decode())
    assert token_form == {
        "client_id": ["test-client-id"],
        "client_secret": ["test-client-secret"],
        "grant_type": ["client_credentials"],
        "scope": ["api://elite/.default"],
    }
    assert api_route.calls[0].request.headers["Authorization"] == "Bearer token-1"
    assert api_route.calls[0].request.url.params["MatterId"] == "m-1"


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_surfaces_identity_provider_message(settings: Settings) -> None:
    respx.post(settings.token_url).mock(
        return_value=httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "The tenant rejected this client secret.",
            },
        )
    )

    async with Elite3EClient(settings) as client:
        with pytest.raises(Elite3EAPIError, match="tenant rejected this client secret") as exc:
            await client.get("api/v1/client")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
@respx.mock
async def test_429_is_retried_and_vendor_error_is_not_swallowed(settings: Settings) -> None:
    respx.post(settings.token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
    )
    api_route = respx.get(f"{settings.base_url}/api/v1/timekeeper").mock(
        side_effect=[
            httpx.Response(429, json={"detail": "Cloud request limit reached"}),
            httpx.Response(200, json={"timekeepers": [{"id": "tk-1"}], "success": True}),
        ]
    )

    async with Elite3EClient(settings) as client:
        result = await client.get("api/v1/timekeeper")

    assert result["timekeepers"][0]["id"] == "tk-1"
    assert api_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_pagination_uses_explicit_link_field_and_honors_limit(settings: Settings) -> None:
    respx.post(settings.token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
    )
    first = respx.get(f"{settings.base_url}/odata/records", params={"cursor": "first"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "records": [{"id": 1}, {"id": 2}],
                "tenantNextLink": f"{settings.base_url}/odata/records?cursor=verified-value",
            },
        )
    )
    second = respx.get(
        f"{settings.base_url}/odata/records", params={"cursor": "verified-value"}
    ).mock(return_value=httpx.Response(200, json={"records": [{"id": 3}, {"id": 4}]}))

    async with Elite3EClient(settings) as client:
        records = [
            record
            async for record in client.paginate(
                "odata/records",
                items_field="records",
                next_link_field="tenantNextLink",
                params={"cursor": "first"},
                limit=3,
            )
        ]

    assert records == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert first.call_count == 1
    assert second.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_final_5xx_surfaces_problem_details(settings: Settings) -> None:
    failing_settings = replace(settings, max_retries=0)
    respx.post(failing_settings.token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
    )
    respx.get(f"{failing_settings.base_url}/api/v1/client").mock(
        return_value=httpx.Response(500, json={"detail": "Elite process failed"})
    )

    async with Elite3EClient(failing_settings) as client:
        with pytest.raises(Elite3EAPIError, match="Elite process failed"):
            await client.get("api/v1/client")
