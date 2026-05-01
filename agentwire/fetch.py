"""URL fetch helper — `agentwire fetch <url>`.

Fetches a URL via Jina Reader (r.jina.ai), which handles JS-rendered pages
and returns clean markdown suitable for LLM consumption. Use this instead of
raw curl when a page returns empty or JavaScript boilerplate.

Usage from inside a workflow or pi session:

    agentwire fetch "https://weather.gc.ca/city/pages/on-134_metric_e.html"

    # Limit output to first N characters (default: 8000)
    agentwire fetch "https://example.com" --limit 4000
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request


JINA_BASE = "https://r.jina.ai/"
DEFAULT_LIMIT = 8000


class FetchError(Exception):
    """Raised for any fetch failure (network, HTTP error, etc.)."""


def fetch_url(url: str, limit: int = DEFAULT_LIMIT) -> str:
    """Fetch a URL via Jina Reader and return trimmed markdown content.

    Args:
        url: The page to fetch.
        limit: Max characters to return (0 = no limit).

    Raises:
        FetchError: Network or HTTP error.
    """
    jina_url = JINA_BASE + url
    req = urllib.request.Request(jina_url, headers={
        "Accept": "text/plain",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise FetchError(f"HTTP {e.code}: {msg[:300]}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"Network error: {e.reason}") from e

    if limit and len(content) > limit:
        content = content[:limit] + f"\n\n[truncated at {limit} chars]"

    return content


def cmd_fetch(args) -> int:
    """CLI handler for `agentwire fetch <url>`."""
    url = args.url.strip()
    if not url:
        print("Error: url is required. Usage: agentwire fetch <url>", file=sys.stderr)
        return 2

    try:
        content = fetch_url(url, limit=args.limit)
    except FetchError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(content)
    return 0
