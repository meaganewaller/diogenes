"""
diogenes.storage
~~~~~~~~~~~~~~~~
Query layer that reconstructs structured agent runs from the raw span store.

Spans are the ground truth. This module groups them into Runs and Steps
that the CLI and assertion engine can reason about.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------------ #
# Data models
# ------------------------------------------------------------------ #

@dataclass
class Step:
    """A single step in an agent run — either an LLM call or a tool call."""
    span_id: str
    trace_id: str
    kind: str                  # "llm_call" | "tool_call" | "unknown"
    name: str
    start_time: int            # nanoseconds
    end_time: int              # nanoseconds
    status: str
    attributes: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) / 1_000_000

    # -- LLM call convenience properties --
    @property
    def model(self) -> Optional[str]:
        return self.attributes.get("gen_ai.request.model")

    @property
    def input_tokens(self) -> int:
        return int(self.attributes.get("gen_ai.usage.input_tokens", 0))

    @property
    def output_tokens(self) -> int:
        return int(self.attributes.get("gen_ai.usage.output_tokens", 0))

    # -- Tool call convenience properties --
    @property
    def tool_name(self) -> Optional[str]:
        return self.attributes.get("diogenes.tool.name")

    @property
    def tool_input(self) -> Optional[str]:
        return self.attributes.get("diogenes.tool.input")

    @property
    def tool_output(self) -> Optional[str]:
        return self.attributes.get("diogenes.tool.output")


@dataclass
class Run:
    """A complete agent run reconstructed from its OTel trace."""
    run_id: str                # == trace_id of the root span
    name: str
    start_time: int            # nanoseconds
    end_time: Optional[int]
    status: str
    steps: list[Step] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        if not self.end_time:
            return 0.0
        return (self.end_time - self.start_time) / 1_000_000

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps if s.kind == "llm_call")

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps if s.kind == "llm_call")

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "tool_call"]

    @property
    def llm_calls(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "llm_call"]

    @property
    def tools_used(self) -> list[str]:
        return [s.tool_name for s in self.tool_calls if s.tool_name]


# ------------------------------------------------------------------ #
# Storage reader
# ------------------------------------------------------------------ #

def _classify_span(name: str) -> str:
    if name == "diogenes.llm_call":
        return "llm_call"
    if name.startswith("diogenes.tool."):
        return "tool_call"
    return "unknown"


def _row_to_step(row: sqlite3.Row) -> Step:
    return Step(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        kind=_classify_span(row["name"]),
        name=row["name"],
        start_time=row["start_time"] or 0,
        end_time=row["end_time"] or 0,
        status=row["status_code"] or "UNSET",
        attributes=json.loads(row["attributes"] or "{}"),
    )


class TraceStore:
    """
    Read-only view over the SQLite span store.
    The SQLiteSpanExporter writes; this class reads.
    """

    def __init__(self, db_path: str = ".diogenes/traces.db"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if not Path(self.db_path).exists():
            raise FileNotFoundError(
                f"No trace database found at '{self.db_path}'. "
                "Run an instrumented agent first."
            )
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_runs(self, limit: int = 20) -> list[Run]:
        """Return recent runs, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT span_id, trace_id, name, start_time, end_time,
                       status_code, attributes
                FROM   spans
                WHERE  parent_span_id IS NULL
                  AND  name = 'diogenes.run'
                ORDER  BY start_time DESC
                LIMIT  ?
                """,
                (limit,),
            ).fetchall()

            runs = []
            for row in rows:
                attrs = json.loads(row["attributes"] or "{}")
                run = Run(
                    run_id=row["trace_id"],
                    name=attrs.get("diogenes.run.name", row["name"]),
                    start_time=row["start_time"] or 0,
                    end_time=row["end_time"],
                    status=row["status_code"] or "UNSET",
                    steps=self._get_steps(conn, row["trace_id"], row["span_id"]),
                )
                runs.append(run)

            return runs
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[Run]:
        """Fetch a single run by its trace ID (prefix match supported)."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT span_id, trace_id, name, start_time, end_time,
                       status_code, attributes
                FROM   spans
                WHERE  parent_span_id IS NULL
                  AND  name = 'diogenes.run'
                  AND  trace_id LIKE ?
                LIMIT 1
                """,
                (f"{run_id}%",),
            ).fetchone()

            if not row:
                return None

            attrs = json.loads(row["attributes"] or "{}")
            return Run(
                run_id=row["trace_id"],
                name=attrs.get("diogenes.run.name", row["name"]),
                start_time=row["start_time"] or 0,
                end_time=row["end_time"],
                status=row["status_code"] or "UNSET",
                steps=self._get_steps(conn, row["trace_id"], row["span_id"]),
            )
        finally:
            conn.close()

    def _get_steps(
        self, conn: sqlite3.Connection, trace_id: str, root_span_id: str
    ) -> list[Step]:
        """Return child spans for a run, ordered by start time."""
        rows = conn.execute(
            """
            SELECT span_id, trace_id, name, start_time, end_time,
                   status_code, attributes
            FROM   spans
            WHERE  trace_id = ?
              AND  span_id  != ?
            ORDER  BY start_time ASC
            """,
            (trace_id, root_span_id),
        ).fetchall()
        return [_row_to_step(r) for r in rows]