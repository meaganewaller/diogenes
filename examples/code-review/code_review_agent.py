"""
demo/code_review_agent.py
~~~~~~~~~~~~~~~~~~~~~~~~~
A code review agent that:
  1. Lists Python files to review
  2. Reads each file
  3. Analyses it for issues
  4. Posts structured review comments

This demo simulates LLM responses so it runs without an API key,
while still exercising the full Diogenes instrumentation stack.

To use a real Anthropic API key, set ANTHROPIC_API_KEY and pass --live.

Usage:
    cd /home/claude/diogenes
    python -m demo.code_review_agent
    python -m demo.code_review_agent --live   # real LLM
"""

import os
import sys
import time
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Make the diogenes package importable when running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import diogenes


# ------------------------------------------------------------------ #
# Tool definitions
# ------------------------------------------------------------------ #

@diogenes.tool
def list_files(directory: str, extension: str = ".py") -> list[str]:
    """List source files in a directory."""
    base = Path(directory)
    if not base.exists():
        return []
    return [str(p) for p in sorted(base.rglob(f"*{extension}"))
            if "__pycache__" not in str(p)]


@diogenes.tool
def read_file(path: str) -> str:
    """Read the contents of a source file."""
    return Path(path).read_text(encoding="utf-8")


@diogenes.tool
def post_comment(file: str, line: int, severity: str, comment: str) -> dict:
    """Post a review comment on a file at a given line."""
    comment_id = f"cmt_{abs(hash((file, line, comment))) % 10000:04d}"
    return {
        "id": comment_id,
        "file": file,
        "line": line,
        "severity": severity,
        "comment": comment,
        "status": "posted",
    }


@diogenes.tool
def finish_review(summary: str, total_issues: int) -> dict:
    """Finalise the review and post a summary."""
    return {
        "status": "complete",
        "summary": summary,
        "total_issues": total_issues,
    }


# ------------------------------------------------------------------ #
# Simulated LLM (for running without an API key)
# ------------------------------------------------------------------ #

@dataclass
class SimulatedMessage:
    content: list
    usage: "Usage"

@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""

@dataclass
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


# The script for the simulated agent — a realistic multi-step code review
SIMULATION_STEPS = [
    # Step 1: model decides to list files
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_01", name="list_files",
                         input={"directory": "demo", "extension": ".py"}),
        ],
        usage=Usage(input_tokens=420, output_tokens=55),
    ),
    # Step 2: after seeing file list, model reads the file
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_02", name="read_file",
                         input={"path": "demo/sample_code.py"}),
        ],
        usage=Usage(input_tokens=680, output_tokens=60),
    ),
    # Step 3: after reading, model posts its first comment — hardcoded secret
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_03", name="post_comment",
                         input={
                             "file": "demo/sample_code.py",
                             "line": 13,
                             "severity": "critical",
                             "comment": (
                                 "Hardcoded API key detected. Rotate this credential "
                                 "immediately and move it to an environment variable "
                                 "or secrets manager. Never commit secrets to source control."
                             ),
                         }),
        ],
        usage=Usage(input_tokens=1840, output_tokens=95),
    ),
    # Step 4: SQL injection
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_04", name="post_comment",
                         input={
                             "file": "demo/sample_code.py",
                             "line": 19,
                             "severity": "critical",
                             "comment": (
                                 "SQL injection vulnerability. String interpolation into "
                                 "SQL queries allows arbitrary query execution. "
                                 "Use parameterised queries: conn.execute('SELECT * FROM "
                                 "users WHERE id = ?', (user_id,))"
                             ),
                         }),
        ],
        usage=Usage(input_tokens=1920, output_tokens=88),
    ),
    # Step 5: broad except
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_05", name="post_comment",
                         input={
                             "file": "demo/sample_code.py",
                             "line": 27,
                             "severity": "warning",
                             "comment": (
                                 "Bare `except:` swallows all exceptions including "
                                 "KeyboardInterrupt and SystemExit. Catch the specific "
                                 "exception type: `except requests.RequestException as e:`"
                             ),
                         }),
        ],
        usage=Usage(input_tokens=1980, output_tokens=82),
    ),
    # Step 6: missing type hints
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_06", name="post_comment",
                         input={
                             "file": "demo/sample_code.py",
                             "line": 16,
                             "severity": "info",
                             "comment": (
                                 "Missing type annotations. Add `user_id: int` and "
                                 "`-> dict | None` return type. Type hints improve "
                                 "IDE support, catch bugs early, and make the contract explicit."
                             ),
                         }),
        ],
        usage=Usage(input_tokens=2040, output_tokens=78),
    ),
    # Step 7: resource leak
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_07", name="post_comment",
                         input={
                             "file": "demo/sample_code.py",
                             "line": 34,
                             "severity": "warning",
                             "comment": (
                                 "Database connection is never closed. Use a context "
                                 "manager (`with open_db_connection() as conn:`) to "
                                 "ensure the connection is released even on exceptions."
                             ),
                         }),
        ],
        usage=Usage(input_tokens=2100, output_tokens=80),
    ),
    # Step 8: finish the review with a summary
    SimulatedMessage(
        content=[
            ToolUseBlock(id="tu_08", name="finish_review",
                         input={
                             "summary": (
                                 "Found 5 issues in demo/sample_code.py: "
                                 "2 critical (hardcoded secret, SQL injection), "
                                 "2 warnings (bare except, resource leak), "
                                 "1 info (missing type hints). "
                                 "Address the critical issues before merging."
                             ),
                             "total_issues": 5,
                         }),
        ],
        usage=Usage(input_tokens=2200, output_tokens=110),
    ),
]


