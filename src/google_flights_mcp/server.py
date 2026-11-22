"""MCP server exposing Google Flights search tools."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date as Date
from datetime import timedelta
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import Cabin, FlightSearchError, Leg, SearchParams, SortBy, build_query, search
from .models import DatePrice, DateSearchResult, SearchResult

mcp = MCPServer(
    name="google-flights",
    instructions=(
        "Search Google Flights for prices and schedules. Airports are 3-letter IATA codes; "
        "metropolitan city codes such as NYC, LON, PAR or TYO also work. Dates are YYYY-MM-DD. "
        "Every result includes a google_flights_url the user can open to book."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)

Airport = Annotated[str, Field(description="3-letter IATA airport or city code, e.g. 'JFK', 'LON'.")]
IsoDate = Annotated[str, Field(description="Date as YYYY-MM-DD.")]
CabinArg = Annotated[Cabin, Field(description="Cabin class.")]
Adults = Annotated[int, Field(ge=1, le=9, description="Adults (12+).")]
Children = Annotated[int, Field(ge=0, le=8, description="Children (2-11).")]
InfantsSeat = Annotated[int, Field(ge=0, le=8, description="Infants with their own seat.")]
InfantsLap = Annotated[int, Field(ge=0, le=8, description="Infants on lap.")]
MaxStops = Annotated[
    int | None, Field(ge=0, le=2, description="0 = nonstop only, 1 = up to one stop, etc.")
]
Airlines = Annotated[
    list[str] | None,
    Field(description="Restrict to airlines by 2-letter IATA code (e.g. ['BA','AA']) or alliance ('ONEWORLD', 'STAR_ALLIANCE', 'SKYTEAM')."),
]
Currency = Annotated[str, Field(description="ISO 4217 currency code, e.g. 'USD', 'EUR', 'ILS'.")]
Sort = Annotated[SortBy, Field(description="'best' keeps Google's ranking.")]
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum itineraries to return.")]
Hour = Annotated[int | None, Field(ge=0, le=23)]


def _err(e: Exception) -> str:
    if isinstance(e, FlightSearchError):
        return str(e)
    return f"Flight search failed: {type(e).__name__}: {e}"


@mcp.tool(annotations=READ_ONLY)
async def search_flights(
    origin: Airport,
    destination: Airport,
    departure_date: IsoDate,
    return_date: Annotated[str | None, Field(description="YYYY-MM-DD for a round trip; omit for one-way.")] = None,
    adults: Adults = 1,
    children: Children = 0,
    infants_in_seat: InfantsSeat = 0,
    infants_on_lap: InfantsLap = 0,
    cabin: CabinArg = "economy",
    max_stops: MaxStops = None,
    airlines: Airlines = None,
    max_price: Annotated[int | None, Field(ge=1, description="Maximum total price in `currency`.")] = None,
    max_duration_hours: Annotated[float | None, Field(gt=0, description="Maximum journey duration per leg.")] = None,
    earliest_departure_hour: Hour = None,
    latest_departure_hour: Hour = None,
    exclude_basic_economy: bool = False,
    hide_separate_tickets: Annotated[bool, Field(description="Hide self-transfer / separate-ticket itineraries.")] = False,
    carry_on_bags: Annotated[int, Field(ge=0, le=1, description="Include carry-on fees in price.")] = 0,
    checked_bags: Annotated[int, Field(ge=0, le=2, description="Include checked-bag fees in price.")] = 0,
    currency: Currency = "USD",
    sort_by: Sort = "best",
    limit: Limit = 20,
) -> SearchResult:
    """Search one-way or round-trip flights on Google Flights.

    Returns itineraries with price, airlines, flight numbers, times, duration,
    layovers, aircraft, legroom and CO2 emissions. For round trips the listed
    flights are outbound options and prices are round-trip totals.
    """
    legs = [
        Leg(origin, destination, departure_date, airlines, max_stops,
            earliest_departure_hour, latest_departure_hour)
    ]
    if return_date:
        legs.append(Leg(destination, origin, return_date, airlines, max_stops))
    params = SearchParams(
        legs=legs,
        trip="round-trip" if return_date else "one-way",
        cabin=cabin,
        adults=adults,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
        max_price=max_price,
        max_duration_minutes=int(max_duration_hours * 60) if max_duration_hours else None,
        exclude_basic_economy=exclude_basic_economy,
        hide_separate_tickets=hide_separate_tickets,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        currency=currency,
    )
    try:
        return await search(params, sort_by=sort_by, limit=limit, max_stops=max_stops)
    except Exception as e:  # surfaced to the model as a tool error
        raise ToolError(_err(e)) from e


@mcp.tool(annotations=READ_ONLY)
async def find_cheapest_dates(
    origin: Airport,
    destination: Airport,
    start_date: Annotated[str, Field(description="First departure date to check, YYYY-MM-DD.")],
    end_date: Annotated[str, Field(description="Last departure date to check, YYYY-MM-DD (max 31 days after start).")],
    trip_length_days: Annotated[
        int | None, Field(ge=1, le=60, description="Nights away for a round trip; omit for one-way.")
    ] = None,
    adults: Adults = 1,
    cabin: CabinArg = "economy",
    max_stops: MaxStops = None,
    airlines: Airlines = None,
    currency: Currency = "USD",
) -> DateSearchResult:
    """Compare the cheapest fare for each departure date in a range (one search per date).

    Useful for flexible travel. Each date runs a full search, so wider ranges take longer.
    """
    try:
        start = Date.fromisoformat(start_date)
        end = Date.fromisoformat(end_date)
    except ValueError as e:
        raise ToolError("start_date and end_date must be YYYY-MM-DD.") from e
    if end < start:
        raise ToolError("end_date must not be before start_date.")
    if (end - start).days > 30:
        raise ToolError("Date range is limited to 31 days.")

    sem = asyncio.Semaphore(2)
    cur = currency.strip().upper()

    async def one(d: Date) -> DatePrice:
        dep = d.isoformat()
        ret = (d + timedelta(days=trip_length_days)).isoformat() if trip_length_days else None
        legs = [Leg(origin, destination, dep, airlines, max_stops)]
        if ret:
            legs.append(Leg(destination, origin, ret, airlines, max_stops))
        params = SearchParams(
            legs=legs, trip="round-trip" if ret else "one-way", cabin=cabin,
            adults=adults, currency=cur,
        )
        try:
            url = build_query(params).url()
        except FlightSearchError as e:
            return DatePrice(departure_date=dep, return_date=ret, google_flights_url="", error=str(e))
        try:
            async with sem:
                r = await search(params, sort_by="price", limit=1, max_stops=max_stops)
        except Exception as e:
            return DatePrice(departure_date=dep, return_date=ret, google_flights_url=url, error=_err(e))
        best = r.itineraries[0] if r.itineraries else None
        return DatePrice(
            departure_date=dep,
            return_date=ret,
            cheapest_price=best.price if best else None,
            cheapest_itinerary=best,
            google_flights_url=url,
        )

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    results = await asyncio.gather(*(one(d) for d in days))
    priced = [r for r in results if r.cheapest_price is not None]
    cheapest = min(priced, key=lambda r: r.cheapest_price) if priced else None
    return DateSearchResult(currency=cur, cheapest=cheapest, dates=list(results))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, idempotentHint=True))
def get_google_flights_url(
    origin: Airport,
    destination: Airport,
    departure_date: IsoDate,
    return_date: Annotated[str | None, Field(description="YYYY-MM-DD for a round trip.")] = None,
    adults: Adults = 1,
    children: Children = 0,
    cabin: CabinArg = "economy",
    max_stops: MaxStops = None,
    currency: Currency = "USD",
) -> str:
    """Build a Google Flights search link without fetching results."""
    legs = [Leg(origin, destination, departure_date, None, max_stops)]
    if return_date:
        legs.append(Leg(destination, origin, return_date, None, max_stops))
    params = SearchParams(
        legs=legs, trip="round-trip" if return_date else "one-way", cabin=cabin,
        adults=adults, children=children, currency=currency,
    )
    try:
        return build_query(params).url()
    except FlightSearchError as e:
        raise ToolError(str(e)) from e


def main() -> None:
    ap = argparse.ArgumentParser(description="Google Flights MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
