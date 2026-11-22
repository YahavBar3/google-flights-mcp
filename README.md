# google-flights-mcp

An [MCP](https://modelcontextprotocol.io) server that lets Claude (or any MCP client) search **Google Flights**: live prices, schedules, flight numbers, layovers and CO₂ emissions, plus flexible-date fare comparison.

No API key needed. Queries are encoded the way the Google Flights web app encodes them (protobuf `tfs` parameter, via [`fast-flights`](https://github.com/AWeirdDev/flights)), and results are parsed from the data embedded in the page.

## Tools

| Tool | What it does |
|---|---|
| `search_flights` | One-way or round-trip search. Filters: cabin, passengers, max stops, airlines/alliances, max price, max duration, departure-hour window, basic economy, separate tickets, bag fees. Sort by best / price / duration / departure / arrival. |
| `find_cheapest_dates` | Cheapest fare for each departure date in a range (up to 31 days), one-way or with a fixed trip length. |
| `get_google_flights_url` | Build a Google Flights link without fetching. |

Each itinerary includes: total price, airlines, stops, total duration, departure/arrival times, and per-segment flight number, aircraft, legroom and duration; layover airports and durations; CO₂ emissions vs. route typical. Every result has a `google_flights_url` to open and book.

Airports are 3-letter IATA codes (`JFK`, `LHR`, `TLV`). Dates are `YYYY-MM-DD`.

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/YahavBar3/google-flights-mcp.git
```

```bash
cd google-flights-mcp && uv sync
```

### Claude Code

```bash
claude mcp add google-flights -- uv --directory /absolute/path/to/google-flights-mcp run google-flights-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-flights": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/google-flights-mcp", "run", "google-flights-mcp"]
    }
  }
}
```

### Without cloning

```bash
uvx --from git+https://github.com/YahavBar3/google-flights-mcp google-flights-mcp
```

### HTTP transport

```bash
uv run google-flights-mcp --transport streamable-http --port 8000
```

## Example prompts

- "Cheapest nonstop from JFK to LAX on Dec 10, back Dec 17."
- "Business class TLV to NRT in March, under 20 hours, Star Alliance only."
- "Which day in the first two weeks of February is cheapest to fly TLV to BER for a 5-night trip?"

## Limitations

- **Unofficial.** This reads the public Google Flights page; Google can change its format at any time and break parsing. Use for personal use and respect Google's terms.
- **Round trips** return outbound options with round-trip total prices. Return-flight selection happens on Google Flights via the returned URL.
- **Multi-city** isn't supported: Google doesn't include those results in the initial page.
- Only the results Google embeds in the initial page are returned (typically 5–40 per search).
- Heavy use triggers HTTP 429 rate limits. The client retries with backoff; `find_cheapest_dates` runs at most 2 requests at a time.
- Requests from some regions (e.g. EU) may hit a cookie-consent page; the tool reports this as an error.

## Development

```bash
uv run pytest
```

```bash
RUN_LIVE=1 uv run pytest -m live
```

## License

MIT
