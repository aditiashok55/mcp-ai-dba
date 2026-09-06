"""Database connection helpers.

Every tool acquires a short-lived connection through :func:`connect`,
which applies our execution guards (statement timeout, read-only
transaction, dict row factory, application_name) uniformly. Tools must
not open raw ``psycopg.connect`` calls — that would bypass the guards.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from mcp_server.config import (
    APPLICATION_NAME,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    STATEMENT_TIMEOUT_MS,
)


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a guarded read-only connection.

    Guards applied:

    * ``statement_timeout`` — hard ceiling per statement, enforced by PG.
    * ``default_transaction_read_only`` — belt-and-braces on top of the
      dedicated read-only DB role.
    * ``application_name`` — identifies our sessions in stat views.
    * ``dict_row`` factory — tools receive rows as dicts, never tuples.
    """
    conn = psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        application_name=APPLICATION_NAME,
        row_factory=dict_row,
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            # SET does not accept bind parameters in Postgres; coerce to int
            # to keep this safe from injection.
            cur.execute(f"SET statement_timeout = {int(STATEMENT_TIMEOUT_MS)};")
            cur.execute("SET default_transaction_read_only = on;")
        conn.commit()
        yield conn
    finally:
        conn.close()
