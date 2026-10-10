"""Regression probes for credential destination validation."""

from dataclasses import replace

import pytest

from elite3e_mcp.config import ConfigError


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/api",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:secret@example.test/api",
        "https://example.test/api?q=secret",
        "https://example.test/api#secret",
        "https://example.test/api?",
        "https://example.test/api#",
        "https://:443",
        "https://example.test:bad",
        "https://example.test:99999",
        "https://example.test\\@evil.test",
        "https://example.test\n/path",
        "https://%65xample.test",
        "https://example.test:",
    ],
)
@pytest.mark.parametrize("field", ["base_url", "token_url"])
def test_credential_urls_reject_review_probes(settings, field, url):
    with pytest.raises(ConfigError, match="HTTPS"):
        replace(settings, **{field: url})


def test_arbitrary_user_header_probe(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    from elite3e_mcp.server import _common_headers

    monkeypatch.delenv("ELITE3E_ALLOWED_USER_IDS", raising=False)
    with pytest.raises(ToolError, match="ELITE3E_ALLOWED_USER_IDS"):
        _common_headers(None, "other-user-id", None)
    monkeypatch.setenv("ELITE3E_ALLOWED_USER_IDS", "approved-user, second-user")
    assert _common_headers(None, "approved-user", None) == {"X-3E-UserId": "approved-user"}
    for value in ["other-user-id", "Approved-user", "approved-user,other-user-id", ""]:
        with pytest.raises(ToolError, match="ELITE3E_ALLOWED_USER_IDS"):
            _common_headers(None, value, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["X-3E-UserId", "x-3e-userid", "X-3e-USERID"])
async def test_direct_header_denied_before_auth(settings, header):
    import httpx

    from elite3e_mcp.client import Elite3EClient

    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = Elite3EClient(settings, http_client=http)
        with pytest.raises(ValueError, match="ELITE3E_ALLOWED_USER_IDS"):
            await client.request_json("GET", "/TimeCardPending", headers={header: "other-user-id"})
    assert sent == []


@pytest.mark.asyncio
async def test_allowlisted_header_is_sent(settings):
    import httpx

    from elite3e_mcp.client import Elite3EClient

    sent = []

    def respond(request):
        sent.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = Elite3EClient(
            replace(settings, allowed_user_ids=frozenset({"approved-user"})), http_client=http
        )
        await client.request_json(
            "GET", "/TimeCardPending", headers={"X-3E-UserId": "approved-user"}
        )
    assert len(sent) == 2
    assert sent[1].headers["X-3E-UserId"] == "approved-user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("get_pending_timecard_template", {}),
        ("create_pending_timecard", {"data_collection": {}, "confirm": True}),
        ("update_pending_timecard", {"data_collection": {}, "confirm": True}),
    ],
)
async def test_mcp_arbitrary_identity_denied(monkeypatch, tool, arguments):
    from unittest.mock import AsyncMock

    from mcp.server.mcpserver.exceptions import ToolError

    from elite3e_mcp import server

    client = AsyncMock()
    monkeypatch.setattr(server, "_get_client", lambda: client)
    monkeypatch.setenv("ELITE3E_ALLOWED_USER_IDS", "approved-user")
    with pytest.raises(ToolError, match="ELITE3E_ALLOWED_USER_IDS"):
        await server.mcp.call_tool(tool, {**arguments, "x_3e_user_id": "other-user-id"})
    client.request_json.assert_not_awaited()
    client.request_json.return_value = {"success": True}
    await server.mcp.call_tool(tool, {**arguments, "x_3e_user_id": "approved-user"})
    assert client.request_json.await_args.kwargs["headers"]["X-3E-UserId"] == "approved-user"


def test_administrator_allowlist_loaded(monkeypatch):
    from elite3e_mcp.config import Settings

    for name, value in {
        "BASE_URL": "https://tenant.example/api",
        "TOKEN_URL": "https://login.example/token",
        "CLIENT_ID": "synthetic",
        "CLIENT_SECRET": "synthetic",
        "SCOPE": "integration",
        "ALLOWED_USER_IDS": "approved-user, second-user",
    }.items():
        monkeypatch.setenv("ELITE3E_" + name, value)
    monkeypatch.delenv("ELITE3E_AUDIENCE", raising=False)
    assert Settings.from_env().allowed_user_ids == frozenset({"approved-user", "second-user"})
