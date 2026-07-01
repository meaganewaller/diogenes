"""
diogenes.core
~~~~~~~~~~~~~
The public instrumentation API.

Usage:

    import diogenes

    diogenes.init()

    @diogenes.tool
    def read_file(path: str) -> str:
        ...

    with diogenes.run("code_review"):
        result = agent_loop()

No config file, no separate process, no env vars required.
"""

import contextlib
import functools
import inspect
from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace.status import Status, StatusCode

from .exporter import SQLiteSpanExporter

# Module-level singletons - one provider + tracer per process
_provider: Optional[TracerProvider] = None
_tracer: Optional[trace.Tracer] = None
_db_path: str = ".diogenes/traces.db"


# ------------------------------------------------------------ #
# Initialization
# ------------------------------------------------------------ #

def init(
    storage: str = ".diogenes/traces.db",
    service_name: str = "diogenes-agent",
    otlp_endpoint: Optional[str] = None,
) -> None:
    """
    Initialize Diogenes. Call once at the top of your entry point.

    Args:
        storage:       Path to the local SQLite trace database.
        service_name:  OTel service.name resource attribute.
        otlp_endpoint: Optional OTLP gRPC endpoint to forward traces
                       (e.g. "http://localhost:4317" for a remote collector
                       or your Diogenes Cloud endpoint).
    """
    global _provider, _tracer, _db_path

    _db_path = storage
    resource = Resource.create({"service.name": service_name})
    _provider = TracerProvider({resource=resource})

    # Local SQLite - always on
    sqlite_exporter = SQLiteSpanExporter(db_path=storage)
    _provider.add_span_processor(SimpleSpanProcessor(sqlite_exporter))

    # Optional OTLP forward (cloud sync, Honeycomb, Grafana Tempo, etc.)
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            otlp = OTLPSpanExporter(endpoint=otlp_endpoint)
            _provider.add_span_processor(BatchSpanProcessor(otlp))
        except ImportError:
            print(
                "[diogenes] Install opentelemetry-exporter-otlp-proto-grpc "
                "to enable OTLP forwarding."
            )

    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("diogenes", "0.1.0")

def _get_tracer() -> trace.Tracer:
    """Return the active tracer, auto-initializing with defaults if needed."""
    if _trace is None:
        init()
    return _tracer # type: ignore[return-value]


# ------------------------------------------------------------------ #
# Agent run context manager
# ------------------------------------------------------------------ #
 
@contextlib.contextmanager
def run(name: str, metadata: Optional[dict] = None):
    """
    Context manager that wraps a complete agent run.
 
    All tool calls and LLM calls made within this block are grouped
    under a single trace, queryable by run name.
 
    Example:
        with diogenes.run("code_review", metadata={"pr": "123"}):
            agent_loop(pr_number=123)
    """
    tracer = _get_tracer()
 
    attributes: dict[str, Any] = {"diogenes.run.name": name}
    if metadata:
        for k, v in metadata.items():
            attributes[f"diogenes.run.meta.{k}"] = str(v)
 
    with tracer.start_as_current_span("diogenes.run", attributes=attributes) as span:
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
 
 
# ------------------------------------------------------------------ #
# @tool decorator
# ------------------------------------------------------------------ #
 
