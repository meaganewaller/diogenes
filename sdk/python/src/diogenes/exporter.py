"""
diogenes.exporter
~~~~~~~~~~~~~~~~~
A custom OTel SpanExporter that writes spans to a local SQLite database.
This is the heart of the local collector and it's just a file in your project directory.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,
    start_time      INTEGER,   -- nanoseconds since epoch
    end_time        INTEGER,   -- nanoseconds since epoch
    status_code     TEXT,
    status_message  TEXT,
    attributes      TEXT,      -- JSON blob
    events          TEXT       -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_spans_trace_id   ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_start_time ON spans(start_time);
CREATE INDEX IF NOT EXISTS idx_spans_name       ON spans(name);
"""


class SQLiteSpanExporter(SpanExporter):
    """
    Exports OTel spans to a local SQLite file.

    Spans are stored verbatim with their full attribute set, so any
    downstream tool (CLI, UI, assertion engine) can reconstruct runs
    without needing to re-query the LLM.

    Thread-safe via a lock around the connection.
    """

    def __init__(self, db_path: str = ".diogenes/traces.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # SpanExporter interface
    # ------------------------------------------------------------------ #

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            conn = self._connect()
            try:
                for span in spans:
                    attributes = dict(span.attributes or {})
                    events = [
                        {
                            "name": e.name,
                            "timestamp": e.timestamp,
                            "attributes": dict(e.attributes or {}),
                        }
                        for e in (span.events or [])
                    ]

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO spans
                            (span_id, trace_id, parent_span_id, name,
                             start_time, end_time, status_code, status_message,
                             attributes, events)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            format(span.context.span_id, "016x"),
                            format(span.context.trace_id, "032x"),
                            format(span.parent.span_id, "016x") if span.parent else None,
                            span.name,
                            span.start_time,
                            span.end_time,
                            span.status.status_code.name if span.status else "UNSET",
                            span.status.description if span.status else None,
                            json.dumps(attributes),
                            json.dumps(events),
                        ),
                    )

                conn.commit()
                return SpanExportResult.SUCCESS

            except Exception as exc:  # noqa: BLE001
                print(f"[diogenes] Export error: {exc}")
                return SpanExportResult.FAILURE

            finally:
                conn.close()

    def shutdown(self) -> None:
        pass  # nothing to tear down — SQLite closes per-connection