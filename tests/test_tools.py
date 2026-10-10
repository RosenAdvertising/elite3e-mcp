"""One mocked MCP round trip for every Elite 3E tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from elite3e_mcp import server
from elite3e_mcp.client import Elite3EClient
from elite3e_mcp.config import Settings

TOOL_CASES: list[dict[str, Any]] = [
    {
        "name": "get_matters",
        "method": "GET",
        "path": "api/v1/matter",
        "args": {"matter_id": "matter-1", "limit": 1},
        "response": {"matters": [{"id": "matter-1"}], "success": True, "message": None},
        "query": ("MatterId", "matter-1"),
    },
    {
        "name": "get_clients",
        "method": "GET",
        "path": "api/v1/client",
        "args": {"client_index": 21},
        "response": {"clients": [{"id": "client-1"}], "success": True},
        "query": ("ClientIndex", "21"),
    },
    {
        "name": "get_entities",
        "method": "GET",
        "path": "api/v1/entity/all",
        "args": {"display_name": "Example Corp"},
        "response": {"persons": [], "organizations": [{"id": "entity-1"}], "success": True},
        "query": ("DisplayName", "Example Corp"),
    },
    {
        "name": "get_timekeepers",
        "method": "GET",
        "path": "api/v1/timekeeper",
        "args": {"number": "TK100"},
        "response": {"timekeepers": [{"id": "tk-1"}], "success": True},
        "query": ("Number", "TK100"),
    },
    {
        "name": "get_new_business_intakes",
        "method": "GET",
        "path": "api/v1/NBI",
        "args": {"job_number": "NBI-9"},
        "response": {"nbIs": [{"id": "nbi-1"}], "success": True},
        "query": ("JobNumber", "NBI-9"),
    },
    {
        "name": "get_posted_timecards",
        "method": "GET",
        "path": "api/v1/time/posted",
        "args": {"start_date": "2026-08-01", "timekeeper_number": "TK100"},
        "response": {"timecards": [{"id": "time-1"}], "success": True},
        "query": ("StartDate", "2026-08-01"),
    },
    {
        "name": "get_pending_timecards",
        "method": "GET",
        "path": "api/v1/time/pending",
        "args": {"pending_timecard_id": "pending-1"},
        "response": {"timecards": [{"id": "pending-1"}], "success": True},
        "query": ("TimeCardPendingID", "pending-1"),
    },
    {
        "name": "get_time_calendar_report",
        "method": "GET",
        "path": "api/v1/time/report",
        "args": {
            "timekeeper": "TK100",
            "start_date": "2026-08-01",
            "end_date": "2026-08-07",
        },
        "response": {"days": [], "hours": [], "holidays": [], "success": True},
        "query": ("Timekeeper", "TK100"),
    },
    {
        "name": "get_posted_cost_cards",
        "method": "GET",
        "path": "api/v1/cost/posted",
        "args": {"matter_number": "1000-001"},
        "response": {"costCards": [{"id": "cost-1"}], "success": True},
        "query": ("MatterNumber", "1000-001"),
    },
    {
        "name": "get_pending_cost_cards",
        "method": "GET",
        "path": "api/v1/cost/pending",
        "args": {"cost_peind_index": 44},
        "response": {"costCards": [{"id": "pending-cost-1"}], "success": True},
        "query": ("CostPeindIndex", "44"),
    },
    {
        "name": "get_attachments",
        "method": "GET",
        "path": "api/v1/attachment",
        "args": {"parent_item_ids": ["matter-1"]},
        "response": {"attachments": {"matter-1": [{"id": "attachment-1"}]}, "success": True},
        "query": ("ParentItemIds", "matter-1"),
    },
    {
        "name": "get_pending_timecard_template",
        "method": "GET",
        "path": "api/v1/time/pending/template",
        "args": {"accept_language": "en-US"},
        "response": {"dataCollection": {"rows": []}, "success": True},
        "header": ("Accept-Language", "en-US"),
    },
    {
        "name": "create_pending_timecard",
        "method": "POST",
        "path": "api/v1/time/pending",
        "args": {"data_collection": {"id": "new"}, "confirm": True},
        "response": {"dataCollection": {"id": "new"}, "success": True},
        "json": {"dataCollection": {"id": "new"}},
    },
    {
        "name": "update_pending_timecard",
        "method": "PATCH",
        "path": "api/v1/time/pending",
        "args": {"data_collection": {"id": "pending-1", "hours": 2}, "confirm": True},
        "response": {"dataCollection": {"id": "pending-1", "hours": 2}, "success": True},
        "json": {"dataCollection": {"id": "pending-1", "hours": 2}},
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TOOL_CASES, ids=[case["name"] for case in TOOL_CASES])
async def test_tool_makes_verified_http_round_trip(
    case: dict[str, Any],
    settings: Settings,
) -> None:
    with respx.mock:
        respx.post(settings.token_url).mock(
            return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        )
        route = respx.route(
            method=case["method"],
            url=f"{settings.base_url}/{case['path']}",
        ).mock(return_value=httpx.Response(200, json=case["response"]))
        client = Elite3EClient(settings)
        server._client = client
        try:
            result = await server.mcp.call_tool(case["name"], case["args"])
        finally:
            await client.aclose()
            server._client = None

    assert result.is_error is False
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer token-1"
    if "query" in case:
        key, value = case["query"]
        assert request.url.params[key] == value
    if "header" in case:
        key, value = case["header"]
        assert request.headers[key] == value
    if "json" in case:
        assert request.read()
        assert __import__("json").loads(request.content) == case["json"]


@pytest.mark.asyncio
async def test_write_tools_require_explicit_confirmation() -> None:
    for name in ("create_pending_timecard", "update_pending_timecard"):
        with pytest.raises(ToolError, match="not confirmed"):
            await server.mcp.call_tool(name, {"data_collection": {"id": "pending-1"}})
