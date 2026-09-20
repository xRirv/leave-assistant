import sqlite3
from contextlib import contextmanager


def connect(path: str = ":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn):
    started = conn.in_transaction
    try:
        if not started:
            conn.execute("BEGIN")
        yield conn
        if not started:
            conn.commit()
    except Exception:
        if not started:
            conn.rollback()
        raise