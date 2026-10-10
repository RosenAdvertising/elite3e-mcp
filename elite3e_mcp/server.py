"""MCP server and tool registration for Thomson Reuters Elite 3E."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Annotated, Any, Literal

from mcp import types
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.applications import Starlette

from elite3e_mcp import __version__
from elite3e_mcp.client import MAX_PAGE_SIZE, Elite3EClient
from elite3e_mcp.runtime import LazyClient
from elite3e_mcp.runtime import SafeMCPServer as MCPServer

mcp = MCPServer(
    "elite3e",
    title="Thomson Reuters Elite 3E",
    description="Read selected law-firm business data and manage pending time entries in Elite 3E.",
    version=__version__,
)

READ_ONLY = types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
CREATE_WRITE = types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
UPDATE_WRITE = types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

Limit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum number of records to return from this response (1-5000).",
    ),
]
OptionalIndex = Annotated[
    int | None, Field(ge=0, description="Optional internal 3E index to filter by.")
]
OptionalDate = Annotated[
    date | None, Field(description="Optional calendar date in YYYY-MM-DD form.")
]
Language = Literal["en-GB", "en-US", "fr-FR"]


class FilterPredicate(BaseModel):
    """One indexed predicate in an Elite 3E advanced filter."""

    attribute: str = Field(description="Tenant attribute name to compare.")
    operator: str = Field(description="Elite 3E filter operator supplied by the tenant schema.")
    value: str = Field(description="Value to compare with the selected attribute.")


_client: Elite3EClient | LazyClient | None = None


def _get_client() -> Elite3EClient | LazyClient:
    global _client
    if _client is None:
        _client = LazyClient()
    return _client


def _clean_params(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
        if value is not None
    }


def _advanced_params(
    *,
    child_objects: list[str] | None = None,
    attributes: list[str] | None = None,
    filter_xoql: str | None = None,
    predicates: list[FilterPredicate] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if child_objects:
        params["AdvancedFilter.ChildObjectsToInclude"] = child_objects
    if attributes:
        params["AdvancedFilter.AttributesToInclude"] = attributes
    if filter_xoql:
        params["AdvancedFilter.FilterXOQL"] = filter_xoql
    for index, predicate in enumerate(predicates or []):
        prefix = f"AdvancedFilter.Filter.Predicates[{index}]"
        params[f"{prefix}.Attribute"] = predicate.attribute
        params[f"{prefix}.Operator"] = predicate.operator
        params[f"{prefix}.Value"] = predicate.value
    return params


def _limit_payload(payload: dict[str, Any], keys: tuple[str, ...], limit: int) -> dict[str, Any]:
    result = dict(payload)
    remaining = limit
    available = 0
    returned = 0
    for key in keys:
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        available += len(raw_items)
        selected = raw_items[:remaining]
        result[key] = selected
        count = len(selected)
        returned += count
        remaining -= count
    result["_mcp"] = {
        "limit": limit,
        "returned": returned,
        "availableInResponse": available,
        "truncated": available > returned,
    }
    return result


def _limit_attachments(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    result = dict(payload)
    raw_groups = payload.get("attachments")
    if not isinstance(raw_groups, dict):
        return _limit_payload(payload, (), limit)
    groups: dict[str, Any] = {}
    remaining = limit
    available = 0
    returned = 0
    for parent_id, raw_items in raw_groups.items():
        if not isinstance(raw_items, list):
            groups[parent_id] = raw_items
            continue
        available += len(raw_items)
        selected = raw_items[:remaining]
        groups[parent_id] = selected
        count = len(selected)
        returned += count
        remaining -= count
    result["attachments"] = groups
    result["_mcp"] = {
        "limit": limit,
        "returned": returned,
        "availableInResponse": available,
        "truncated": available > returned,
    }
    return result


def _common_headers(
    session_id: str | None,
    user_id: str | None,
    accept_language: Language | None,
) -> dict[str, str]:
    from elite3e_mcp.config import allowed_user_ids_from_env

    if user_id is not None and user_id not in allowed_user_ids_from_env():
        raise ToolError("X-3E-UserId must be allowed by ELITE3E_ALLOWED_USER_IDS.")
    return {
        key: value
        for key, value in {
            "X-3E-SessionId": session_id,
            "X-3E-UserId": user_id,
            "Accept-Language": accept_language,
        }.items()
        if value is not None
    }


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_matters(
    matter_id: Annotated[str | None, Field(description="Optional matter record ID.")] = None,
    matter_index: OptionalIndex = None,
    number: Annotated[str | None, Field(description="Optional matter alias or number.")] = None,
    child_objects_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant child-object names to include in each matter."),
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant matter attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None,
        Field(description="Optional tenant-validated XOQL advanced filter."),
    ] = None,
    predicates: Annotated[
        list[FilterPredicate] | None,
        Field(description="Optional indexed advanced-filter predicates."),
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find matters by record ID, internal index, number, or an advanced filter."""
    params = _clean_params({"MatterId": matter_id, "MattIndex": matter_index, "Number": number})
    params.update(
        _advanced_params(
            child_objects=child_objects_to_include,
            attributes=attributes_to_include,
            filter_xoql=filter_xoql,
            predicates=predicates,
        )
    )
    return _limit_payload(
        await _get_client().get("api/v1/matter", params=params), ("matters",), limit
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_clients(
    client_id: Annotated[str | None, Field(description="Optional client record ID.")] = None,
    client_index: OptionalIndex = None,
    number: Annotated[str | None, Field(description="Optional client number.")] = None,
    child_objects_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant child-object names to include in each client."),
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant client attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    predicates: Annotated[
        list[FilterPredicate] | None,
        Field(description="Optional indexed advanced-filter predicates."),
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find billing or marketing client records by ID, number, or an advanced filter."""
    params = _clean_params({"ClientId": client_id, "ClientIndex": client_index, "Number": number})
    params.update(
        _advanced_params(
            child_objects=child_objects_to_include,
            attributes=attributes_to_include,
            filter_xoql=filter_xoql,
            predicates=predicates,
        )
    )
    return _limit_payload(
        await _get_client().get("api/v1/client", params=params), ("clients",), limit
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_entities(
    entity_id: Annotated[
        str | None, Field(description="Optional person or organization record ID.")
    ] = None,
    entity_index: OptionalIndex = None,
    display_name: Annotated[
        str | None, Field(description="Optional person or organization display name.")
    ] = None,
    child_objects_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant child-object names to include in each entity."),
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant entity attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    predicates: Annotated[
        list[FilterPredicate] | None,
        Field(description="Optional indexed advanced-filter predicates."),
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Search people and organizations used as contacts and master records."""
    params = _clean_params(
        {"EntityId": entity_id, "EntityIndex": entity_index, "DisplayName": display_name}
    )
    params.update(
        _advanced_params(
            child_objects=child_objects_to_include,
            attributes=attributes_to_include,
            filter_xoql=filter_xoql,
            predicates=predicates,
        )
    )
    return _limit_payload(
        await _get_client().get("api/v1/entity/all", params=params),
        ("persons", "organizations"),
        limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_timekeepers(
    timekeeper_id: Annotated[
        str | None, Field(description="Optional timekeeper record ID.")
    ] = None,
    timekeeper_index: OptionalIndex = None,
    number: Annotated[str | None, Field(description="Optional timekeeper number.")] = None,
    child_objects_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant child-object names to include in each timekeeper."),
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant timekeeper attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    predicates: Annotated[
        list[FilterPredicate] | None,
        Field(description="Optional indexed advanced-filter predicates."),
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find attorneys, paralegals, and other billable personnel."""
    params = _clean_params(
        {"TimekeeperId": timekeeper_id, "TimekeeperIndex": timekeeper_index, "Number": number}
    )
    params.update(
        _advanced_params(
            child_objects=child_objects_to_include,
            attributes=attributes_to_include,
            filter_xoql=filter_xoql,
            predicates=predicates,
        )
    )
    return _limit_payload(
        await _get_client().get("api/v1/timekeeper", params=params),
        ("timekeepers",),
        limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_new_business_intakes(
    request_id: Annotated[
        str | None, Field(description="Optional new-business request record ID.")
    ] = None,
    job_number: Annotated[
        str | None, Field(description="Optional new-business job number.")
    ] = None,
    child_objects_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant child-object names to include in each intake."),
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant intake attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    predicates: Annotated[
        list[FilterPredicate] | None,
        Field(description="Optional indexed advanced-filter predicates."),
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find legacy conflicts and new-business-intake searches."""
    params = _clean_params({"CftNewBizRequestId": request_id, "JobNumber": job_number})
    params.update(
        _advanced_params(
            child_objects=child_objects_to_include,
            attributes=attributes_to_include,
            filter_xoql=filter_xoql,
            predicates=predicates,
        )
    )
    return _limit_payload(await _get_client().get("api/v1/NBI", params=params), ("nbIs",), limit)


def _time_params(
    *,
    start_date: date | None,
    end_date: date | None,
    card_id: str | None,
    card_index: int | None,
    timekeeper_index: int | None,
    timekeeper_number: str | None,
    timekeeper_id: str | None,
    attributes: list[str] | None,
    filter_xoql: str | None,
    pending: bool,
) -> dict[str, Any]:
    params = _clean_params(
        {
            "StartDate": start_date,
            "EndDate": end_date,
            "TimeCardPendingID" if pending else "TimecardID": card_id,
            "TimePendIndex" if pending else "TimeIndex": card_index,
            "TimekeeperIndex": timekeeper_index,
            "TimekeeperNumber": timekeeper_number,
            "TimekeeperID": timekeeper_id,
        }
    )
    params.update(_advanced_params(attributes=attributes, filter_xoql=filter_xoql))
    return params


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_posted_timecards(
    start_date: OptionalDate = None,
    end_date: OptionalDate = None,
    timecard_id: Annotated[str | None, Field(description="Optional posted timecard ID.")] = None,
    time_index: OptionalIndex = None,
    timekeeper_index: OptionalIndex = None,
    timekeeper_number: Annotated[
        str | None, Field(description="Optional timekeeper number.")
    ] = None,
    timekeeper_id: Annotated[
        str | None, Field(description="Optional timekeeper record ID.")
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant timecard attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find posted time entries by date, card, or timekeeper."""
    params = _time_params(
        start_date=start_date,
        end_date=end_date,
        card_id=timecard_id,
        card_index=time_index,
        timekeeper_index=timekeeper_index,
        timekeeper_number=timekeeper_number,
        timekeeper_id=timekeeper_id,
        attributes=attributes_to_include,
        filter_xoql=filter_xoql,
        pending=False,
    )
    return _limit_payload(
        await _get_client().get("api/v1/time/posted", params=params),
        ("timecards",),
        limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_pending_timecards(
    start_date: OptionalDate = None,
    end_date: OptionalDate = None,
    pending_timecard_id: Annotated[
        str | None, Field(description="Optional pending timecard ID.")
    ] = None,
    pending_time_index: OptionalIndex = None,
    timekeeper_index: OptionalIndex = None,
    timekeeper_number: Annotated[
        str | None, Field(description="Optional timekeeper number.")
    ] = None,
    timekeeper_id: Annotated[
        str | None, Field(description="Optional timekeeper record ID.")
    ] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant pending-time attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find editable unposted time entries by date, card, or timekeeper."""
    params = _time_params(
        start_date=start_date,
        end_date=end_date,
        card_id=pending_timecard_id,
        card_index=pending_time_index,
        timekeeper_index=timekeeper_index,
        timekeeper_number=timekeeper_number,
        timekeeper_id=timekeeper_id,
        attributes=attributes_to_include,
        filter_xoql=filter_xoql,
        pending=True,
    )
    return _limit_payload(
        await _get_client().get("api/v1/time/pending", params=params),
        ("timecards",),
        limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_time_calendar_report(
    timekeeper: Annotated[str, Field(description="Required timekeeper identifier or number.")],
    start_date: Annotated[date, Field(description="First report date in YYYY-MM-DD form.")],
    end_date: Annotated[date, Field(description="Last report date in YYYY-MM-DD form.")],
    matter: Annotated[
        str | None, Field(description="Optional matter identifier or number.")
    ] = None,
    client: Annotated[
        str | None, Field(description="Optional client identifier or number.")
    ] = None,
    include_hours: Annotated[
        bool | None,
        Field(description="Whether to include detailed hours when the tenant supports them."),
    ] = None,
    limit: Limit = 500,
) -> dict[str, Any]:
    """Show submitted time, targets, and holidays for a timekeeper and date range."""
    params = _clean_params(
        {
            "Timekeeper": timekeeper,
            "StartDate": start_date,
            "EndDate": end_date,
            "Matter": matter,
            "Client": client,
            "IncludeHours": include_hours,
        }
    )
    return _limit_payload(
        await _get_client().get("api/v1/time/report", params=params),
        ("days", "hours", "holidays"),
        limit,
    )


def _cost_params(
    *,
    start_date: date | None,
    end_date: date | None,
    costcard_id: str | None,
    cost_index: int | None,
    cost_type: str | None,
    timekeeper_index: int | None,
    timekeeper_number: str | None,
    timekeeper_id: str | None,
    matter_index: int | None,
    matter_number: str | None,
    attributes: list[str] | None,
    filter_xoql: str | None,
    pending: bool,
) -> dict[str, Any]:
    params = _clean_params(
        {
            "StartDate": start_date,
            "EndDate": end_date,
            "CostcardID": costcard_id,
            "CostPeindIndex" if pending else "CostIndex": cost_index,
            "CostType": cost_type,
            "TimekeeperIndex": timekeeper_index,
            "TimekeeperNumber": timekeeper_number,
            "TimekeeperID": timekeeper_id,
            "MatterIndex": matter_index,
            "MatterNumber": matter_number,
        }
    )
    params.update(_advanced_params(attributes=attributes, filter_xoql=filter_xoql))
    return params


async def _get_cost_cards(
    *,
    path: str,
    pending: bool,
    start_date: date | None,
    end_date: date | None,
    costcard_id: str | None,
    cost_index: int | None,
    cost_type: str | None,
    timekeeper_index: int | None,
    timekeeper_number: str | None,
    timekeeper_id: str | None,
    matter_index: int | None,
    matter_number: str | None,
    attributes_to_include: list[str] | None,
    filter_xoql: str | None,
    limit: int,
) -> dict[str, Any]:
    params = _cost_params(
        start_date=start_date,
        end_date=end_date,
        costcard_id=costcard_id,
        cost_index=cost_index,
        cost_type=cost_type,
        timekeeper_index=timekeeper_index,
        timekeeper_number=timekeeper_number,
        timekeeper_id=timekeeper_id,
        matter_index=matter_index,
        matter_number=matter_number,
        attributes=attributes_to_include,
        filter_xoql=filter_xoql,
        pending=pending,
    )
    return _limit_payload(await _get_client().get(path, params=params), ("costCards",), limit)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_posted_cost_cards(
    start_date: OptionalDate = None,
    end_date: OptionalDate = None,
    costcard_id: Annotated[str | None, Field(description="Optional posted cost-card ID.")] = None,
    cost_index: OptionalIndex = None,
    cost_type: Annotated[str | None, Field(description="Optional cost type.")] = None,
    timekeeper_index: OptionalIndex = None,
    timekeeper_number: Annotated[
        str | None, Field(description="Optional timekeeper number.")
    ] = None,
    timekeeper_id: Annotated[
        str | None, Field(description="Optional timekeeper record ID.")
    ] = None,
    matter_index: OptionalIndex = None,
    matter_number: Annotated[str | None, Field(description="Optional matter number.")] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant cost-card attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find posted matter expenses by date, card, timekeeper, matter, or type."""
    return await _get_cost_cards(
        path="api/v1/cost/posted",
        pending=False,
        start_date=start_date,
        end_date=end_date,
        costcard_id=costcard_id,
        cost_index=cost_index,
        cost_type=cost_type,
        timekeeper_index=timekeeper_index,
        timekeeper_number=timekeeper_number,
        timekeeper_id=timekeeper_id,
        matter_index=matter_index,
        matter_number=matter_number,
        attributes_to_include=attributes_to_include,
        filter_xoql=filter_xoql,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_pending_cost_cards(
    start_date: OptionalDate = None,
    end_date: OptionalDate = None,
    costcard_id: Annotated[str | None, Field(description="Optional pending cost-card ID.")] = None,
    cost_peind_index: Annotated[
        int | None,
        Field(
            ge=0, description="Optional pending-cost index; maps to vendor spelling CostPeindIndex."
        ),
    ] = None,
    cost_type: Annotated[str | None, Field(description="Optional cost type.")] = None,
    timekeeper_index: OptionalIndex = None,
    timekeeper_number: Annotated[
        str | None, Field(description="Optional timekeeper number.")
    ] = None,
    timekeeper_id: Annotated[
        str | None, Field(description="Optional timekeeper record ID.")
    ] = None,
    matter_index: OptionalIndex = None,
    matter_number: Annotated[str | None, Field(description="Optional matter number.")] = None,
    attributes_to_include: Annotated[
        list[str] | None,
        Field(description="Tenant pending-cost attribute names to include."),
    ] = None,
    filter_xoql: Annotated[
        str | None, Field(description="Optional tenant-validated XOQL filter.")
    ] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Find editable unposted matter expenses by date, card, timekeeper, matter, or type."""
    return await _get_cost_cards(
        path="api/v1/cost/pending",
        pending=True,
        start_date=start_date,
        end_date=end_date,
        costcard_id=costcard_id,
        cost_index=cost_peind_index,
        cost_type=cost_type,
        timekeeper_index=timekeeper_index,
        timekeeper_number=timekeeper_number,
        timekeeper_id=timekeeper_id,
        matter_index=matter_index,
        matter_number=matter_number,
        attributes_to_include=attributes_to_include,
        filter_xoql=filter_xoql,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_attachments(
    parent_item_ids: Annotated[
        list[str],
        Field(
            min_length=1, description="One or more parent 3E record IDs whose attachments to list."
        ),
    ],
    archetype: Annotated[str | None, Field(description="Optional parent archetype filter.")] = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """List file attachments for one or more 3E records without downloading them."""
    params = _clean_params({"ParentItemIds": parent_item_ids, "Archetype": archetype})
    return _limit_attachments(await _get_client().get("api/v1/attachment", params=params), limit)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_pending_timecard_template(
    x_3e_session_id: Annotated[
        str | None,
        Field(description="Optional 3E session identifier supplied by the tenant workflow."),
    ] = None,
    x_3e_user_id: Annotated[
        str | None,
        Field(description="Optional delegated 3E user ID; requires the Integration scope."),
    ] = None,
    accept_language: Annotated[
        Language | None,
        Field(description="Optional response language: en-GB, en-US, or fr-FR."),
    ] = None,
) -> dict[str, Any]:
    """Get the tenant-specific starting object needed to create a pending time entry."""
    return await _get_client().request_json(
        "GET",
        "api/v1/time/pending/template",
        headers=_common_headers(x_3e_session_id, x_3e_user_id, accept_language),
    )


@mcp.tool(annotations=CREATE_WRITE, structured_output=True)
async def create_pending_timecard(
    data_collection: Annotated[
        dict[str, Any],
        Field(description="Template-derived dataCollection with the desired pending-time values."),
    ],
    confirm: Annotated[
        bool,
        Field(description="Must be true to confirm creation of the pending time entry."),
    ] = False,
    x_3e_session_id: Annotated[
        str | None,
        Field(description="Optional 3E session identifier supplied by the tenant workflow."),
    ] = None,
    x_3e_user_id: Annotated[
        str | None,
        Field(description="Optional delegated 3E user ID; requires the Integration scope."),
    ] = None,
    accept_language: Annotated[
        Language | None,
        Field(description="Optional response language: en-GB, en-US, or fr-FR."),
    ] = None,
) -> dict[str, Any]:
    """Create an editable pending time entry from a template-derived object."""
    if not confirm:
        raise ToolError(
            "Creation not confirmed; set confirm=true only after reviewing data_collection"
        )
    return await _get_client().request_json(
        "POST",
        "api/v1/time/pending",
        json_body={"dataCollection": data_collection},
        headers=_common_headers(x_3e_session_id, x_3e_user_id, accept_language),
    )


@mcp.tool(annotations=UPDATE_WRITE, structured_output=True)
async def update_pending_timecard(
    data_collection: Annotated[
        dict[str, Any],
        Field(description="Existing pending-time dataCollection with the desired changes."),
    ],
    confirm: Annotated[
        bool,
        Field(description="Must be true to confirm modification of the pending time entry."),
    ] = False,
    x_3e_session_id: Annotated[
        str | None,
        Field(description="Optional 3E session identifier supplied by the tenant workflow."),
    ] = None,
    x_3e_user_id: Annotated[
        str | None,
        Field(description="Optional delegated 3E user ID; requires the Integration scope."),
    ] = None,
    accept_language: Annotated[
        Language | None,
        Field(description="Optional response language: en-GB, en-US, or fr-FR."),
    ] = None,
) -> dict[str, Any]:
    """Update an existing editable pending time entry."""
    if not confirm:
        raise ToolError(
            "Update not confirmed; set confirm=true only after reviewing data_collection"
        )
    return await _get_client().request_json(
        "PATCH",
        "api/v1/time/pending",
        json_body={"dataCollection": data_collection},
        headers=_common_headers(x_3e_session_id, x_3e_user_id, accept_language),
    )


STREAMABLE_HTTP_TRANSPORT = "streamable-http"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _requested_transport() -> str:
    return os.environ.get("ELITE3E_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"


def _host() -> str:
    return os.environ.get("ELITE3E_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _port() -> int:
    raw = os.environ.get("PORT", "8080").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"PORT must be an integer, got {raw!r}") from exc


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


def _transport_security() -> TransportSecuritySettings | None:
    # The SDK installs loopback Host and Origin checks when this returns None.
    if _host() in _LOOPBACK_HOSTS:
        return None
    allowed_hosts = _csv_env("ELITE3E_MCP_ALLOWED_HOSTS")
    if not allowed_hosts:
        raise SystemExit(
            "ELITE3E_MCP_ALLOWED_HOSTS is required when ELITE3E_MCP_HOST "
            "is not a loopback host. Set a comma-separated list of allowed Host headers."
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=_csv_env("ELITE3E_MCP_ALLOWED_ORIGINS"),
    )


def create_serve_app() -> Starlette:
    """Build the stateless MCP HTTP app, leaving json_response at the SDK default."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=_host(),
        stateless_http=True,
        transport_security=_transport_security(),
    )


async def _serve_streamable_http() -> None:
    import uvicorn

    config = uvicorn.Config(
        create_serve_app(),
        host=_host(),
        port=_port(),
        log_level=mcp.settings.log_level.lower(),
        access_log=False,
    )
    await uvicorn.Server(config).serve()


def _run_stdio() -> None:
    """Run stdio with deferred client configuration."""
    global _client
    _client = LazyClient()
    try:
        mcp.run(transport="stdio")
    finally:
        asyncio.run(_client.aclose())
        _client = None


def _run_streamable_http() -> None:
    global _client
    _client = LazyClient()
    try:
        asyncio.run(_serve_streamable_http())
    finally:
        asyncio.run(_client.aclose())
        _client = None


def main() -> None:
    """Run stdio, or stateless Streamable HTTP when selected."""
    transport = _requested_transport()
    if transport == "stdio":
        _run_stdio()
        return
    if transport == STREAMABLE_HTTP_TRANSPORT:
        _run_streamable_http()
        return
    raise SystemExit(
        "Unsupported ELITE3E_MCP_TRANSPORT "
        f"{transport!r}; expected 'stdio' or '{STREAMABLE_HTTP_TRANSPORT}'."
    )


if __name__ == "__main__":
    main()
