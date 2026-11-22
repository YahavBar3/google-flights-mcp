"""Google Flights querying and parsing.

Queries are encoded with `fast-flights` (protobuf `tfs` URL parameter) and the
page is fetched with a browser-impersonating HTTP client. Results are parsed
from the `ds:1` data blob embedded in the HTML, covering both the "best" and
"other" result lists.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date as Date
from typing import Any, Literal

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.querying import Query
from primp import Client

from .models import Airport, Itinerary, Layover, SearchResult, Segment

URL = "https://www.google.com/travel/flights"

Cabin = Literal["economy", "premium-economy", "business", "first"]
SortBy = Literal["best", "price", "duration", "departure", "arrival"]

_IATA = re.compile(r"^[A-Z]{3}$")


class FlightSearchError(Exception):
    pass


@dataclass
class Leg:
    origin: str
    destination: str
    date: str
    airlines: list[str] | None = None
    max_stops: int | None = None
    earliest_departure_hour: int | None = None
    latest_departure_hour: int | None = None


@dataclass
class SearchParams:
    legs: list[Leg]
    trip: Literal["one-way", "round-trip"]
    cabin: Cabin = "economy"
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0
    max_price: int | None = None
    max_duration_minutes: int | None = None
    exclude_basic_economy: bool = False
    hide_separate_tickets: bool = False
    carry_on_bags: int = 0
    checked_bags: int = 0
    currency: str = "USD"
    language: str = "en-US"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation & query building
# ---------------------------------------------------------------------------


def _norm_airport(code: str, label: str) -> str:
    c = code.strip().upper()
    if not _IATA.match(c):
        raise FlightSearchError(
            f"{label} must be a 3-letter IATA airport or city code (e.g. 'JFK', 'NYC'), got {code!r}."
        )
    return c


def _norm_date(value: str, label: str) -> str:
    try:
        d = Date.fromisoformat(value.strip())
    except ValueError as e:
        raise FlightSearchError(f"{label} must be YYYY-MM-DD, got {value!r}.") from e
    if d < Date.today():
        raise FlightSearchError(f"{label} {value} is in the past.")
    return d.isoformat()


def _validate(p: SearchParams) -> None:
    if not p.legs:
        raise FlightSearchError("At least one leg is required.")
    for i, leg in enumerate(p.legs, 1):
        leg.origin = _norm_airport(leg.origin, f"Leg {i} origin")
        leg.destination = _norm_airport(leg.destination, f"Leg {i} destination")
        if leg.origin == leg.destination:
            raise FlightSearchError(f"Leg {i}: origin and destination are the same.")
        leg.date = _norm_date(leg.date, f"Leg {i} date")
        if leg.airlines:
            leg.airlines = [a.strip().upper() for a in leg.airlines if a.strip()]
    dates = [leg.date for leg in p.legs]
    if dates != sorted(dates):
        raise FlightSearchError("Leg dates must be in chronological order.")
    total = p.adults + p.children + p.infants_in_seat + p.infants_on_lap
    if p.adults < 1:
        raise FlightSearchError("At least one adult is required.")
    if total > 9:
        raise FlightSearchError("Google Flights supports at most 9 passengers.")
    if p.infants_on_lap > p.adults:
        raise FlightSearchError("Each lap infant needs an accompanying adult.")
    p.currency = p.currency.strip().upper()


def build_query(p: SearchParams) -> Query:
    _validate(p)
    flights = [
        FlightQuery(
            date=leg.date,
            from_airport=leg.origin,
            to_airport=leg.destination,
            max_stops=leg.max_stops,
            airlines=leg.airlines or None,
            earliest_departure_hour=leg.earliest_departure_hour,
            latest_departure_hour=leg.latest_departure_hour,
            max_duration_minutes=p.max_duration_minutes,
        )
        for leg in p.legs
    ]
    return create_query(
        flights=flights,
        seat=p.cabin,
        trip=p.trip,
        passengers=Passengers(
            adults=p.adults,
            children=p.children,
            infants_in_seat=p.infants_in_seat,
            infants_on_lap=p.infants_on_lap,
        ),
        language=p.language,
        currency=p.currency,
        max_price=p.max_price,
        carry_on_bags=p.carry_on_bags,
        checked_bags=p.checked_bags,
        hide_separate_and_self_transfer=p.hide_separate_tickets,
        exclude_basic_economy=p.exclude_basic_economy,
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _fetch_html(q: Query, retries: int = 3) -> str:
    client = Client(
        impersonate="chrome_145",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
        timeout=30,
    )
    for attempt in range(retries + 1):
        res = client.get(URL, params=q.params())
        if res.status_code == 200:
            return res.text
        if res.status_code in (429, 503) and attempt < retries:
            time.sleep(2 * 2**attempt)
            continue
        if res.status_code == 429:
            raise FlightSearchError(
                "Google Flights is rate-limiting requests (HTTP 429). Wait a few minutes and retry."
            )
        raise FlightSearchError(f"Google Flights returned HTTP {res.status_code}.")
    raise FlightSearchError("Google Flights request failed.")


_DECODER = json.JSONDecoder()


def _extract_payload(html: str) -> list | None:
    """Extract the `ds:1` AF_initDataCallback payload (flight results)."""
    key = html.find("key: 'ds:1'")
    if key == -1:
        lowered = html.lower()
        if "consent.google" in lowered or "before you continue" in lowered:
            raise FlightSearchError("Google served a cookie-consent page instead of results.")
        raise FlightSearchError("Could not find flight data in the Google Flights response.")
    data = html.find("data:", key)
    if data == -1:
        raise FlightSearchError("Malformed Google Flights data block.")
    i = data + len("data:")
    while html[i].isspace():
        i += 1
    try:
        payload, _ = _DECODER.raw_decode(html, i)
    except json.JSONDecodeError as e:
        if "errorHasStatus" in html[i : i + 200]:
            return None
        raise FlightSearchError(f"Failed to decode Google Flights data: {e}") from e
    return payload if isinstance(payload, list) else None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _g(obj: Any, *idx: int) -> Any:
    """Safe nested index: returns None on any missing level."""
    for i in idx:
        if not isinstance(obj, list) or i >= len(obj) or i < -len(obj):
            return None
        obj = obj[i]
    return obj


def _dt(d: Any, t: Any) -> str:
    if not isinstance(d, list) or len(d) < 3:
        return ""
    t = [*(t or []), None, None]
    return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}T{(t[0] or 0):02d}:{(t[1] or 0):02d}"


def _kg(grams: Any) -> float | None:
    return round(grams / 1000, 1) if isinstance(grams, (int, float)) else None


def _parse_itinerary(entry: list, currency: str, is_best: bool) -> Itinerary | None:
    f = _g(entry, 0)
    if not isinstance(f, list):
        return None
    price = _g(entry, 1, 0, 1)

    segments: list[Segment] = []
    for s in _g(f, 2) or []:
        fn = _g(s, 22)
        segments.append(
            Segment(
                flight_number=f"{fn[0]} {fn[1]}" if isinstance(fn, list) and len(fn) > 1 and fn[1] else None,
                airline=_g(s, 22, 3),
                origin=Airport(code=_g(s, 3) or "", name=_g(s, 4)),
                destination=Airport(code=_g(s, 6) or "", name=_g(s, 5)),
                departure=_dt(_g(s, 20), _g(s, 8)),
                arrival=_dt(_g(s, 21), _g(s, 10)),
                duration_minutes=_g(s, 11),
                aircraft=_g(s, 17),
                legroom=_g(s, 30) or _g(s, 14),
            )
        )
    if not segments:
        return None

    layovers = [
        Layover(
            airport=Airport(code=_g(lo, 1) or "", name=_g(lo, 4)),
            city=_g(lo, 5),
            duration_minutes=_g(lo, 0),
        )
        for lo in (_g(f, 13) or [])
        if isinstance(lo, list)
    ]

    return Itinerary(
        price=price if isinstance(price, int) else None,
        currency=currency,
        airlines=_g(f, 1) or [],
        stops=len(segments) - 1,
        total_duration_minutes=_g(f, 9),
        departure=_dt(_g(f, 4), _g(f, 5)) or segments[0].departure,
        arrival=_dt(_g(f, 7), _g(f, 8)) or segments[-1].arrival,
        segments=segments,
        layovers=layovers,
        co2_emissions_kg=_kg(_g(f, 22, 7)),
        typical_co2_emissions_kg=_kg(_g(f, 22, 8)),
        is_best=is_best,
    )


def parse_payload(payload: list | None, currency: str) -> list[Itinerary]:
    if payload is None:
        return []
    out: list[Itinerary] = []
    seen: set[tuple] = set()
    for block, is_best in ((_g(payload, 2, 0), True), (_g(payload, 3, 0), False)):
        for entry in block or []:
            it = _parse_itinerary(entry, currency, is_best)
            if it is None:
                continue
            key = (tuple(s.flight_number or s.departure for s in it.segments), it.price)
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


def _sort(items: list[Itinerary], by: SortBy) -> list[Itinerary]:
    big = 10**9
    if by == "price":
        return sorted(items, key=lambda i: (i.price if i.price is not None else big))
    if by == "duration":
        return sorted(items, key=lambda i: i.total_duration_minutes or big)
    if by == "departure":
        return sorted(items, key=lambda i: i.departure)
    if by == "arrival":
        return sorted(items, key=lambda i: i.arrival)
    return items  # "best": Google's own ordering


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def search(
    p: SearchParams, *, sort_by: SortBy = "best", limit: int = 20, max_stops: int | None = None
) -> SearchResult:
    q = build_query(p)
    html = await asyncio.to_thread(_fetch_html, q)
    items = parse_payload(_extract_payload(html), p.currency)
    if max_stops is not None:
        # Google treats max_stops as a soft filter in some markets; enforce it.
        items = [i for i in items if i.stops <= max_stops]
    items = _sort(items, sort_by)
    total = len(items)
    note = None
    if p.trip == "round-trip":
        note = (
            "Itineraries list outbound flights; prices are round-trip totals. "
            "Return flight options are chosen on Google Flights via the URL."
        )
    if total == 0:
        note = (note + " " if note else "") + "No flights found for these criteria."
    return SearchResult(
        trip_type=p.trip,
        currency=p.currency,
        google_flights_url=q.url(),
        result_count=total,
        itineraries=items[: max(1, limit)],
        note=note,
    )
