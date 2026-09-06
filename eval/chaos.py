"""Reproducible failure injectors for the eval harness.

Each scenario is a class exposing ``start()`` / ``stop()`` and a
``expected_findings`` list of keywords the agent's diagnosis should
contain to count as "correct".

The injectors run background threads that open **admin-role**
connections (they need to hold locks / stay idle-in-transaction, which
the read-only role cannot do). The agent still reads via the
read-only role — the trust boundary is unaffected.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

import psycopg


ADMIN_URL = os.getenv(
    "AI_DBA_ADMIN_URL",
    "postgresql://admin:admin_password@localhost:5432/ai_dba",
)


# --- shared plumbing ------------------------------------------------------


class _Background:
    def __init__(self, name: str) -> None:
        self.name = name
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _spawn(self, target) -> None:
        t = threading.Thread(target=target, name=self.name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)


# --- scenarios ------------------------------------------------------------


class SlowQueryScenario(_Background):
    """Holds a long ``pg_sleep`` open in a background session."""

    expected_findings = ("slow", "sleep", "long-running")

    def __init__(self) -> None:
        super().__init__(name="chaos_slow_query")

    def start(self) -> None:
        def worker() -> None:
            with psycopg.connect(ADMIN_URL, application_name="chaos_slow_query") as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_sleep(30);")
                    # unreachable in normal run; stop() cancels the conn
            _ = self._stop
        self._spawn(worker)


class LockContentionScenario(_Background):
    """Session A holds a row lock; session B blocks trying to update it."""

    expected_findings = ("lock", "block", "contention")

    def __init__(self) -> None:
        super().__init__(name="chaos_lock_contention")

    def start(self) -> None:
        started = threading.Event()

        def holder() -> None:
            with psycopg.connect(ADMIN_URL, application_name="chaos_lock_holder") as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE customers SET name = name WHERE customer_id = 1;"
                    )
                    started.set()
                    # Hold the lock until stop.
                    while not self._stop.is_set():
                        time.sleep(0.2)
                    conn.rollback()

        def waiter() -> None:
            started.wait(timeout=5)
            with psycopg.connect(ADMIN_URL, application_name="chaos_lock_waiter") as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    # Will block on the holder's row lock.
                    try:
                        cur.execute(
                            "UPDATE customers SET name = name WHERE customer_id = 1;"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    conn.rollback()

        self._spawn(holder)
        self._spawn(waiter)


class IdleInTransactionScenario(_Background):
    """Opens N connections that go idle-in-transaction — a real leak signature."""

    expected_findings = ("idle in transaction", "idle-in-transaction", "connection")

    def __init__(self, n: int = 5) -> None:
        super().__init__(name="chaos_idle_in_txn")
        self.n = n

    def start(self) -> None:
        def worker() -> None:
            with psycopg.connect(
                ADMIN_URL, application_name="chaos_idle_in_txn"
            ) as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                while not self._stop.is_set():
                    time.sleep(0.2)
                conn.rollback()

        for _ in range(self.n):
            self._spawn(worker)


@dataclass
class Scenario:
    key: str
    label: str
    factory: type[_Background]
    expected_findings: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: dict[str, Scenario] = {
    "slow_query": Scenario(
        "slow_query",
        "Long-running query",
        SlowQueryScenario,
        SlowQueryScenario.expected_findings,
    ),
    "lock_contention": Scenario(
        "lock_contention",
        "Lock contention on customers row 1",
        LockContentionScenario,
        LockContentionScenario.expected_findings,
    ),
    "idle_in_transaction": Scenario(
        "idle_in_transaction",
        "Idle-in-transaction connection leak",
        IdleInTransactionScenario,
        IdleInTransactionScenario.expected_findings,
    ),
}
