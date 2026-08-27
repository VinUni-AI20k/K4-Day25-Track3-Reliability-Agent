"""Stretch goal: graceful degradation when Redis goes away.

Shows ResilientCache / ResilientCircuitBreaker:
  1. Redis UP    -> served from Redis
  2. Redis DOWN  -> transparently served from the in-process fallback, no error
  3. Redis BACK  -> promotes itself again after the recheck window

Redis "going down" is simulated by swapping the live client for a stub that
raises on every call, so the demo needs no container restart.
"""
from __future__ import annotations

import time

import redis.exceptions

from reliability_lab.cache import ResilientCache
from reliability_lab.circuit_breaker import ResilientCircuitBreaker

URL = "redis://localhost:6379/0"
RECHECK = 2.0


class _BrokenClient:
    """Every attribute access returns a callable that raises, like a dead Redis."""

    def __getattr__(self, _name: str):
        def _boom(*_a: object, **_k: object) -> object:
            raise redis.exceptions.ConnectionError("simulated Redis outage")

        return _boom


def demo_cache() -> None:
    print("== ResilientCache ==")
    c = ResilientCache(URL, ttl_seconds=60, similarity_threshold=0.5, recheck_seconds=RECHECK)
    if c.degraded:
        print("  (Redis not reachable — start docker compose up -d to see step 1)")
    c.set("explain circuit breaker states", "CLOSED / OPEN / HALF_OPEN ...")
    val, _ = c.get("explain circuit breaker states")
    print(f"  1. Redis up   : degraded={c.degraded}  get -> {val!r}")

    live = c._redis_cache._redis
    c._redis_cache._redis = _BrokenClient()
    val, _ = c.get("explain circuit breaker states")     # falls back to local ResponseCache
    c.set("new during outage", "served + stored locally")
    print(f"  2. Redis down : degraded={c.degraded}  degraded_events={c.degraded_events}  get -> {val!r}  (no exception)")

    c._redis_cache._redis = live
    time.sleep(RECHECK + 0.1)
    val, _ = c.get("explain circuit breaker states")
    print(f"  3. Redis back : degraded={c.degraded}  get -> {val!r}")
    c.close()


def demo_breaker() -> None:
    print("\n== ResilientCircuitBreaker ==")
    cb = ResilientCircuitBreaker(
        "primary", URL, failure_threshold=3, reset_timeout_seconds=5, recheck_seconds=RECHECK
    )
    cb.reset()
    print(f"  1. Redis up   : degraded={cb.degraded}  state={cb.state.value}")

    live = cb._redis_cb._redis
    cb._redis_cb._redis = _BrokenClient()
    for _ in range(3):
        cb.record_failure()              # tracked on the local breaker
    print(
        f"  2. Redis down : degraded={cb.degraded}  degraded_events={cb.degraded_events}  "
        f"state={cb.state.value}  allow_request={cb.allow_request()}  (local state machine)"
    )

    cb._redis_cb._redis = live
    time.sleep(RECHECK + 0.1)
    print(f"  3. Redis back : degraded={cb.degraded}  state={cb.state.value}  (shared state again)")
    cb.reset()


if __name__ == "__main__":
    demo_cache()
    demo_breaker()
