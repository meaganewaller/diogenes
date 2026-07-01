"""
Integration test: runs the code review agent end-to-end in simulation mode
and asserts the resulting trace has the expected shape.

Full stack exercised:
  @diogenes.tool → OTel spans → SQLiteExporter → TraceStore → assertions
"""

import os
import sys
from pathlib import Path

import pytest

# Resolve the repo root so we can import the agent and run it from there
_REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "code-review"
sys.path.insert(0, str(_EXAMPLE_DIR))

import diogenes
from diogenes.storage import TraceStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "integration-traces.db")


@pytest.fixture
def repo_root_cwd():
    """Change to repo root for the duration of the test, then restore."""
    original = os.getcwd()
    os.chdir(_REPO_ROOT)
    yield _REPO_ROOT
    os.chdir(original)


def test_code_review_agent_trace_shape(db_path, repo_root_cwd):
    """
    Run the simulated code review agent and verify the trace structure:
    - Run captured with correct name and OK status
    - All expected tools called; no destructive tools called
    - LLM calls interleaved with tool calls
    - Token counts non-zero
    - At least one critical comment posted
    """
    from agent import run_simulated

    diogenes.init(storage=db_path, service_name="integration-test")

    with diogenes.run("code_review", metadata={"target": "examples/code-review"}):
        run_simulated("examples/code-review")

    store = TraceStore(db_path=db_path)
    runs = store.list_runs()

    assert len(runs) == 1
    run = runs[0]

    # Run-level
    assert run.name == "code_review"
    assert run.status == "OK"
    assert run.duration_ms > 0

    # Tool assertions
    tools_used = set(run.tools_used)
    assert "list_files"    in tools_used, "Agent must list files"
    assert "read_file"     in tools_used, "Agent must read at least one file"
    assert "post_comment"  in tools_used, "Agent must post review comments"
    assert "finish_review" in tools_used, "Agent must call finish_review"
    assert "delete_file"   not in tools_used, "Agent must NOT delete files"

    # LLM + tool interleaving
    assert len(run.llm_calls)  > 0
    assert len(run.tool_calls) > 0

    # Token accounting
    assert run.total_input_tokens  > 0
    assert run.total_output_tokens > 0

    # First step is always an LLM decision
    assert run.steps[0].kind == "llm_call"

    # At least one critical comment
    critical = [s for s in run.tool_calls
                if s.tool_name == "post_comment" and "critical" in (s.tool_input or "")]
    assert len(critical) >= 1