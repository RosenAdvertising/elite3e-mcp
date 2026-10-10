"""In-process checks for the stateless Streamable HTTP entry point."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.lowlevel.server import HandlerEntry

from elite3e_mcp import __version__, server
from elite3e_mcp.config import Settings
from elite3e_mcp.runtime import MCPServer as SdkMCPServer

PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"


def _modern_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    request_id: int = 1,
    client_name: str = "elite3e-http-test",
) -> tuple[dict[str, str], dict[str, Any]]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: protocol_version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": client_name, "version": "0"},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": protocol_version,
        "mcp-method": method,
    }
    if method in ("tools/call", "prompts/get"):
        headers["mcp-name"] = str(request_params["name"])
    elif method == "resources/read":
        headers["mcp-name"] = str(request_params["uri"])
    return headers, {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _payload(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        data_lines = [
            line[len("data:") :].strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert data_lines, response.text
        payload = json.loads(data_lines[-1])
    else:
        payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    return payload


def _result(response: httpx.Response) -> dict[str, Any]:
    payload = _payload(response)
    assert "result" in payload, payload
    return payload["result"]


@pytest.fixture(autouse=True)
def transport_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "ELITE3E_MCP_TRANSPORT",
        "ELITE3E_MCP_HOST",
        "PORT",
        "ELITE3E_MCP_ALLOWED_HOSTS",
        "ELITE3E_MCP_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(server, "_client", None)


@asynccontextmanager
async def _http_client():
    app = server.create_serve_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8080",
        ) as client,
    ):
        yield client


async def _post_modern(
    method: str,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    header_overrides = kwargs.pop("header_overrides", {})
    headers, body = _modern_request(method, params, **kwargs)
    headers.update(header_overrides)
    async with _http_client() as client:
        return await client.post("/mcp", headers=headers, json=body)


def _vendor_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("ELITE3E_BASE_URL", settings.base_url)
    monkeypatch.setenv("ELITE3E_TOKEN_URL", settings.token_url)
    monkeypatch.setenv("ELITE3E_CLIENT_ID", settings.client_id)
    monkeypatch.setenv("ELITE3E_CLIENT_SECRET", settings.client_secret)
    monkeypatch.setenv("ELITE3E_SCOPE", settings.auth_value)
    monkeypatch.delenv("ELITE3E_AUDIENCE", raising=False)


async def test_http_catalog_equals_stdio_server_catalog() -> None:
    async with Client(server.mcp, cache=None) as stdio_server:
        listed = await stdio_server.list_tools()
        expected = {tool.name: tool.input_schema for tool in listed.tools}
    response = await _post_modern("tools/list")
    # json_response stays at the SDK default. A fast tools/list still finishes as
    # JSON inside the SDK's deferral window; a slower call commits to SSE, and a
    # disconnect cancels that call.
    assert server.mcp._lowlevel_server._session_manager.json_response is False
    result = _result(response)
    actual = {tool["name"]: tool["inputSchema"] for tool in result["tools"]}
    assert actual == expected
    assert len(actual) == 14


async def test_read_tool_over_http_uses_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    _vendor_env(monkeypatch, settings)
    payload = {"matters": [{"id": "matter-1"}], "success": True, "message": None}
    with respx.mock(assert_all_called=True) as router:
        token_route = router.post(settings.token_url).mock(
            return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        )
        route = router.get(f"{settings.base_url}/api/v1/matter").mock(
            return_value=httpx.Response(200, json=payload)
        )
        try:
            result = _result(
                await _post_modern(
                    "tools/call",
                    {"name": "get_matters", "arguments": {"matter_id": "matter-1", "limit": 1}},
                    header_overrides={"authorization": "Bearer untrusted-request-secret"},
                )
            )
        finally:
            client = server._client
            if client is not None:
                await client.aclose()
    assert result["isError"] is False
    assert result["structuredContent"]["matters"] == [{"id": "matter-1"}]
    assert route.call_count == 1
    assert route.calls.last.request.headers["authorization"] == "Bearer token-1"
    assert "untrusted-request-secret" not in route.calls.last.request.headers["authorization"]
    form = parse_qs(token_route.calls.last.request.content.decode())
    assert form["client_id"] == [settings.client_id]
    assert form["client_secret"] == [settings.client_secret]
    assert form["grant_type"] == ["client_credentials"]
    assert form["scope"] == [settings.auth_value]


async def test_requests_share_no_session_and_lifespan_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lowlevel = server.mcp._lowlevel_server
    entry = lowlevel.get_request_handler("tools/list")
    assert entry is not None
    sessions: list[Any] = []
    states: list[Any] = []
    connection_states: list[tuple[str, dict[str, Any]]] = []
    events: list[str] = []
    original_lifespan = lowlevel.lifespan

    @asynccontextmanager
    async def observed_lifespan(app):
        events.append("start")
        async with original_lifespan(app) as state:
            yield state
        events.append("stop")

    async def observe(ctx, params):
        sentinel = ctx.request.headers["x-isolation-sentinel"]
        connection_state = ctx.session._connection.state
        connection_state[sentinel] = sentinel
        sessions.append(ctx.session)
        states.append(ctx.lifespan_context)
        connection_states.append((sentinel, connection_state))
        return await entry.handler(ctx, params)

    monkeypatch.setattr(lowlevel, "lifespan", observed_lifespan)
    monkeypatch.setitem(
        lowlevel._request_handlers,
        "tools/list",
        HandlerEntry(entry.params_type, observe),
    )
    async with _http_client() as client:
        posts = []
        for name in ("first", "second"):
            headers, body = _modern_request("tools/list", client_name=name)
            headers["x-isolation-sentinel"] = name
            posts.append(client.post("/mcp", headers=headers, json=body))
        responses = await asyncio.gather(*posts)
        assert events == ["start"]
    assert events == ["start", "stop"]
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert states[0] is states[1]
    assert len(connection_states) == 2
    by_sentinel = dict(connection_states)
    assert set(by_sentinel) == {"first", "second"}
    first_state = by_sentinel["first"]
    second_state = by_sentinel["second"]
    assert first_state is not second_state
    assert first_state.get("first") == "first"
    assert "second" not in first_state
    assert second_state.get("second") == "second"
    assert "first" not in second_state
    assert all("mcp-session-id" not in response.headers for response in responses)
    assert _result(responses[0]) == _result(responses[1])


def test_default_transport_keeps_existing_stdio_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(SdkMCPServer, "run", lambda self, **kwargs: calls.append(kwargs))
    assert server._requested_transport() == "stdio"
    server.main()
    assert calls == [{"transport": "stdio"}]


@pytest.mark.parametrize("value", ["", "  ", " STDIO "])
def test_transport_normalizes_stdio(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ELITE3E_MCP_TRANSPORT", value)
    assert server._requested_transport() == "stdio"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_transport_variable_starts_stdio(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """A set-but-empty or whitespace transport keeps stdio startup exactly as at BASE (F3)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(SdkMCPServer, "run", lambda self, **kwargs: calls.append(kwargs))
    monkeypatch.setenv("ELITE3E_MCP_TRANSPORT", value)
    server.main()
    assert calls == [{"transport": "stdio"}]


