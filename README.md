# Elite 3E MCP server

[![CI](https://github.com/RosenAdvertising/elite3e-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/elite3e-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-2026--07--28-7C3AED.svg)](https://modelcontextprotocol.io)

Connect Claude and other MCP clients to Elite 3E to search matters, clients and timekeepers, read timecards and cost cards, and manage pending timecards.

Elite 3E MCP server is a [Model Context Protocol](https://modelcontextprotocol.io) server for Thomson Reuters Elite 3E, a law-firm business management system. It registers 14 tools: 12 that read data and 2 that create or update pending timecards, which require `confirm=true`. It runs over stdio by default, for desktop clients such as Claude Desktop, and offers an opt-in stateless Streamable HTTP mode that implements MCP specification 2026-07-28. Elite 3E credentials stay on the machine that runs the server: they come from environment variables, never from the client. The server covers selected Elite 3E REST API data and the pending-timecard workflow.

## Features

- Search matters, clients, people and organizations, timekeepers, and new-business intakes.
- Read posted and pending timecards, time calendar reports, posted and pending cost cards, and attachment metadata.
- Retrieve a tenant-specific pending-timecard template, then create or update pending timecards only when the caller sets `confirm=true`.
- Filter supported searches by IDs, numbers, tenant attributes, child objects, and advanced filters.
- Preserve the vendor response envelope and report local result limits. Collection calls make one request; the public REST specification does not define pagination fields, so the server does not invent them.
- Enforce the documented Cloud rate limit of 30 REST calls per 60-second sliding window and retry throttled, server-error, and transport failures with bounded backoff.
- Cache OAuth access tokens until shortly before their returned expiry. Tool failures return actionable messages without vendor error details; empty and `204` responses remain structured JSON.
- Support optional `X-3E-SessionId`, `X-3E-UserId`, and `Accept-Language` headers on pending-timecard operations. Elite documents `X-3E-UserId` for Integration scope; confirm the tenant's authorization requirements before using it.

## Tools

The server registers 14 tools.

| Tool | What it does |
| --- | --- |
| `get_matters` | Find matters by record ID, internal index, number, or an advanced filter. |
| `get_clients` | Find billing or marketing client records by ID, number, or an advanced filter. |
| `get_entities` | Search people and organizations used as contacts and master records. |
| `get_timekeepers` | Find attorneys, paralegals, and other billable personnel. |
| `get_new_business_intakes` | Find legacy conflicts and new-business-intake searches. |
| `get_posted_timecards` | Find posted time entries by date, card, or timekeeper. |
| `get_pending_timecards` | Find editable unposted time entries by date, card, or timekeeper. |
| `get_time_calendar_report` | Show submitted time, targets, and holidays for a timekeeper and date range. |
| `get_posted_cost_cards` | Find posted matter expenses by date, card, timekeeper, matter, or type. |
| `get_pending_cost_cards` | Find editable unposted matter expenses by date, card, timekeeper, matter, or type. |
| `get_attachments` | List file attachments for one or more 3E records without downloading them. |
| `get_pending_timecard_template` | Get the tenant-specific starting object needed to create a pending time entry. |
| `create_pending_timecard` | Create an editable pending time entry from a template-derived object. Requires `confirm=true`. |
| `update_pending_timecard` | Update an existing editable pending time entry. Requires `confirm=true`. |

See [Safe pending-time workflow](#safe-pending-time-workflow) for the order to call the last three.

## Requirements

- Python 3.12
- An MCP client such as Claude Desktop
- Customer-issued Elite 3E OAuth client credentials and API instance details

## Installation

Install [uv](https://docs.astral.sh/uv/), then clone the repository and install its locked dependencies:

```bash
git clone https://github.com/RosenAdvertising/elite3e-mcp.git
cd elite3e-mcp
uv sync --locked
```

## Configuration

Elite 3E credentials come from environment variables. Set them in the environment that launches the server, such as the `env` object in your MCP client configuration.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `ELITE3E_BASE_URL` | Yes | None | Customer-issued REST base URL, including instance routing and `/3eapi` when instructed. Must use HTTPS, without userinfo, query or fragment. |
| `ELITE3E_TOKEN_URL` | Yes | None | Customer-issued OAuth 2.0 token URL. Same HTTPS rules as the base URL. |
| `ELITE3E_CLIENT_ID` | Yes | None | OAuth application/client ID. |
| `ELITE3E_CLIENT_SECRET` | Yes | None | OAuth client secret. |
| `ELITE3E_SCOPE` | One of two | None | Tenant-issued OAuth scope. Set exactly one of scope or audience. |
| `ELITE3E_AUDIENCE` | One of two | None | Legacy tenant-issued OAuth audience. Set exactly one of scope or audience. |
| `ELITE3E_TIMEOUT_SECONDS` | No | `30` | HTTP timeout in seconds; a positive number. |
| `ELITE3E_MAX_RETRIES` | No | `3` | Additional retries after throttling, server errors or transport errors; a whole number, zero or more. |
| `ELITE3E_BACKOFF_SECONDS` | No | `0.5` | Initial exponential-backoff delay in seconds; a positive number. |
| `ELITE3E_ALLOWED_USER_IDS` | No | Empty | Comma-separated user IDs allowed for `X-3E-UserId`. Empty denies delegation. See [Delegated user IDs](#delegated-user-ids). |

There are no guessed defaults for the API URL, OAuth URL, scope, or audience. Use the values and URL construction supplied for the target tenant. `.env.example` contains placeholders only; never commit real credentials. The server does not load `.env` files.

The server starts and answers MCP initialization and tool listing without credentials. Tools report a configuration error until the required environment is provided. Two commands help with setup:

```bash
uv run elite3e-mcp-setup
uv run elite3e-mcp-verify
```

`elite3e-mcp-setup` prints environment-variable instructions. `elite3e-mcp-verify` validates the local configuration and exits with code 2 if it is missing or invalid; it does not contact Elite 3E or prove authentication. Neither command saves or prints secret values.

## Usage with Claude Desktop

Add the server to Claude Desktop's configuration file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "elite3e": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/to/elite3e-mcp", "elite3e-mcp"],
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

Replace `/absolute/path/to/elite3e-mcp` with the path of your clone and the example values with the settings issued for your tenant. Set `ELITE3E_AUDIENCE` instead of `ELITE3E_SCOPE` only when the tenant package specifies the legacy audience form. Keep real credentials in your client's protected environment or secret store. Restart Claude Desktop after saving. Any other stdio MCP client uses the same command and arguments.

## HTTP mode

Stdio is the default. Set `ELITE3E_MCP_TRANSPORT=streamable-http` to serve the stateless Streamable HTTP transport from MCP specification 2026-07-28 at `/mcp`. Each request stands alone: no initialization handshake and no `Mcp-Session-Id`. Clients on earlier protocol versions are served on the same endpoint.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `ELITE3E_MCP_ALLOWED_HOSTS` and `ELITE3E_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ELITE3E_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. |
| `ELITE3E_MCP_HOST` | `127.0.0.1` | Bind address. `127.0.0.1`, `localhost` and `::1` use the SDK's built-in Host and Origin checks; any other value requires `ELITE3E_MCP_ALLOWED_HOSTS`. |
| `PORT` | `8080` | Port; must be an integer. |
| `ELITE3E_MCP_ALLOWED_HOSTS` | unset | Comma-separated `Host` header values accepted on a non-loopback bind, such as `mcp.example.com:8080` or `mcp.example.com:*`. |
| `ELITE3E_MCP_ALLOWED_ORIGINS` | unset | Comma-separated `Origin` values accepted on a non-loopback bind, such as `https://client.example.com`. Requests without an `Origin` header are accepted; any other `Origin` is refused, including when this is unset. |

Elite 3E credentials come from the same configuration as stdio (see [Configuration](#configuration)), never from the request.

With the Elite 3E variables from Configuration exported in the process environment, run:

```bash
ELITE3E_MCP_TRANSPORT=streamable-http PORT=8080 uv run --locked elite3e-mcp
```

Point the MCP client at `http://127.0.0.1:8080/mcp`.

## Error handling

A failed tool call returns an MCP error result (`isError`) with a fixed message. Elite 3E's and the identity provider's response text is never returned to the client and is not written to the log.

| Situation | The message begins |
| --- | --- |
| Missing or invalid configuration | `elite3e is not configured or its configuration is invalid.` It names the required variables and the `elite3e-mcp-setup` command. |
| HTTP 401 | `Authentication failed.` |
| HTTP 403 | `Access denied.` |
| HTTP 404 | `Resource not found.` |
| HTTP 429 | `Rate limit reached.` |
| HTTP 5xx | `Service unavailable.` |
| Any other HTTP error or unexpected response | `Request rejected or response invalid.` |
| Network failure or timeout | `Connection failed; the operation outcome may be unknown.` |

HTTP errors from the OAuth token request map the same way. Input problems have their own messages: a write tool called without `confirm=true` returns `Creation not confirmed` or `Update not confirmed`, and an `X-3E-UserId` that is not in `ELITE3E_ALLOWED_USER_IDS` is refused before any request is sent.

Requests that fail with a network error, HTTP 429 or any HTTP 5xx are retried up to `ELITE3E_MAX_RETRIES` times (default 3), including OAuth token requests. The wait before a retry is the `Retry-After` value when it is a number, and otherwise `ELITE3E_BACKOFF_SECONDS` doubled on each attempt (0.5, 1 and 2 seconds by default); a wait after an HTTP 429 or 5xx never exceeds 60 seconds. Requests time out after `ELITE3E_TIMEOUT_SECONDS` (default 30). The server keeps REST calls within Elite 3E's documented Cloud limit of 30 per rolling 60 seconds.

At startup the server exits with a message and status 1 on an unsupported `ELITE3E_MCP_TRANSPORT`, a `PORT` that is not an integer, or a non-loopback `ELITE3E_MCP_HOST` without `ELITE3E_MCP_ALLOWED_HOSTS`. Missing credentials do not stop the server from starting; tool calls report the configuration error instead.

## Safe pending-time workflow

1. Call `get_pending_timecard_template` to retrieve the target tenant's template.
2. Prepare the `dataCollection` using tenant-understood fields and values.
3. Review the complete object, then call `create_pending_timecard` with `confirm=true`.
4. For later changes, submit the existing object to `update_pending_timecard` with `confirm=true`.

Retrieving a template does not create a record. Tenant attributes and child objects are dynamic, so use the tenant's schema and returned template instead of assuming field IDs or defaults.

## Delegated user IDs

Per-call `x_3e_user_id` / `X-3E-UserId` delegation is denied by default. An administrator can set `ELITE3E_ALLOWED_USER_IDS` to a comma-separated list of exact user IDs authorized for this integration. Every override must match an entry; tool arguments cannot grant additional identities. Restart after changing configuration.

## Testing

The test suite runs offline and needs no Elite 3E account: every Elite 3E API call is intercepted at the HTTP layer with `respx`. It covers each tool's requests and responses, input validation, error handling, credential loading, pagination and retries, the stdio server, and the Streamable HTTP transport including the 2026-07-28 wire format, Host and Origin checks and stateless requests.

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
```

CI runs the suite on every push and pull request to `main`.

## License

MIT. See [LICENSE](LICENSE).
