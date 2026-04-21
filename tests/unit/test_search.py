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


class TestCmdBrave:
    """Tests for the cmd_brave CLI handler — argparse-to-func wiring."""

    def _make_args(self, query, count=10, freshness="pd", json_out=False):
        args = MagicMock()
        # argparse with nargs="+" produces a list
        args.query = query if isinstance(query, list) else [query]
        args.count = count
        args.freshness = freshness
        args.json = json_out
        return args

    def test_empty_query_returns_2(self, capsys):
        from agentwire.search import cmd_brave
        rc = cmd_brave(self._make_args([]))
        assert rc == 2
        err = capsys.readouterr().err
        assert "query is required" in err

    def test_brave_error_returns_1_and_prints_stderr(self, monkeypatch, capsys):
        from agentwire.search import cmd_brave
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        rc = cmd_brave(self._make_args(["hello"]))
        assert rc == 1
        err = capsys.readouterr().err
        assert "BRAVE_SEARCH_API_KEY" in err

    def test_no_results_prints_placeholder_and_returns_0(self, capsys):
        from agentwire.search import cmd_brave
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b'{"web":{"results":[]}}'
            with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "fake"}):
                rc = cmd_brave(self._make_args(["anything"]))
        assert rc == 0
        assert "(no results)" in capsys.readouterr().out

    def test_text_output_default(self, capsys):
        from agentwire.search import cmd_brave
        api = {"web": {"results": [
            {"title": "T", "url": "http://u", "age": "1h", "description": "d"},
        ]}}
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = json.dumps(api).encode()
            with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "fake"}):
                rc = cmd_brave(self._make_args(["hello"]))
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "T | http://u | 1h | d"

    def test_json_output_when_flagged(self, capsys):
        from agentwire.search import cmd_brave
        api = {"web": {"results": [
            {"title": "T", "url": "http://u", "age": "1h", "description": "d"},
        ]}}
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = json.dumps(api).encode()
            with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "fake"}):
                rc = cmd_brave(self._make_args(["hello"], json_out=True))
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed[0]["title"] == "T"

    def test_multiword_query_joined(self):
        """argparse's nargs='+' passes query words as a list; we join with spaces."""
        from agentwire.search import cmd_brave
        with patch("agentwire.search.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b'{"web":{"results":[]}}'
            with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "fake"}):
                cmd_brave(self._make_args(["open", "source", "LLM"]))
            # Query string sent to Brave should be "open source LLM" URL-encoded
            url_called = m.call_args[0][0].full_url
            assert "q=open+source+LLM" in url_called or "q=open%20source%20LLM" in url_called