# ------------------------------------------------------------------ #
# Tool dispatch
# ------------------------------------------------------------------ #

TOOL_MAP = {
    "list_files":   list_files,
    "read_file":    read_file,
    "post_comment": post_comment,
    "finish_review": finish_review,
}


def dispatch_tool(name: str, args: dict):
    fn = TOOL_MAP.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    return fn(**args)


# ------------------------------------------------------------------ #
# Agent loop
# ------------------------------------------------------------------ #

def run_simulated(target_dir: str) -> None:
    """Agent loop using pre-scripted responses (no API key needed)."""

    messages = [
        {
            "role": "user",
            "content": (
                f"Please review the Python code in the '{target_dir}' directory. "
                "List the files, read each one, and post a comment for each issue "
                "you find. Finish with a summary."
            ),
        }
    ]

    for step_idx, simulated_response in enumerate(SIMULATION_STEPS, 1):
        print(f"\n  ── Step {step_idx} ──")

        # Record the simulated LLM call as a proper OTel span
        tool_calls_data = [
            {"name": b.name, "input": b.input}
            for b in simulated_response.content
            if b.type == "tool_use"
        ]
        diogenes.record_llm_call(
            model="claude-sonnet-4-6-simulated",
            system="anthropic",
            input_tokens=simulated_response.usage.input_tokens,
            output_tokens=simulated_response.usage.output_tokens,
            tool_calls=tool_calls_data,
        )

        # Dispatch each tool call the model "requested"
        tool_results = []
        for block in simulated_response.content:
            if block.type == "tool_use":
                print(f"     🔧 {block.name}({json.dumps(block.input)[:80]})")
                result = dispatch_tool(block.name, block.input)
                print(f"     ✓  {str(result)[:100]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

                # Stop after finish_review
                if block.name == "finish_review":
                    return

        # Append assistant + tool result turns to history
        messages.append({"role": "assistant", "content": simulated_response.content})
        messages.append({"role": "user", "content": tool_results})

        time.sleep(0.05)  # tiny delay so timing numbers look realistic


def run_live(target_dir: str) -> None:
    """Agent loop using the real Anthropic API."""
    import anthropic  # noqa: PLC0415

    raw_client = anthropic.Anthropic()
    client = diogenes.wrap_anthropic(raw_client)

    tools = [
        {
            "name": "list_files",
            "description": "List Python source files in a directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "extension": {"type": "string", "default": ".py"},
                },
                "required": ["directory"],
            },
        },
        {
            "name": "read_file",
            "description": "Read the contents of a source file.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "post_comment",
            "description": "Post a review comment on a specific line.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file":     {"type": "string"},
                    "line":     {"type": "integer"},
                    "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                    "comment":  {"type": "string"},
                },
                "required": ["file", "line", "severity", "comment"],
            },
        },
        {
            "name": "finish_review",
            "description": "Finalise the review with a summary.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary":      {"type": "string"},
                    "total_issues": {"type": "integer"},
                },
                "required": ["summary", "total_issues"],
            },
        },
    ]

    messages = [
        {
            "role": "user",
            "content": (
                f"Review the Python code in '{target_dir}'. "
                "List the files, read each one, post a comment for every issue "
                "(hardcoded secrets, SQL injection, missing type hints, broad "
                "exception catches, resource leaks, unused code), "
                "then call finish_review with a summary."
            ),
        }
    ]

    step = 0
    while True:
        step += 1
        print(f"\n  ── Step {step} ──")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=tools,
            messages=messages,
        )

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        if text_blocks:
            print(f"     💬 {text_blocks[0].text[:120]}")

        tool_results = []
        for tc in tool_calls:
            print(f"     🔧 {tc.name}({json.dumps(tc.input)[:80]})")
            result = dispatch_tool(tc.name, tc.input)
            print(f"     ✓  {str(result)[:100]}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result),
            })
            if tc.name == "finish_review":
                return

        if response.stop_reason == "end_turn" or not tool_calls:
            break

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="Diogenes demo: code review agent")
    parser.add_argument("--live", action="store_true",
                        help="Use real Anthropic API (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--dir", default="demo",
                        help="Directory to review (default: demo)")
    parser.add_argument("--db", default=".diogenes/traces.db",
                        help="Trace database path")
    args = parser.parse_args()

    # Initialize Diogenes — single call, rest is automatic
    diogenes.init(storage=args.db, service_name="code-review-agent")

    mode = "live (Anthropic API)" if args.live else "simulated"
    print(f"\n🔍 Diogenes Code Review Agent")
    print(f"   Mode:   {mode}")
    print(f"   Target: {args.dir}/")
    print(f"   Traces: {args.db}")

    with diogenes.run("code_review", metadata={"target": args.dir, "mode": mode}):
        if args.live:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print("\n[error] ANTHROPIC_API_KEY not set. Run without --live for simulation.")
                sys.exit(1)
            run_live(args.dir)
        else:
            run_simulated(args.dir)

    print(f"\n✅ Review complete. View trace with:")
    print(f"   diogenes runs list")
    print(f"   diogenes runs show <run-id>")


if __name__ == "__main__":
    main()