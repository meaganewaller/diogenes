"""
Unit tests for diogenes.core and diogenes.storage.

These tests are fast and self-contained — no LLM calls, no network,
temp SQLite databases only.
"""

import tempfile
import time
from pathlib import Path

import pytest

import diogenes
from diogenes.storage import TraceStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """A fresh SQLite database for each test."""
    db = str(tmp_path / "test-traces.db")
    diogenes.init(storage=db, service_name="test-agent")
    return db


@pytest.fixture
def store(tmp_db):
    return TraceStore(db_path=tmp_db)


# ── @tool decorator ────────────────────────────────────────────────────────────

def test_tool_decorator_passthrough(tmp_db):
    """@diogenes.tool should return the function's value unchanged."""

    @diogenes.tool
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_tool_decorator_named(tmp_db):
    """@diogenes.tool(name=...) should use the custom name."""

    @diogenes.tool(name="custom_name")
    def my_func():
        return "ok"

    assert my_func.__diogenes_tool_name__ == "custom_name"
    assert my_func() == "ok"


def test_tool_decorator_propagates_exception(tmp_db):
    """Exceptions from the wrapped function should propagate normally."""

    @diogenes.tool
    def broken():
        raise ValueError("intentional")

    with pytest.raises(ValueError, match="intentional"):
        broken()


# ── diogenes.run context manager ──────────────────────────────────────────────

def test_run_captures_to_db(tmp_db, store):
    """A completed run should appear in the trace store."""

    @diogenes.tool
    def noop(x: str) -> str:
        return x

    with diogenes.run("unit_test_run"):
        noop("hello")

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].name == "unit_test_run"
    assert runs[0].status == "OK"


def test_run_records_tool_calls(tmp_db, store):
    """Tool calls inside a run should appear as steps."""

    @diogenes.tool
    def search(query: str) -> list:
        return ["result1", "result2"]

    @diogenes.tool
    def summarise(text: str) -> str:
        return "summary"

    with diogenes.run("tool_call_test"):
        search("diogenes")
        summarise("result1 result2")

    runs = store.list_runs()
    run = runs[0]

    assert len(run.tool_calls) == 2
    assert run.tools_used == ["search", "summarise"]


def test_run_records_llm_calls(tmp_db, store):
    """record_llm_call should appear as an llm_call step."""

    with diogenes.run("llm_call_test"):
        diogenes.record_llm_call(
            model="claude-sonnet-4-6",
            input_tokens=500,
            output_tokens=120,
            output_text="Here is a summary.",
            system="anthropic",
        )

    run = store.list_runs()[0]
    assert len(run.llm_calls) == 1
    assert run.total_input_tokens == 500
    assert run.total_output_tokens == 120
    assert run.total_tokens == 620


def test_run_failed_status(tmp_db, store):
    """A run that raises should be recorded with ERROR status."""

    @diogenes.tool
    def explode():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        with diogenes.run("failing_run"):
            explode()

    run = store.list_runs()[0]
    assert run.status == "ERROR"


# ── Multiple runs ──────────────────────────────────────────────────────────────

def test_multiple_runs_are_independent(tmp_db, store):
    """Two separate runs should not share steps."""

    @diogenes.tool
    def ping() -> str:
        return "pong"

    with diogenes.run("run_a"):
        ping()
        ping()

    with diogenes.run("run_b"):
        ping()

    runs = store.list_runs()
    assert len(runs) == 2

    run_a = next(r for r in runs if r.name == "run_a")
    run_b = next(r for r in runs if r.name == "run_b")

    assert len(run_a.tool_calls) == 2
    assert len(run_b.tool_calls) == 1


# ── TraceStore queries ─────────────────────────────────────────────────────────

def test_get_run_by_prefix(tmp_db, store):
    """get_run() should support prefix matching on run_id."""

    with diogenes.run("prefix_test"):
        pass

    runs = store.list_runs()
    full_id = runs[0].run_id

    # Should work with any prefix
    assert store.get_run(full_id[:8]) is not None
    assert store.get_run(full_id) is not None


def test_get_run_missing(tmp_db, store):
    """get_run() should return None for an unknown ID."""
    assert store.get_run("nonexistent") is None


def test_list_runs_empty(tmp_db, store):
    """list_runs() should return empty list when no runs exist."""
    assert store.list_runs() == []