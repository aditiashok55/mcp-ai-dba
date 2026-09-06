"""Per-tool token-bucket rate limiter.

Caps a runaway agent loop before it drowns the database in stat
queries. In-process only (this is a single-user MCP server); a
distributed deployment would swap this out for Redis.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    capacity: float
    tokens: float
    refill_per_sec: float
    last: float


class RateLimiter:
    def __init__(self, capacity: int = 10, refill_per_sec: float = 2.0) -> None:
        self._capacity = float(capacity)
        self._refill = refill_per_sec
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, key: str) -> _Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(self._capacity, self._capacity, self._refill, time.monotonic())
            self._buckets[key] = b
        return b

    def try_acquire(self, key: str) -> bool:
        with self._lock:
            b = self._bucket(key)
            now = time.monotonic()
            b.tokens = min(b.capacity, b.tokens + (now - b.last) * b.refill_per_sec)
            b.last = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True
            return False


# Module-level singleton — read from config at import time so it can
# be tuned via env without touching tool code.
from mcp_server.config import RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL_PER_SEC

limiter = RateLimiter(
    capacity=RATE_LIMIT_CAPACITY,
    refill_per_sec=RATE_LIMIT_REFILL_PER_SEC,
)