def tool(func: Optional[Callable] = None, *, name: Optional[str] = None):
    """
    Decorator that instruments a tool function.
 
    Records the tool name, serialized inputs, and output as span
    attributes under the diogenes.tool.* namespace. Also records
    exceptions as span events so they're visible in the trace.
 
    Usage:
        @diogenes.tool
        def read_file(path: str) -> str: ...
 
        @diogenes.tool(name="web_search")
        def search(query: str) -> list: ...
    """
 
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
 
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tracer = _get_tracer()
 
            # Bind positional args to param names for readable traces
            try:
                sig = inspect.signature(fn)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                input_repr = {k: _truncate(v) for k, v in bound.arguments.items()}
            except Exception:
                input_repr = {"args": _truncate(args), "kwargs": _truncate(kwargs)}
 
            span_attrs: dict[str, Any] = {
                "diogenes.tool.name": tool_name,
                "diogenes.tool.input": json_safe(input_repr),
            }
 
            with tracer.start_as_current_span(
                f"diogenes.tool.{tool_name}", attributes=span_attrs
            ) as span:
                try:
                    result = fn(*args, **kwargs)
                    span.set_attribute("diogenes.tool.output", _truncate(result))
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise
 
        wrapper.__diogenes_tool__ = True  # marker for schema introspection later
        wrapper.__diogenes_tool_name__ = tool_name
        return wrapper
 
    # Support both @tool and @tool(name="...")
    if func is not None:
        return decorator(func)
    return decorator
 
 
# ------------------------------------------------------------------ #
# Anthropic client wrapper
# ------------------------------------------------------------------ #
 
def wrap_anthropic(client):
    """
    Wrap an anthropic.Anthropic (or AsyncAnthropic) client so that every
    messages.create() call is automatically traced.
 
    Usage:
        import anthropic
        import diogenes
 
        client = diogenes.wrap_anthropic(anthropic.Anthropic())
        # use client exactly as before — traces appear automatically
    """
    original_create = client.messages.create
 
    @functools.wraps(original_create)
    def traced_create(*args, **kwargs):
        tracer = _get_tracer()
        model = kwargs.get("model", "unknown")
 
        with tracer.start_as_current_span(
            "diogenes.llm_call",
            attributes={
                "gen_ai.system": "anthropic",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
            },
        ) as span:
            response = original_create(*args, **kwargs)
 
            # Record usage from the response
            if hasattr(response, "usage") and response.usage:
                span.set_attribute(
                    "gen_ai.usage.input_tokens", response.usage.input_tokens
                )
                span.set_attribute(
                    "gen_ai.usage.output_tokens", response.usage.output_tokens
                )
 
            # Record tool calls requested by the model
            tool_calls = [
                b for b in response.content if b.type == "tool_use"
            ]
            if tool_calls:
                span.set_attribute(
                    "diogenes.llm.tool_calls",
                    json_safe([
                        {"name": tc.name, "input": tc.input}
                        for tc in tool_calls
                    ]),
                )
                span.set_attribute(
                    "diogenes.llm.tool_calls_count", len(tool_calls)
                )
 
            # Record text output
            text_blocks = [b for b in response.content if b.type == "text"]
            if text_blocks:
                span.set_attribute(
                    "diogenes.llm.output_text",
                    _truncate(text_blocks[0].text, 2000),
                )
 
            span.set_status(Status(StatusCode.OK))
            return response
 
    # Patch the method on the messages sub-object
    client.messages.create = traced_create
    return client
 
 
# ------------------------------------------------------------------ #
# Manual LLM call recording (for non-Anthropic clients)
# ------------------------------------------------------------------ #
 
def record_llm_call(
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    output_text: str = "",
    tool_calls: Optional[list] = None,
    system: str = "unknown",
) -> None:
    """
    Manually record an LLM call span. Useful when wrapping a client
    that isn't Anthropic (OpenAI, Gemini, local models, etc.).
    """
    tracer = _get_tracer()
 
    with tracer.start_as_current_span(
        "diogenes.llm_call",
        attributes={
            "gen_ai.system": system,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "diogenes.llm.output_text": _truncate(output_text, 2000),
            "diogenes.llm.tool_calls_count": len(tool_calls or []),
        },
    ) as span:
        if tool_calls:
            span.set_attribute("diogenes.llm.tool_calls", json_safe(tool_calls))
        span.set_status(Status(StatusCode.OK))
 
 
# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
 
def _truncate(value: Any, max_len: int = 500) -> str:
    """Truncate a string to a maximum length."""
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "…"
 
 
def json_safe(value: Any) -> str:
    """Serialize a value to a compact JSON string, safely."""
    import json
 
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)