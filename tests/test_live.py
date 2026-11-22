"""Live tests against Google Flights. Run with: RUN_LIVE=1 uv run pytest -m live"""

import os
from datetime import date, timedelta

import pytest

from google_flights_mcp.client import Leg, SearchParams, search

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1 to hit Google Flights"),
]


async def test_live_one_way():
    d = (date.today() + timedelta(days=45)).isoformat()
    r = await search(SearchParams(legs=[Leg("JFK", "LAX", d)], trip="one-way"), sort_by="price")
    assert r.result_count > 0
    assert r.itineraries[0].price is not None
    prices = [i.price for i in r.itineraries if i.price is not None]
    assert prices == sorted(prices)
