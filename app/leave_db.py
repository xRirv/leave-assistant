"""leave_db.py: students, leave balances, holidays, requests and notifications."""

import json
import time
from collections.abc import Callable
from pathlib import Path

from app.db import connect, transaction


SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "leave.sql"


class LeaveDb:
    def __init__(
        self,
        path: str = ":memory:",
        clock: Callable[[], float] = time.time
    ):
        self.conn = connect(path)
        self.clock = clock

    def transaction(self):
        return transaction(self.conn)

    def migrate(self) -> None:
        self.conn.executescript(SCHEMA.read_text())

    # ---------------------------------------------------------
    # STUDENT
    # ---------------------------------------------------------

    def get_student(self, student_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM student WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        return dict(row) if row else None

    # ---------------------------------------------------------
    # LEAVE BALANCE
    # ---------------------------------------------------------

    def get_leave_balance(
        self,
        student_id: int,
        leave_type: str | None = None
    ) -> list[dict] | dict:

        if leave_type:
            row = self.conn.execute(
                """
                SELECT
                    leave_type,
                    total_leaves,
                    used_leaves,
                    (total_leaves - used_leaves) AS remaining_leaves
                FROM leave_balance
                WHERE student_id = ?
                  AND leave_type = ?
                """,
                (student_id, leave_type)
            ).fetchone()

            return dict(row) if row else None

        rows = self.conn.execute(
            """
            SELECT
                leave_type,
                total_leaves,
                used_leaves,
                (total_leaves - used_leaves) AS remaining_leaves
            FROM leave_balance
            WHERE student_id = ?
            ORDER BY leave_type
            """,
            (student_id,)
        ).fetchall()

        return [dict(row) for row in rows]

    # ---------------------------------------------------------
    # HOLIDAYS
    # ---------------------------------------------------------

    def list_holidays(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT holiday_id, holiday_date, name
            FROM holiday
            ORDER BY holiday_date
            """
        ).fetchall()

        return [dict(row) for row in rows]

    # ---------------------------------------------------------
    # POLICY
    # ---------------------------------------------------------

    def get_policy(self, leave_type: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM policy
            WHERE leave_type = ?
            """,
            (leave_type,)
        ).fetchone()

        return dict(row) if row else None

    # ---------------------------------------------------------
    # LEAVE REQUEST
    # ---------------------------------------------------------

    def get_leave_request(self, leave_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM leave_request
            WHERE leave_id = ?
            """,
            (leave_id,)
        ).fetchone()

        return dict(row) if row else None

    def create_leave_request(
        self,
        student_id: int,
        leave_type: str,
        start_date: str,
        end_date: str,
        days: int,
        reason: str | None = None,
    ) -> dict:

        if end_date < start_date:
            raise ValueError("invalid_dates")

        with self.transaction() as c:

            # Check leave balance
            balance = c.execute(
                """
                SELECT total_leaves, used_leaves
                FROM leave_balance
                WHERE student_id = ?
                  AND leave_type = ?
                """,
                (student_id, leave_type)
            ).fetchone()

            if balance is None:
                raise ValueError(
                    f"No {leave_type} leave balance found for student."
                )

            remaining = balance["total_leaves"] - balance["used_leaves"]

            if days > remaining:
                raise ValueError(
                    f"Insufficient {leave_type} leave balance. "
                    f"Available: {remaining}, requested: {days}."
                )

            # Check policy stored in DB
            policy = c.execute(
                """
                SELECT max_days
                FROM policy
                WHERE leave_type = ?
                """,
                (leave_type,)
            ).fetchone()

            if policy is None:
                raise ValueError(
                    f"No policy found for leave type: {leave_type}"
                )

            if days > policy["max_days"]:
                raise ValueError("policy_limit")

            # Reserve/use the leave
            updated = c.execute(
                """
                UPDATE leave_balance
                SET used_leaves = used_leaves + ?
                WHERE student_id = ?
                  AND leave_type = ?
                  AND used_leaves + ? <= total_leaves
                """,
                (
                    days,
                    student_id,
                    leave_type,
                    days,
                )
            ).rowcount

            if updated != 1:
                raise ValueError("Unable to update leave balance.")

            # Create request
            cur = c.execute(
                """
                INSERT INTO leave_request
                    (
                        student_id,
                        leave_type,
                        start_date,
                        end_date,
                        days,
                        reason,
                        status
                    )
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    student_id,
                    leave_type,
                    start_date,
                    end_date,
                    days,
                    reason,
                )
            )

            leave_id = cur.lastrowid

            row = c.execute(
                """
                SELECT *
                FROM leave_request
                WHERE leave_id = ?
                """,
                (leave_id,)
            ).fetchone()

            return dict(row)

    # ---------------------------------------------------------
    # WITHDRAW LEAVE
    # ---------------------------------------------------------

    def withdraw_leave(self, leave_id: int) -> dict:

        with self.transaction() as c:

            leave = c.execute(
                """
                SELECT *
                FROM leave_request
                WHERE leave_id = ?
                """,
                (leave_id,)
            ).fetchone()

            if leave is None:
                raise ValueError("Leave request not found.")

            if leave["status"] == "WITHDRAWN":
                return dict(leave)

            if leave["status"] not in ("PENDING", "APPROVED"):
                raise ValueError(
                    f"Cannot withdraw a {leave['status']} leave request."
                )

            # Restore balance
            c.execute(
                """
                UPDATE leave_balance
                SET used_leaves = used_leaves - ?
                WHERE student_id = ?
                  AND leave_type = ?
                """,
                (
                    leave["days"],
                    leave["student_id"],
                    leave["leave_type"],
                )
            )

            # Mark request withdrawn
            c.execute(
                """
                UPDATE leave_request
                SET status = 'WITHDRAWN'
                WHERE leave_id = ?
                """,
                (leave_id,)
            )

            row = c.execute(
                """
                SELECT *
                FROM leave_request
                WHERE leave_id = ?
                """,
                (leave_id,)
            ).fetchone()

            return dict(row)

    # ---------------------------------------------------------
    # NOTIFICATION
    # ---------------------------------------------------------

    def record_notification(
        self,
        leave_id: int,
        student_id: int,
        recipient: str,
        message: str
    ) -> tuple[int, bool]:

        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO notification
                (leave_id, student_id, recipient, message, status, created_at)
            VALUES (?, ?, ?, ?, 'SENT', ?)
            """,
            (
                leave_id,
                student_id,
                recipient,
                message,
                self.clock(),
            )
        )

        if cur.rowcount == 1:
            return cur.lastrowid, True

        row = self.conn.execute(
            """
            SELECT notification_id
            FROM notification
            WHERE leave_id = ?
              AND recipient = ?
            """,
            (leave_id, recipient)
        ).fetchone()

        return row["notification_id"], False

    # ---------------------------------------------------------
    # IDEMPOTENCY
    # ---------------------------------------------------------

    def once(
        self,
        key: str,
        tool_name: str,
        effect: Callable[[], dict]
    ) -> tuple[dict, bool]:

        with self.transaction() as c:

            row = c.execute(
                """
                SELECT result
                FROM idempotency
                WHERE key = ?
                """,
                (key,)
            ).fetchone()

            if row is not None:
                return json.loads(row["result"]), False

            result = effect()

            c.execute(
                """
                INSERT INTO idempotency
                    (key, tool_name, result, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    key,
                    tool_name,
                    json.dumps(result, default=str),
                    self.clock(),
                )
            )

            return result, True

    # ---------------------------------------------------------
    # UTILITY
    # ---------------------------------------------------------

    def count(self, table: str) -> int:
        assert table.isidentifier()

        return self.conn.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0]

    def close(self):
        if self.conn:
            self.conn.close()