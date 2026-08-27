"""Graceful degradation: Redis backends fall back to in-process ones (stretch goal).

These tests point at an unreachable Redis URL, so they need no running Redis and
stay green in CI. They assert the wrappers never raise and keep working locally.
"""
from __future__ import annotations

from reliability_lab.cache import ResilientCache
from reliability_lab.circuit_breaker import CircuitOpenError, CircuitState, ResilientCircuitBreaker

DEAD_URL = "redis://127.0.0.1:6399/0"  # nothing listens here
# Fast-fail settings so these tests don't sit on connect timeouts.
FAST = {"connect_timeout": 0.3, "retries": 0}


# --- cache ---------------------------------------------------------------

def test_resilient_cache_starts_degraded_and_serves_locally() -> None:
    c = ResilientCache(DEAD_URL, ttl_seconds=60, similarity_threshold=0.5, recheck_seconds=999, **FAST)
    assert c.degraded is True
    assert c.degraded_events >= 1

    c.set("hello world", "response")          # must not raise
    cached, score = c.get("hello world")      # served from the local ResponseCache
    assert cached == "response"
    assert score == 1.0


def test_resilient_cache_keeps_privacy_and_false_hit_guardrails_while_degraded() -> None:
    c = ResilientCache(DEAD_URL, ttl_seconds=60, similarity_threshold=0.3, recheck_seconds=999, **FAST)
    c.set("password reset for user 456", "secret")
    assert c.get("password reset for user 456")[0] is None  # privacy guard

    c.set("Summarize refund policy for 2024 deadline", "old")
    assert c.get("Summarize refund policy for 2026 deadline")[0] is None  # false-hit guard
    assert len(c.false_hit_log) == 1


# --- circuit breaker --------------------------------------------------

def test_resilient_breaker_starts_degraded_and_uses_local_state_machine() -> None:
    cb = ResilientCircuitBreaker(
        "primary", DEAD_URL, failure_threshold=3, reset_timeout_seconds=1, recheck_seconds=999, **FAST
    )
    assert cb.degraded is True
    assert cb.state is CircuitState.CLOSED
    assert cb.allow_request() is True

    for _ in range(3):
        cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert cb.allow_request() is False
    assert cb.transition_log[-1]["reason"] == "failure_threshold_reached"


def test_resilient_breaker_call_fails_fast_when_open_locally() -> None:
    cb = ResilientCircuitBreaker(
        "primary", DEAD_URL, failure_threshold=1, reset_timeout_seconds=10, recheck_seconds=999, **FAST
    )
    cb.record_failure()
    assert cb.state is CircuitState.OPEN

    ran = False

    def _fn() -> str:
        nonlocal ran
        ran = True
        return "x"

    try:
        cb.call(_fn)
    except CircuitOpenError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected CircuitOpenError")
    assert ran is False  # never touched the wrapped call
