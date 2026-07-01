"""
conftest.py — shared pytest fixtures for the diogenes test suite.

The key problem: OTel's global TracerProvider can only be set once per
process via a Go-style sync.Once (_TRACER_PROVIDER_SET_ONCE). Each test
that calls diogenes.init() with a fresh temp DB needs to reset that flag,
otherwise the second test's provider is silently ignored.

We patch the three relevant globals before each test and shut down cleanly after.
"""

import pytest
import opentelemetry.trace as otel_trace
import diogenes.core as core_module


@pytest.fixture(autouse=True)
def reset_diogenes_state():
    """Reset diogenes + OTel global state before and after every test."""
    _reset()
    yield
    if core_module._provider is not None:
        try:
            core_module._provider.force_flush()
            core_module._provider.shutdown()
        except Exception:
            pass
    _reset()


def _reset():
    """Clear diogenes singletons and OTel's set-once global."""
    core_module._provider = None
    core_module._tracer = None

    # _TRACER_PROVIDER_SET_ONCE is opentelemetry.util._once.Once —
    # a Go-style sync.Once. Its _done flag prevents set_tracer_provider()
    # from running twice. Reset it so the next test can register a fresh provider.
    otel_trace._TRACER_PROVIDER = None
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = False