"""Tests for agentwire/search.py — Brave Search helper."""

import json
from unittest.mock import patch, MagicMock

import pytest

from agentwire.search import (
    BraveResult,
    BraveSearchError,
    brave_search,
    format_results_json,
    format_results_text,
)


class TestBraveSearch:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        with pytest.raises(BraveSearchError, match="BRAVE_SEARCH_API_KEY"):
            brave_search("anything")

    def test_invalid_freshness_raises(self):
        with pytest.raises(BraveSearchError, match="Invalid freshness"):
            brave_search("q", freshness="pz", api_key="fake")

    def test_count_clamped_to_brave_cap(self):
        """Count > 20 should silently clamp to 20, not error."""
        # We can't easily mock urlopen without getting network-y, so we just
        # verify the clamp logic at the brave_search level by intercepting the
        # URL builder — but since the helper builds URL internally, simpler is
        # to assert no ValueError from count out of range.
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b'{"web":{"results":[]}}'
            brave_search("q", count=50, api_key="fake")
            url_called = m.call_args[0][0].full_url
            assert "count=20" in url_called

    def test_parses_results(self):
        api_response = {
            "web": {
                "results": [
                    {
                        "title": "Story One",
                        "url": "https://example.com/1",
                        "age": "2 hours ago",
                        "description": "A summary of story one.",
                    },
                    {
                        "title": "Story Two",
                        "url": "https://example.com/2",
                        "description": "No age field here.",
                    },
                ],
            },
        }
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = json.dumps(api_response).encode()
            results = brave_search("q", api_key="fake")

        assert len(results) == 2
        assert results[0].title == "Story One"
        assert results[0].age == "2 hours ago"
        assert results[1].age == ""  # missing field → empty string, not None

    def test_falls_back_to_page_age(self):
        """If `age` is missing but `page_age` is present, use page_age."""
        api_response = {"web": {"results": [
            {"title": "T", "url": "u", "page_age": "2026-04-20", "description": "d"},
        ]}}
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = json.dumps(api_response).encode()
            results = brave_search("q", api_key="fake")
        assert results[0].age == "2026-04-20"

    def test_empty_results_returns_empty_list(self):
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b'{"web":{"results":[]}}'
            results = brave_search("q", api_key="fake")
        assert results == []

    def test_missing_web_field_returns_empty(self):
        """Brave sometimes returns just discussion/faq without web results."""
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b'{"type":"search"}'
            results = brave_search("q", api_key="fake")
        assert results == []

    def test_http_error_wraps_as_brave_error(self):
        import urllib.error
        with patch("agentwire.search.urllib.request.urlopen") as m:
            err = urllib.error.HTTPError(
                url="x", code=429, msg="Too Many Requests", hdrs=None, fp=MagicMock(read=lambda: b"rate limited"),
            )
            m.side_effect = err
            with pytest.raises(BraveSearchError, match="HTTP 429"):
                brave_search("q", api_key="fake")

    def test_malformed_json_wraps_as_brave_error(self):
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b"not json"
            with pytest.raises(BraveSearchError, match="invalid JSON"):
                brave_search("q", api_key="fake")


class TestFormatters:
    def test_format_text_compact(self):
        results = [
            BraveResult(title="A", url="http://a.com", age="1h", description="first"),
            BraveResult(title="B", url="http://b.com", age="", description="second"),
        ]
        out = format_results_text(results)
        assert out == "A | http://a.com | 1h | first\nB | http://b.com |  | second"

    def test_format_text_empty(self):
        assert format_results_text([]) == ""

    def test_format_json_shape(self):
        results = [BraveResult(title="A", url="u", age="1h", description="d")]
        out = format_results_json(results)
        parsed = json.loads(out)
        assert parsed == [{"title": "A", "url": "u", "age": "1h", "description": "d"}]
