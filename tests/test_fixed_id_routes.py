"""Elite3E IDs are query/body data; adversarial values cannot change routes."""

import json

import httpx
import pytest
from mcp import Client

from elite3e_mcp import server
from elite3e_mcp.client import Elite3EClient

QUERY_ROUTES = [
    ("get_matters", "matter_id", "MatterId", "/3eapi/api/v1/matter"),
    ("get_clients", "client_id", "ClientId", "/3eapi/api/v1/client"),
    ("get_entities", "entity_id", "EntityId", "/3eapi/api/v1/entity/all"),
    ("get_timekeepers", "timekeeper_id", "TimekeeperId", "/3eapi/api/v1/timekeeper"),
    ("get_new_business_intakes", "request_id", "CftNewBizRequestId", "/3eapi/api/v1/NBI"),
    ("get_posted_timecards", "timecard_id", "TimecardID", "/3eapi/api/v1/time/posted"),
    (
        "get_pending_timecards",
        "pending_timecard_id",
        "TimeCardPendingID",
        "/3eapi/api/v1/time/pending",
    ),
    ("get_posted_cost_cards", "costcard_id", "CostcardID", "/3eapi/api/v1/cost/posted"),
    ("get_pending_cost_cards", "costcard_id", "CostcardID", "/3eapi/api/v1/cost/pending"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", ".", "..", "a/../b", "%2e%2e", "normal-id_123"])
@pytest.mark.parametrize("tool,parameter,query_key,path", QUERY_ROUTES)
async def test_query_id_cannot_retarget_route(
    monkeypatch, settings, tool, parameter, query_key, path, value
):
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        real = Elite3EClient(settings, http_client=http)
        real._access_token = "synthetic"
        real._token_expires_at = float("inf")
        monkeypatch.setattr(server, "_client", real)
        async with Client(server.mcp) as session:
            result = await session.call_tool(tool, {parameter: value})
        assert not result.is_error
        assert len(sent) == 1
        assert sent[0].url.path == path
        assert sent[0].url.params[query_key] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", ".", "..", "a/../b", "%2e%2e", "normal-id_123"])
@pytest.mark.parametrize(
    "tool,method", [("create_pending_timecard", "POST"), ("update_pending_timecard", "PATCH")]
)
async def test_body_id_cannot_retarget_route(monkeypatch, settings, tool, method, value):
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        real = Elite3EClient(settings, http_client=http)
        real._access_token = "synthetic"
        real._token_expires_at = float("inf")
        monkeypatch.setattr(server, "_client", real)
        async with Client(server.mcp) as session:
            result = await session.call_tool(
                tool, {"data_collection": {"id": value}, "confirm": True}
            )
        assert not result.is_error
        assert len(sent) == 1
        assert sent[0].method == method
        assert sent[0].url.path == "/3eapi/api/v1/time/pending"
        assert json.loads(sent[0].content)["dataCollection"]["id"] == value
