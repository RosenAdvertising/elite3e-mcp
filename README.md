# Elite 3E MCP

[![CI](https://github.com/RosenAdvertising/elite3e-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/elite3e-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An MCP server for selected Thomson Reuters Elite 3E REST API data and pending timecard workflows. It uses stdio transport and the official MCP Python SDK. The server currently registers 14 tools.

## Features

- Search matters, clients, people and organizations, timekeepers, and new-business intakes.
- Read posted and pending timecards, time calendar reports, posted and pending cost cards, and attachment metadata.
- Retrieve a tenant-specific pending-timecard template, then create or update pending timecards only when the caller sets `confirm=true`.
- Filter supported searches by IDs, numbers, tenant attributes, child objects, and advanced filters.
- Preserve the vendor response envelope and report local result limits. Collection calls make one request; the public REST specification does not define pagination fields, so the server does not invent them.
- Enforce the documented Cloud rate limit of 30 REST calls per 60-second sliding window and retry throttled, server-error, and transport failures with bounded backoff.
- Cache OAuth access tokens until shortly before their returned expiry. Tool failures return actionable messages without vendor error details; empty and `204` responses remain structured JSON.
- Support optional `X-3E-SessionId`, `X-3E-UserId`, and `Accept-Language` headers on pending-timecard operations. Elite documents `X-3E-UserId` for Integration scope; confirm the tenant's authorization requirements before using it.

## Requirements

- Python 3.12
- An MCP-compatible client
- Customer-issued Elite 3E OAuth client credentials and API instance details

The package targets the MCP Python SDK `>=2.3,<3` and protocol revision `2026-07-28`.

## Install from source

Install [uv](https://docs.astral.sh/uv/) and clone this repository, then run:

```bash
uv sync --locked
```

This installs the project and its command-line entry point in the local environment.

## Configuration

Set these variables in the environment used to start the MCP server:

| Variable | Required | Description |
|---|---:|---|
| `ELITE3E_BASE_URL` | Yes | Customer-issued REST base URL, including instance routing and `/3eapi` when instructed. |
| `ELITE3E_TOKEN_URL` | Yes | Customer-issued OAuth 2.0 token URL. |
| `ELITE3E_CLIENT_ID` | Yes | OAuth application/client ID. |
| `ELITE3E_CLIENT_SECRET` | Yes | OAuth client secret. |
| `ELITE3E_SCOPE` | One of two | Tenant-issued OAuth scope. Set exactly one of scope or audience. |
| `ELITE3E_AUDIENCE` | One of two | Legacy tenant-issued OAuth audience. Set exactly one of scope or audience. |
| `ELITE3E_TIMEOUT_SECONDS` | No | HTTP timeout in seconds; defaults to `30`. |
| `ELITE3E_MAX_RETRIES` | No | Additional retries after throttling, server errors, or transport errors; defaults to `3`. |
| `ELITE3E_BACKOFF_SECONDS` | No | Initial exponential-backoff delay; defaults to `0.5`. |

There are no guessed defaults for the API URL, OAuth URL, scope, or audience. Use the values and URL construction supplied for the target tenant. `.env.example` contains placeholders only; never commit real credentials.

The server reads credentials from its process environment and does not provide a credential setup wizard. Use the MCP host's protected environment configuration to supply them.

## MCP client configuration

For an MCP client that accepts JSON server configuration, use `uv` and the path where you cloned the repository. Supply the required environment variables through the client's environment configuration:

```json
{
  "mcpServers": {
    "elite3e": {
      "command": "uv",
      "args": ["--directory", "/path/to/elite3e-mcp", "run", "elite3e-mcp"],
      "env": {
        "ELITE3E_BASE_URL": "https://your-tenant.example/3eapi",
        "ELITE3E_TOKEN_URL": "https://identity.example/oauth2/token",
        "ELITE3E_CLIENT_ID": "your-client-id",
        "ELITE3E_CLIENT_SECRET": "your-client-secret",
        "ELITE3E_SCOPE": "your-tenant-issued-scope"
      }
    }
  }
}
```

Replace the example values with tenant-issued settings and keep real secrets in the MCP client's protected configuration. Set `ELITE3E_AUDIENCE` instead of `ELITE3E_SCOPE` only when the tenant package specifies the legacy audience form.

## HTTP mode

Stdio remains the default. To serve stateless Streamable HTTP instead, set `ELITE3E_MCP_TRANSPORT=streamable-http`. Each request is one POST to `/mcp`. The process reads vendor credentials from the same environment variables as stdio and never from the request.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write and delete tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `ELITE3E_MCP_ALLOWED_HOSTS` and `ELITE3E_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Required | Description |
|---|---:|---|
| `ELITE3E_MCP_TRANSPORT` | No | `stdio` (default) or `streamable-http`. |
| `ELITE3E_MCP_HOST` | No | Bind address. Defaults to `127.0.0.1`. |
| `PORT` | No | Bind port. Defaults to `8080`. A non-integer value exits. |
| `ELITE3E_MCP_ALLOWED_HOSTS` | Non-loopback only | Comma-separated `Host` values. Required when `ELITE3E_MCP_HOST` is not `127.0.0.1`, `localhost`, or `::1`. |
| `ELITE3E_MCP_ALLOWED_ORIGINS` | No | Comma-separated `Origin` values. When set, any other `Origin` is rejected. |
| `ELITE3E_BASE_URL` | Yes | Customer-issued REST base URL, including instance routing and `/3eapi` when instructed. |
| `ELITE3E_TOKEN_URL` | Yes | Customer-issued OAuth 2.0 token URL. |
| `ELITE3E_CLIENT_ID` | Yes | OAuth application/client ID. |
| `ELITE3E_CLIENT_SECRET` | Yes | OAuth client secret. |
| `ELITE3E_SCOPE` | One of two | Tenant-issued OAuth scope. Set exactly one of scope or audience. |
| `ELITE3E_AUDIENCE` | One of two | Legacy tenant-issued OAuth audience. Set exactly one of scope or audience. |
| `ELITE3E_TIMEOUT_SECONDS` | No | HTTP timeout in seconds; defaults to `30`. |
| `ELITE3E_MAX_RETRIES` | No | Additional retries after throttling, server errors, or transport errors; defaults to `3`. |
| `ELITE3E_BACKOFF_SECONDS` | No | Initial exponential-backoff delay; defaults to `0.5`. |
| `ELITE3E_ALLOWED_USER_IDS` | No | Comma-separated user IDs allowed for `X-3E-UserId`. Empty denies delegation. |

```bash
ELITE3E_MCP_TRANSPORT=streamable-http \
ELITE3E_MCP_HOST=127.0.0.1 \
PORT=8080 \
ELITE3E_BASE_URL="https://your-tenant.example/3eapi" \
ELITE3E_TOKEN_URL="https://identity.example/oauth2/token" \
ELITE3E_CLIENT_ID="your-client-id" \
ELITE3E_CLIENT_SECRET="your-client-secret" \
ELITE3E_SCOPE="your-tenant-issued-scope" \
uv run elite3e-mcp
```

The endpoint is `http://127.0.0.1:8080/mcp`. Clients POST JSON-RPC there. On a loopback bind, Host and Origin checks use the SDK's loopback rules. On any other bind, set `ELITE3E_MCP_ALLOWED_HOSTS` before starting.

## Safe pending-time workflow

1. Call `get_pending_timecard_template` to retrieve the target tenant's template.
2. Prepare the `dataCollection` using tenant-understood fields and values.
3. Review the complete object, then call `create_pending_timecard` with `confirm=true`.
4. For later changes, submit the existing object to `update_pending_timecard` with `confirm=true`.

Retrieving a template does not create a record. Tenant attributes and child objects are dynamic, so use the tenant's schema and returned template instead of assuming field IDs or defaults.

## Development

The test suite uses mocked HTTP requests and does not require live credentials:

```bash
uv run --locked pytest -q
uv run --locked ruff check .
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Local setup and verification

The server starts and answers MCP initialization and tool listing without credentials.
Tools report a configuration error until the required environment is provided.
Run `elite3e-mcp-setup` for environment-variable instructions, then restart your MCP
launcher with those variables exported. Run `elite3e-mcp-verify` to validate local
configuration (exit 2 if missing or invalid). This command does not contact the
vendor or prove authentication. Neither command saves or prints secret values.

`ELITE3E_BASE_URL` and `ELITE3E_TOKEN_URL` must use HTTPS without userinfo,
query or fragment. Set these to your administrator-approved tenant and identity provider.

Per-call `x_3e_user_id` / `X-3E-UserId` delegation is denied by default.
An administrator can set `ELITE3E_ALLOWED_USER_IDS` to a comma-separated list of
exact user IDs authorized for this integration. Every override must match an entry;
tool arguments cannot grant additional identities. Restart after changing configuration.
