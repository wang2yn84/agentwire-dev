"""Tests for the error-classification helper in anthropic.py.

Pure Python — no claude-agent-sdk import needed. Verifies that SDK-side
failures are tagged with a category prefix so users and canary reports can
distinguish rate-limited runs from genuine bugs.
"""

from __future__ import annotations

from agentwire.workflows.runners.anthropic import _classify


class TestClassify:
    def test_rate_limit_is_transient(self):
        assert _classify("RateLimitError", "rate_limit exceeded") == "transient"

    def test_overloaded_is_transient(self):
        assert _classify("APIStatusError", "overloaded_error: please retry") == "transient"

    def test_529_is_transient(self):
        assert _classify("APIStatusError", "status 529: service overloaded") == "transient"

    def test_503_is_transient(self):
        assert _classify("ProcessError", "backend returned 503") == "transient"

    def test_auth_is_permanent(self):
        assert _classify("AuthenticationError", "401 unauthorized") == "permanent"

    def test_invalid_request_is_invalid(self):
        assert _classify("BadRequestError", "400 invalid_request: bad model id") == "invalid"

    def test_generic_is_error(self):
        assert _classify("RuntimeError", "something unexpected happened") == "error"

    def test_empty_strings_fall_through(self):
        assert _classify("", "") == "error"
