"""Lazy client creation and a safe boundary for MCP tool failures."""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from elite3e_mcp.client import Elite3EAPIError, Elite3EClient
from elite3e_mcp.config import ConfigError, Settings

CONFIGURATION_HELP = (
    "elite3e is not configured or its configuration is invalid. "
    "Set ELITE3E_BASE_URL, ELITE3E_TOKEN_URL, ELITE3E_CLIENT_ID, ELITE3E_CLIENT_SECRET, "
    "and exactly one of ELITE3E_SCOPE or ELITE3E_AUDIENCE. "
    "Run elite3e-mcp-setup for instructions, then restart the MCP server."
)


class LazyClient:
    """Create a client only on tool use, never during initialize or tools/list."""

    def __init__(self) -> None:
        self._client: Elite3EClient | None = None

    def __getattr__(self, name: str) -> Any:
        if self._client is None:
            self._client = Elite3EClient(Settings.from_env())
        return getattr(self._client, name)

    async def __aenter__(self) -> LazyClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
            self._client = None


def safe_error(exc: Exception) -> ToolError:
    """Map error types/status codes; never interpolate exception or vendor text."""
    if isinstance(exc, ConfigError):
        return ToolError(CONFIGURATION_HELP)
    if isinstance(exc, Elite3EAPIError):
        status = exc.status_code
        if status == 401:
            message = "Authentication failed. Check credentials with elite3e-mcp-setup."
        elif status == 403:
            message = "Access denied. Check account permissions and API scopes for this operation."
        elif status == 404:
            message = "Resource not found. Check the record ID and account access."
        elif status == 429:
            message = "Rate limit reached. Wait before retrying; check any write result first."
        elif isinstance(status, int) and 500 <= status <= 599:
            message = "Service unavailable. Check whether any write completed before retrying."
        else:
            message = "Request rejected or response invalid. Check inputs and API configuration."
        return ToolError(message)
    if isinstance(exc, httpx.RequestError):
        return ToolError(
            "Connection failed; the operation outcome may be unknown. "
            "Check connectivity and whether any write completed before retrying."
        )
    if type(exc) is ToolError:
        return exc
    if isinstance(exc, ValueError):
        return ToolError(
            "Invalid tool input or response. Check the tool schema and supplied values."
        )
    return ToolError(
        "The operation could not be completed. Check configuration and service availability; "
        "check whether any write completed before retrying."
    )


class SafeMCPServer(MCPServer):
    """Translate failures before the SDK logs or returns their private details."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as exc:
            raise safe_error(exc.__cause__ or exc) from None
        except ToolError as exc:
            # The SDK wraps deliberate ToolErrors; classify the original typed failure.
            raise safe_error(exc.__cause__ or exc) from None
        except Exception as exc:
            raise safe_error(exc) from None
