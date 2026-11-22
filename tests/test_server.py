from datetime import date, timedelta

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_flights_mcp.server import find_cheapest_dates, get_google_flights_url, mcp


async def test_tools_registered():
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"search_flights", "find_cheapest_dates", "get_google_flights_url"}


def test_url_tool():
    d = (date.today() + timedelta(days=20)).isoformat()
    url = get_google_flights_url("TLV", "LHR", d, currency="eur")
    assert "curr=EUR" in url


async def test_date_range_limit():
    with pytest.raises(ToolError, match="31 days"):
        await find_cheapest_dates("TLV", "LHR", "2026-12-01", "2027-02-01")