def test_unknown_transport_exits_with_both_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELITE3E_MCP_TRANSPORT", "bogus")
    with pytest.raises(
        SystemExit,
        match=r"ELITE3E_MCP_TRANSPORT.*bogus.*stdio.*streamable-http",
    ):
        server.main()


def test_http_transport_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def serve() -> None:
        calls.append("http")

    monkeypatch.setenv("ELITE3E_MCP_TRANSPORT", " STREAMABLE-HTTP ")
    monkeypatch.setattr(server, "_serve_streamable_http", serve)
    server.main()
    assert calls == ["http"]


def test_host_and_port_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    assert server._host() == "127.0.0.1"
    assert server._port() == 8080
    monkeypatch.setenv("ELITE3E_MCP_HOST", " 0.0.0.0 ")
    monkeypatch.setenv("PORT", " 9000 ")
    assert server._host() == "0.0.0.0"
    assert server._port() == 9000


def test_invalid_port_exits_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "bogus")
    with pytest.raises(SystemExit, match="PORT must be an integer"):
        server._port()


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_host_variable_yields_loopback(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """A set-but-empty or whitespace host yields 127.0.0.1, never an empty bind (F4)."""
    monkeypatch.setenv("ELITE3E_MCP_HOST", value)
    assert server._host() == "127.0.0.1"
    assert server._transport_security() is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_uses_sdk_security_defaults(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("ELITE3E_MCP_HOST", host)
    assert server._transport_security() is None


def test_uppercase_localhost_is_not_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """_host() keeps the caller's spelling, so LOCALHOST exits without allowed hosts (F7)."""
    monkeypatch.setenv("ELITE3E_MCP_HOST", "LOCALHOST")
    assert server._host() == "LOCALHOST"
    with pytest.raises(SystemExit, match="ELITE3E_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


def test_server_imports_without_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the server survives a distribution metadata read that raises (F8)."""

    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version("elite3e-mcp")
    spec = importlib.util.spec_from_file_location(
        "elite3e_mcp_server_without_distribution", Path(server.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__version__ == __version__


@pytest.mark.parametrize("allowed_hosts", [None, "", " , "])
def test_non_loopback_requires_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
    allowed_hosts: str | None,
) -> None:
    monkeypatch.setenv("ELITE3E_MCP_HOST", "0.0.0.0")
    if allowed_hosts is not None:
        monkeypatch.setenv("ELITE3E_MCP_ALLOWED_HOSTS", allowed_hosts)
    with pytest.raises(SystemExit, match="ELITE3E_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


async def test_explicit_host_and_origin_allowlists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELITE3E_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("ELITE3E_MCP_ALLOWED_HOSTS", " 127.0.0.1:8080, mcp.example.test:* , ")
    monkeypatch.setenv("ELITE3E_MCP_ALLOWED_ORIGINS", " https://app.example.test, ")
    settings = server._transport_security()
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["127.0.0.1:8080", "mcp.example.test:*"]
    assert settings.allowed_origins == ["https://app.example.test"]
    async with _http_client() as client:
        headers, body = _modern_request("tools/list")
        bad_host = await client.post("/mcp", headers={**headers, "host": "evil.test"}, json=body)
        bad_origin = await client.post(
            "/mcp",
            headers={**headers, "origin": "https://evil.test"},
            json=body,
        )
        good_origin = await client.post(
            "/mcp",
            headers={**headers, "origin": "https://app.example.test"},
            json=body,
        )
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403
    assert len(_result(good_origin)["tools"]) == 14


async def test_origin_is_rejected_when_optional_allowlist_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELITE3E_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("ELITE3E_MCP_ALLOWED_HOSTS", "127.0.0.1:8080")
    response = await _post_modern(
        "tools/list",
        header_overrides={"origin": "https://evil.test"},
    )
    assert response.status_code == 403


async def test_loopback_rejects_untrusted_host_and_origin() -> None:
    bad_host = await _post_modern("tools/list", header_overrides={"host": "evil.test"})
    bad_origin = await _post_modern(
        "tools/list",
        header_overrides={"origin": "https://evil.test"},
    )
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_http_rejects_get_and_delete(method: str) -> None:
    async with _http_client() as client:
        response = await client.request(
            method,
            "/mcp",
            headers={"mcp-protocol-version": PROTOCOL_VERSION},
        )
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_discovery_has_protocol_and_complete_server_identity() -> None:
    response = await _post_modern("server/discover")
    result = _result(response)
    assert "mcp-session-id" not in response.headers
    assert PROTOCOL_VERSION in result["supportedVersions"]
    info = result["_meta"][SERVER_INFO_META_KEY]
    assert info["name"] == "elite3e"
    assert info["title"] == "Thomson Reuters Elite 3E"
    assert info["version"] == __version__
    assert info["version"]


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("get_matters", {"limit": 1}),
        ("create_pending_timecard", {"data_collection": {"id": "new"}, "confirm": True}),
        (
            "update_pending_timecard",
            {"data_collection": {"id": "pending-1"}, "confirm": True},
        ),
    ],
)
async def test_cancelled_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    class CancelledClient:
        def __getattr__(self, _name: str):
            async def cancel(*_args: object, **_kwargs: object) -> None:
                raise asyncio.CancelledError

            return cancel

    monkeypatch.setattr(server, "_client", CancelledClient())
    with pytest.raises(asyncio.CancelledError):
        await server.mcp.call_tool(tool_name, arguments)
