"""agent.db: conversations, runs, job queue and idempotency."""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.db import connect, transaction


SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "agent.sql"

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

TERMINAL = (
    "succeeded",
    "failed",
    "cancelled",
    "dead",
)


@dataclass(frozen=True)
class Claimed:
    run_id: str
    thread_id: str
    attempts: int


class RunStore:

    def __init__(
        self,
        path: str = ":memory:",
        clock: Callable[[], float] = time.time,
    ):
        self.conn = connect(path)
        self.clock = clock

    # ==================================================================
    # Database
    # ==================================================================

    def migrate(self) -> None:
        self.conn.executescript(
            SCHEMA.read_text()
        )

    def transaction(self):
        return transaction(self.conn)

    # ==================================================================
    # Conversations
    # ==================================================================

    def create_thread(self, student_id: str) -> str:
        thread_id = str(uuid.uuid4())

        self.conn.execute(
            """
            INSERT INTO thread (id, student_id)
            VALUES (?, ?)
            """,
            (
                thread_id,
                student_id,
            ),
        )

        return thread_id

    def get_thread(self, thread_id: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM thread
            WHERE id = ?
            """,
            (thread_id,),
        ).fetchone()

        return dict(row) if row else None

    def append_message(
        self,
        thread_id: str,
        role: str,
        text: str,
    ) -> int:

        cur = self.conn.execute(
            """
            INSERT INTO message
                (thread_id, seq, role, text)
            VALUES (
                ?,
                (
                    SELECT COALESCE(MAX(seq), 0) + 1
                    FROM message
                    WHERE thread_id = ?
                ),
                ?,
                ?
            )
            """,
            (
                thread_id,
                thread_id,
                role,
                text,
            ),
        )

        return self.conn.execute(
            """
            SELECT seq
            FROM message
            WHERE id = ?
            """,
            (cur.lastrowid,),
        ).fetchone()["seq"]

    def load_history(
        self,
        thread_id: str,
    ) -> list[dict]:

        rows = self.conn.execute(
            """
            SELECT seq, role, text
            FROM message
            WHERE thread_id = ?
            ORDER BY seq
            """,
            (thread_id,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    # ==================================================================
    # Run steps
    # ==================================================================

    def record_model_step(
        self,
        run_id: str,
        seq: int,
        tokens_in: int,
        tokens_out: int,
        text: str | None,
        tool_calls: list[dict],
    ) -> int:

        with self.transaction() as c:

            step_id = c.execute(
                """
                INSERT INTO run_step
                    (
                        run_id,
                        seq,
                        kind,
                        tokens_in,
                        tokens_out,
                        text,
                        tool_calls
                    )
                VALUES (
                    ?,
                    ?,
                    'model',
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    run_id,
                    seq,
                    tokens_in,
                    tokens_out,
                    text,
                    json.dumps(tool_calls),
                ),
            ).lastrowid

            c.execute(
                """
                UPDATE run
                SET
                    tokens_in = tokens_in + ?,
                    tokens_out = tokens_out + ?
                WHERE id = ?
                """,
                (
                    tokens_in,
                    tokens_out,
                    run_id,
                ),
            )

            return step_id

    def record_tool_call(
        self,
        run_id: str,
        seq: int,
        name: str,
        args: dict,
        result: dict,
        ok: bool,
        latency_ms: int,
        idempotency_key: str | None = None,
    ) -> int:

        with self.transaction() as c:

            step_id = c.execute(
                """
                INSERT INTO run_step
                    (run_id, seq, kind)
                VALUES (?, ?, 'tool')
                """,
                (
                    run_id,
                    seq,
                ),
            ).lastrowid

            c.execute(
                """
                INSERT INTO tool_call
                    (
                        run_step_id,
                        tool_name,
                        args,
                        result,
                        ok,
                        latency_ms,
                        idempotency_key
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    name,
                    json.dumps(args, default=str),
                    json.dumps(result, default=str),
                    int(ok),
                    latency_ms,
                    idempotency_key,
                ),
            )

            return step_id

    def load_steps(
        self,
        run_id: str,
    ) -> list[dict]:

        """Every recorded step of a run, in order, with JSON decoded."""

        rows = self.conn.execute(
            """
            SELECT
                s.seq,
                s.kind,
                s.text,
                s.tool_calls,
                t.tool_name,
                t.args,
                t.result,
                t.ok
            FROM run_step s
            LEFT JOIN tool_call t
                ON t.run_step_id = s.id
            WHERE s.run_id = ?
            ORDER BY s.seq
            """,
            (run_id,),
        ).fetchall()

        out = []

        for row in rows:

            data = dict(row)

            for key in (
                "tool_calls",
                "args",
                "result",
            ):
                data[key] = (
                    json.loads(data[key])
                    if data[key] is not None
                    else None
                )

            out.append(data)

        return out

    def get_run(
        self,
        run_id: str,
    ) -> dict | None:

        row = self.conn.execute(
            """
            SELECT *
            FROM run
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()

        if not row:
            return None

        return {
            **dict(row),
            "steps": self.load_steps(run_id),
        }

    # ==================================================================
    # Idempotency
    # ==================================================================

    def get_idempotency(
        self,
        key: str,
    ) -> dict | None:

        row = self.conn.execute(
            """
            SELECT *
            FROM idempotency
            WHERE key = ?
            """,
            (key,),
        ).fetchone()

        if row is None:
            return None

        return dict(row)

    def save_idempotency(
        self,
        key: str,
        tool_name: str,
        result: dict,
    ) -> None:

        with self.transaction() as c:

            c.execute(
                """
                INSERT OR IGNORE INTO idempotency
                    (
                        key,
                        tool_name,
                        result
                    )
                VALUES (?, ?, ?)
                """,
                (
                    key,
                    tool_name,
                    json.dumps(
                        result,
                        default=str,
                    ),
                ),
            )

    def run_idempotent(
        self,
        key: str,
        tool_name: str,
        operation: Callable[[], dict],
    ) -> tuple[dict, bool]:

        """
        Execute a side effect exactly once for a given key.

        Returns:

            (result, False)
                Operation was executed.

            (result, True)
                Existing result was replayed.
        """

        existing = self.get_idempotency(key)

        if existing is not None:

            return (
                json.loads(existing["result"]),
                True,
            )

        result = operation()

        self.save_idempotency(
            key,
            tool_name,
            result,
        )

        return (
            result,
            False,
        )

    # ==================================================================
    # Queue
    # ==================================================================

    def enqueue(
        self,
        thread_id: str,
        text: str,
        model: str,
        max_attempts: int = 3,
    ) -> str:

        """Save the user's message and a queued run together."""

        run_id = str(uuid.uuid4())

        with self.transaction() as c:

            self.append_message(
                thread_id,
                "user",
                text,
            )

            c.execute(
                """
                INSERT INTO run
                    (
                        id,
                        thread_id,
                        status,
                        model,
                        max_attempts,
                        available_at
                    )
                VALUES (
                    ?,
                    ?,
                    'queued',
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    run_id,
                    thread_id,
                    model,
                    max_attempts,
                    self.clock(),
                ),
            )

        return run_id

    def claim_next(
        self,
        worker_id: str,
        lease_seconds: float,
    ) -> Claimed | None:

        """
        Atomically take the oldest claimable run.

        queued + available now
        -> running + leased to this worker
        """

        now = self.clock()

        with self.transaction() as c:

            row = c.execute(
                """
                SELECT
                    id,
                    thread_id,
                    attempts
                FROM run
                WHERE status = 'queued'
                  AND available_at <= ?
                ORDER BY available_at, created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()

            if row is None:
                return None

            c.execute(
                f"""
                UPDATE run
                SET
                    status = 'running',
                    lease_owner = ?,
                    lease_until = ?,
                    attempts = attempts + 1,
                    started_at = COALESCE(
                        started_at,
                        {NOW_SQL}
                    )
                WHERE id = ?
                """,
                (
                    worker_id,
                    now + lease_seconds,
                    row["id"],
                ),
            )

            return Claimed(
                row["id"],
                row["thread_id"],
                row["attempts"] + 1,
            )

    def heartbeat(
        self,
        run_id: str,
        worker_id: str,
        lease_seconds: float,
    ) -> bool:

        """
        Extend the lease.

        False means this worker no longer owns the run.
        """

        cur = self.conn.execute(
            """
            UPDATE run
            SET lease_until = ?
            WHERE id = ?
              AND status = 'running'
              AND lease_owner = ?
            """,
            (
                self.clock() + lease_seconds,
                run_id,
                worker_id,
            ),
        )

        return cur.rowcount == 1

    def reap_expired(self) -> list[str]:

        """
        Handle runs whose workers died.

        If attempts remain:
            running -> queued

        Otherwise:
            running -> dead
        """

        now = self.clock()

        with self.transaction() as c:

            rows = c.execute(
                """
                SELECT
                    id,
                    attempts,
                    max_attempts
                FROM run
                WHERE status = 'running'
                  AND lease_until < ?
                """,
                (now,),
            ).fetchall()

            for row in rows:

                if row["attempts"] >= row["max_attempts"]:

                    c.execute(
                        f"""
                        UPDATE run
                        SET
                            status = 'dead',
                            error_code = 'lease_expired',
                            lease_owner = NULL,
                            lease_until = NULL,
                            finished_at = {NOW_SQL}
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )

                else:

                    c.execute(
                        """
                        UPDATE run
                        SET
                            status = 'queued',
                            error_code = 'lease_expired',
                            lease_owner = NULL,
                            lease_until = NULL,
                            available_at = ?
                        WHERE id = ?
                        """,
                        (
                            now,
                            row["id"],
                        ),
                    )

            return [
                row["id"]
                for row in rows
            ]

    def complete(
        self,
        run_id: str,
        worker_id: str,
        reply: str,
    ) -> bool:

        """
        Save the model reply and mark the run succeeded.

        Only the worker currently owning the lease may complete it.
        """

        with self.transaction() as c:

            row = c.execute(
                """
                SELECT thread_id
                FROM run
                WHERE id = ?
                  AND status = 'running'
                  AND lease_owner = ?
                """,
                (
                    run_id,
                    worker_id,
                ),
            ).fetchone()

            if row is None:
                return False

            self.append_message(
                row["thread_id"],
                "model",
                reply,
            )

            c.execute(
                f"""
                UPDATE run
                SET
                    status = 'succeeded',
                    lease_owner = NULL,
                    lease_until = NULL,
                    error_code = NULL,
                    finished_at = {NOW_SQL}
                WHERE id = ?
                """,
                (run_id,),
            )

            return True

    # ==================================================================
    # Cancel
    # ==================================================================

    def request_cancel(
        self,
        run_id: str,
    ) -> bool:

        """
        Queued:
            flag cancellation and let the worker stop before execution.

        Running:
            flag cancellation.

        Finished:
            leave unchanged.
        """

        with self.transaction() as c:

            row = c.execute(
                """
                SELECT status
                FROM run
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()

            if row is None:
                return False

            if row["status"] in ("queued", "running"):

                c.execute(
                    """
                    UPDATE run
                    SET cancel_requested = 1
                    WHERE id = ?
                    """,
                    (run_id,),
                )
                return True

            return False

    def cancel_requested(
        self,
        run_id: str,
    ) -> bool:

        row = self.conn.execute(
            """
            SELECT cancel_requested
            FROM run
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()

        return bool(row[0])

    def mark_cancelled(
        self,
        run_id: str,
        worker_id: str,
    ) -> bool:

        cur = self.conn.execute(
            f"""
            UPDATE run
            SET
                status = 'cancelled',
                lease_owner = NULL,
                lease_until = NULL,
                finished_at = {NOW_SQL}
            WHERE id = ?
              AND status = 'running'
              AND lease_owner = ?
            """,
            (
                run_id,
                worker_id,
            ),
        )

        return cur.rowcount == 1

    # ==================================================================
    # Retry / Dead Letter
    # ==================================================================

    def fail_attempt(
        self,
        run_id: str,
        worker_id: str,
        error_code: str,
        retryable: bool,
        backoff_seconds: float = 2.0,
    ) -> str | None:

        """
        Handle a failed attempt.

        Non-retryable:
            -> failed

        Retryable with attempts remaining:
            -> queued

        Retryable with no attempts remaining:
            -> dead
        """

        with self.transaction() as c:

            row = c.execute(
                """
                SELECT
                    attempts,
                    max_attempts
                FROM run
                WHERE id = ?
                  AND status = 'running'
                  AND lease_owner = ?
                """,
                (
                    run_id,
                    worker_id,
                ),
            ).fetchone()

            if row is None:
                return None

            if (
                retryable
                and row["attempts"] < row["max_attempts"]
            ):

                delay = (
                    backoff_seconds
                    * 2 ** (row["attempts"] - 1)
                )

                c.execute(
                    """
                    UPDATE run
                    SET
                        status = 'queued',
                        error_code = ?,
                        lease_owner = NULL,
                        lease_until = NULL,
                        available_at = ?
                    WHERE id = ?
                    """,
                    (
                        error_code,
                        self.clock() + delay,
                        run_id,
                    ),
                )

                return "queued"

            status = (
                "dead"
                if retryable
                else "failed"
            )

            c.execute(
                f"""
                UPDATE run
                SET
                    status = ?,
                    error_code = ?,
                    lease_owner = NULL,
                    lease_until = NULL,
                    finished_at = {NOW_SQL}
                WHERE id = ?
                """,
                (
                    status,
                    error_code,
                    run_id,
                ),
            )

            return status

    # ==================================================================
    # Close
    # ==================================================================

    def close(self) -> None:
        self.conn.close()