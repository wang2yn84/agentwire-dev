"""Brave Search helper — `agentwire brave <query>`.

A thin CLI wrapper around the Brave Search API, optimized for LLM consumption
inside workflow prompts. Returns compact text or JSON that includes just the
fields a research agent needs (title, url, age, description) — avoiding the
full-JSON token dump that inflates context when workflows curl the API directly.

Token-efficiency was the whole point of the three-way A/B in 2026-04-21:
`curl | parse` beat raw `curl` by ~100× on input tokens. This helper codifies
that pattern so every research workflow gets it for free.

Usage from inside a workflow prompt:

    # Default: compact text output
    agentwire brave "GPT-5 release"

    # Raw JSON (full Brave API response) when you need image URLs, location data, etc.
    agentwire brave "GPT-5 release" --json

    # Tighten the count and widen the freshness window
    agentwire brave "open source LLM" --count 5 --freshness pw
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass


BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_COUNT = 10
DEFAULT_FRESHNESS = "pd"  # past day — best default for "fresh news" workflows
VALID_FRESHNESS = {"pd", "pw", "pm", "py"}  # past day/week/month/year


class BraveSearchError(Exception):
    """Raised for any Brave Search failure (missing key, API error, etc.)."""


@dataclass
class BraveResult:
    """Compact per-result record — the fields LLMs actually read."""
    title: str
    url: str
    age: str           # human string like "2 hours ago" — empty if Brave doesn't return it
    description: str   # Brave's snippet; usually enough without fetching the page


def brave_search(
    query: str,
    count: int = DEFAULT_COUNT,
    freshness: str = DEFAULT_FRESHNESS,
    api_key: str | None = None,
) -> list[BraveResult]:
    """Hit Brave Search API and return a compact list of results.

    Args:
        query: Free-text search query.
        count: Max results (Brave caps at 20).
        freshness: pd/pw/pm/py for past day/week/month/year.
        api_key: Override the BRAVE_SEARCH_API_KEY env var.

    Raises:
        BraveSearchError: Missing API key, HTTP error, or malformed response.
    """
    key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not key:
        raise BraveSearchError(
            "BRAVE_SEARCH_API_KEY not set. Add it to ~/.agentwire/.env or export "
            "it in your environment. Get a key at https://brave.com/search/api/."
        )
    if freshness not in VALID_FRESHNESS:
        raise BraveSearchError(
            f"Invalid freshness '{freshness}'. Use one of: {', '.join(sorted(VALID_FRESHNESS))}"
        )
    # Brave caps count at 20 — silently clamp rather than erroring so workflows
    # don't break on "count: 50" mistakes.
    count = max(1, min(int(count), 20))

    params = urllib.parse.urlencode({
        "q": query,
        "count": count,
        "freshness": freshness,
    })
    url = f"{BRAVE_SEARCH_ENDPOINT}?{params}"
    req = urllib.request.Request(url, headers={
        "X-Subscription-Token": key,
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise BraveSearchError(f"Brave API HTTP {e.code}: {msg[:300]}") from e
    except urllib.error.URLError as e:
        raise BraveSearchError(f"Brave API network error: {e.reason}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise BraveSearchError(f"Brave API returned invalid JSON: {e}") from e

    web_results = (data.get("web") or {}).get("results") or []
    return [
        BraveResult(
            title=r.get("title", "") or "",
            url=r.get("url", "") or "",
            age=r.get("age", "") or r.get("page_age", "") or "",
            description=r.get("description", "") or "",
        )
        for r in web_results
    ]


def format_results_text(results: list[BraveResult]) -> str:
    """Compact pipe-separated format: `title | url | age | description`.

    One result per line. No headers — the caller is usually an LLM that
    already knows the schema from its prompt.
    """
    lines = []
    for r in results:
        lines.append(f"{r.title} | {r.url} | {r.age} | {r.description}")
    return "\n".join(lines)


def format_results_json(results: list[BraveResult]) -> str:
    """Pretty JSON of the compact records."""
    return json.dumps(
        [{"title": r.title, "url": r.url, "age": r.age, "description": r.description}
         for r in results],
        indent=2,
    )


def cmd_brave(args) -> int:
    """CLI handler for `agentwire brave <query>`."""
    query = " ".join(args.query).strip() if isinstance(args.query, list) else str(args.query or "").strip()
    if not query:
        print("Error: query is required. Usage: agentwire brave <query...>", file=sys.stderr)
        return 2

    try:
        results = brave_search(query, count=args.count, freshness=args.freshness)
    except BraveSearchError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not results:
        print("(no results)")
        return 0

    if args.json:
        print(format_results_json(results))
    else:
        print(format_results_text(results))
    return 0
