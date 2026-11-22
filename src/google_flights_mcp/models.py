"""Typed result models returned by the MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Airport(BaseModel):
    code: str = Field(description="IATA airport code, e.g. 'LHR'.")
    name: str | None = None


class Segment(BaseModel):
    """A single flight (one takeoff, one landing)."""

    flight_number: str | None = Field(None, description="e.g. 'BA 164'.")
    airline: str | None = None
    origin: Airport
    destination: Airport
    departure: str = Field(description="Local departure time, ISO 8601 (YYYY-MM-DDTHH:MM).")
    arrival: str = Field(description="Local arrival time, ISO 8601 (YYYY-MM-DDTHH:MM).")
    duration_minutes: int | None = None
    aircraft: str | None = None
    legroom: str | None = None


class Layover(BaseModel):
    airport: Airport
    city: str | None = None
    duration_minutes: int | None = None


class Itinerary(BaseModel):
    """One bookable option for the searched leg."""

    price: int | None = Field(
        None,
        description=(
            "Total price for all passengers in the requested currency. "
            "For round trips this is the full round-trip price."
        ),
    )
    currency: str
    airlines: list[str]
    stops: int
    total_duration_minutes: int | None = None
    departure: str
    arrival: str
    segments: list[Segment]
    layovers: list[Layover] = []
    co2_emissions_kg: float | None = None
    typical_co2_emissions_kg: float | None = Field(
        None, description="Typical emissions for this route, for comparison."
    )
    is_best: bool = Field(
        False, description="Google ranks this among the 'best' options (price/duration/stops)."
    )


class SearchResult(BaseModel):
    trip_type: str
    currency: str
    google_flights_url: str = Field(description="Open this link to view or book on Google Flights.")
    result_count: int
    itineraries: list[Itinerary]
    note: str | None = None


class DatePrice(BaseModel):
    departure_date: str
    return_date: str | None = None
    cheapest_price: int | None = None
    cheapest_itinerary: Itinerary | None = None
    google_flights_url: str
    error: str | None = None


class DateSearchResult(BaseModel):
    currency: str
    cheapest: DatePrice | None = None
    dates: list[DatePrice]
