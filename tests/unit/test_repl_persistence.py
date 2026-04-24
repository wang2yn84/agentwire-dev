"""Tests for REPL transcript persistence (Phase 2 PR 2).

Covers agentwire/repl/persistence.py — create_session, write_event,
record_session_id, finalize, list_sessions, load_session.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentwire.repl import persistence
from agentwire.repl.state import ReplState


@pytest.fixture
def repl_home(tmp_path):
    return tmp_path / "repl_home"


class TestCreateSession:
    def test_basic_layout(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="claude-opus-4-7",
            allowed_tools=["Read", "Bash"], home=repl_home,
        )
        try:
            assert t.session_dir.is_dir()
            assert t.events_path.is_file()
            assert t.metadata_path.is_file()
            meta = json.loads(t.metadata_path.read_text())
            assert meta["mode"] == "bypass"
            assert meta["model"] == "claude-opus-4-7"
            assert meta["allowed_tools"] == ["Read", "Bash"]
            assert meta["schema_version"] == 1
            assert meta["turn_count"] == 0
            assert meta["sdk_session_ids"] == []
        finally:
            t.close()

    def test_explicit_name_honored(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[],
            name="my-session", home=repl_home,
        )
        try:
            assert t.session_dir.name == "my-session"
        finally:
            t.close()

    def test_name_collision_suffixed(self, repl_home):
        t1 = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[],
            name="same", home=repl_home,
        )
        t2 = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[],
            name="same", home=repl_home,
        )
        try:
            assert t1.session_dir.name == "same"
            assert t2.session_dir.name == "same-2"
        finally:
            t1.close()
            t2.close()

    def test_auto_name_is_iso_plus_hex(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        try:
            name = t.session_dir.name
            # "YYYY-MM-DDTHH-MM-SS-xxxxxx" → split by - ≥ 6 parts
            assert len(name.split("-")) >= 6
            # Last part is 6 hex chars
            assert len(name.split("-")[-1]) == 6
        finally:
            t.close()


class TestWriteEvent:
    def test_appends_jsonl(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        try:
            t.write_event({"type": "user_input", "text": "hello"})
            t.write_event({"type": "user_input", "text": "second"})
        finally:
            t.close()

        lines = t.events_path.read_text().splitlines()
        assert len(lines) == 2
        e1 = json.loads(lines[0])
        e2 = json.loads(lines[1])
        assert e1["type"] == "user_input"
        assert e1["text"] == "hello"
        assert "ts" in e1
        assert e2["text"] == "second"

    def test_handles_non_serializable_via_default_str(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        try:
            t.write_event({"type": "x", "weird": object()})
        finally:
            t.close()
        # Just verify it didn't raise; line exists.
        assert len(t.events_path.read_text().splitlines()) == 1


class TestRecordSessionId:
    def test_adds_once(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        persistence.record_session_id(t, "sess-abc")
        persistence.record_session_id(t, "sess-abc")  # dupe ignored
        persistence.record_session_id(t, "sess-xyz")
        t.close()
        meta = json.loads(t.metadata_path.read_text())
        assert meta["sdk_session_ids"] == ["sess-abc", "sess-xyz"]

    def test_empty_id_noop(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        persistence.record_session_id(t, None)
        persistence.record_session_id(t, "")
        t.close()
        meta = json.loads(t.metadata_path.read_text())
        assert meta["sdk_session_ids"] == []


class TestFinalize:
    def test_writes_totals_and_ended_at(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=["Read"], home=repl_home,
        )
        state = ReplState(mode="bypass", model="m", allowed_tools=["Read"])
        state.total_input_tokens = 100
        state.total_output_tokens = 50
        state.total_cost_usd = 0.005
        state.turn_count = 3
        state.session_id = "sdk-abc"

        persistence.finalize(t, state)
        t.close()

        meta = json.loads(t.metadata_path.read_text())
        assert meta["total_input_tokens"] == 100
        assert meta["total_output_tokens"] == 50
        assert meta["total_cost_usd"] == pytest.approx(0.005)
        assert meta["turn_count"] == 3
        assert "ended_at" in meta
        assert "sdk-abc" in meta["sdk_session_ids"]

    def test_idempotent(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[], home=repl_home,
        )
        state = ReplState(mode="bypass", model="m", allowed_tools=[])
        state.turn_count = 1
        persistence.finalize(t, state)
        persistence.finalize(t, state)  # second call shouldn't explode
        t.close()
        meta = json.loads(t.metadata_path.read_text())
        assert meta["turn_count"] == 1


class TestListSessions:
    def test_empty_home(self, repl_home):
        assert persistence.list_sessions(home=repl_home) == []

    def test_orders_by_started_at_desc(self, repl_home, monkeypatch):
        # Create three sessions with staggered timestamps.
        import time
        stamps = [1000.0, 2000.0, 1500.0]
        for i, ts in enumerate(stamps):
            t = persistence.create_session(
                mode="bypass", model="m", allowed_tools=[],
                name=f"s{i}", home=repl_home,
            )
            t.close()
            # Rewrite started_at to our deterministic value.
            meta = json.loads(t.metadata_path.read_text())
            meta["started_at"] = ts
            t.metadata_path.write_text(json.dumps(meta))

        listed = persistence.list_sessions(home=repl_home)
        names = [m["name"] for m in listed]
        assert names == ["s1", "s2", "s0"]  # 2000 > 1500 > 1000

    def test_limit_applied(self, repl_home):
        for i in range(5):
            t = persistence.create_session(
                mode="bypass", model="m", allowed_tools=[],
                name=f"n{i}", home=repl_home,
            )
            t.close()
        listed = persistence.list_sessions(home=repl_home, limit=3)
        assert len(listed) == 3

    def test_skips_corrupt_metadata(self, repl_home):
        good = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[],
            name="good", home=repl_home,
        )
        good.close()
        # Create a dir with garbage metadata
        bad_dir = repl_home / "bad"
        bad_dir.mkdir()
        (bad_dir / "metadata.json").write_text("{not valid json")
        listed = persistence.list_sessions(home=repl_home)
        names = [m["name"] for m in listed]
        assert "good" in names
        assert "bad" not in names

    def test_skips_dirs_without_metadata(self, repl_home):
        good = persistence.create_session(
            mode="bypass", model="m", allowed_tools=[],
            name="good", home=repl_home,
        )
        good.close()
        (repl_home / "no-metadata").mkdir()
        listed = persistence.list_sessions(home=repl_home)
        names = [m["name"] for m in listed]
        assert names == ["good"]


class TestLoadSession:
    def test_missing_returns_none(self, repl_home):
        assert persistence.load_session("nope", home=repl_home) is None

    def test_corrupt_returns_none(self, repl_home):
        bad = repl_home / "bad"
        bad.mkdir(parents=True)
        (bad / "metadata.json").write_text("not json")
        assert persistence.load_session("bad", home=repl_home) is None

    def test_round_trip(self, repl_home):
        t = persistence.create_session(
            mode="bypass", model="m", allowed_tools=["Read"],
            name="rt", home=repl_home,
        )
        persistence.record_session_id(t, "sdk-x")
        t.close()
        meta = persistence.load_session("rt", home=repl_home)
        assert meta is not None
        assert meta["name"] == "rt"
        assert meta["allowed_tools"] == ["Read"]
        assert meta["sdk_session_ids"] == ["sdk-x"]
