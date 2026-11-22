import json
from datetime import date, timedelta

import pytest

from google_flights_mcp.client import (
    FlightSearchError,
    Leg,
    SearchParams,
    _extract_payload,
    build_query,
    parse_payload,
)

FUTURE = (date.today() + timedelta(days=30)).isoformat()
LATER = (date.today() + timedelta(days=37)).isoformat()


def _segment(o, d, fn, dep, arr, mins):
    s = [None] * 31
    s[3], s[4], s[5], s[6] = o, f"{o} Airport", f"{d} Airport", d
    s[8], s[10], s[11] = dep, arr, mins
    s[14], s[17] = "30 in", "Boeing 787"
    s[20] = s[21] = [2026, 12, 15]
    s[22] = [fn[:2], fn[2:], None, "Test Air"]
    return s


def _itin(price, segs, airlines, layovers=None):
    f = [None] * 23
    f[0], f[1], f[2] = "TA", airlines, segs
    f[4], f[5] = [2026, 12, 15], segs[0][8]
    f[7], f[8] = [2026, 12, 15], segs[-1][10]
    f[9] = sum(s[11] for s in segs) + sum(lo[0] for lo in layovers or [])
    f[13] = layovers
    f[22] = [None] * 7 + [250000, 300000]
    return [f, [[None, price]]]


def _payload():
    p = [None] * 8
    nonstop = _itin(300, [_segment("JFK", "LHR", "TA100", [8], [20, 5], 425)], ["Test Air"])
    onestop = _itin(
        250,
        [
            _segment("JFK", "DUB", "TA200", [6, 30], [18], 390),
            _segment("DUB", "LHR", "TA201", [19], [20, 15], 75),
        ],
        ["Test Air"],
        layovers=[[60, "DUB", "DUB", [3], "Dublin Airport", "Dublin"]],
    )
    p[2] = [[nonstop]]
    p[3] = [[onestop, nonstop]]  # duplicate of nonstop should be dropped
    return p


def test_parse_payload_best_and_other():
    items = parse_payload(_payload(), "USD")
    assert len(items) == 2
    best, other = items
    assert best.is_best and not other.is_best
    assert best.price == 300 and best.stops == 0
    assert best.segments[0].flight_number == "TA 100"
    assert best.segments[0].departure == "2026-12-15T08:00"
    assert best.arrival == "2026-12-15T20:05"
    assert best.co2_emissions_kg == 250.0
    assert other.stops == 1
    assert other.layovers[0].airport.code == "DUB"
    assert other.layovers[0].duration_minutes == 60
    assert other.departure == "2026-12-15T06:30"


def test_parse_empty():
    assert parse_payload(None, "USD") == []
    assert parse_payload([None] * 8, "USD") == []


def test_extract_payload_from_html():
    data = json.dumps(_payload())
    html = (
        "<script>AF_initDataCallback({key: 'ds:0', hash: '1', data:[1], sideChannel: {}});</script>"
        f"<script>AF_initDataCallback({{key: 'ds:1', hash: '5', data:{data}, sideChannel: {{}}}});</script>"
    )
    assert _extract_payload(html) == _payload()


def test_extract_payload_missing():
    with pytest.raises(FlightSearchError):
        _extract_payload("<html></html>")


def test_build_query_round_trip_url():
    q = build_query(
        SearchParams(legs=[Leg("jfk", "lhr", FUTURE), Leg("lhr", "jfk", LATER)], trip="round-trip")
    )
    url = q.url()
    assert url.startswith("https://www.google.com/travel/flights/search?tfs=")
    assert "curr=USD" in url


@pytest.mark.parametrize(
    "params, msg",
    [
        (SearchParams(legs=[Leg("JF", "LHR", FUTURE)], trip="one-way"), "IATA"),
        (SearchParams(legs=[Leg("JFK", "JFK", FUTURE)], trip="one-way"), "same"),
        (SearchParams(legs=[Leg("JFK", "LHR", "2020-01-01")], trip="one-way"), "past"),
        (SearchParams(legs=[Leg("JFK", "LHR", "12/01/2026")], trip="one-way"), "YYYY-MM-DD"),
        (
            SearchParams(legs=[Leg("JFK", "LHR", LATER), Leg("LHR", "JFK", FUTURE)], trip="round-trip"),
            "chronological",
        ),
        (SearchParams(legs=[Leg("JFK", "LHR", FUTURE)], trip="one-way", adults=5, children=5), "9"),
        (SearchParams(legs=[Leg("JFK", "LHR", FUTURE)], trip="one-way", infants_on_lap=2), "lap"),
    ],
)
def test_validation(params, msg):
    with pytest.raises(FlightSearchError, match=msg):
        build_query(params)
