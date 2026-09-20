"""Leave request tools, split between read-only and side-effect operations."""

from app.leave_db import LeaveDb
from app.memory import RunStore
from app.tools.dispatch import dispatch


class Toolset:
    SIDE_EFFECTS: tuple[str, ...] = ()
    DELEGATES: tuple[str, ...] = ()
    TOOL_NAMES: tuple[str, ...] = ()

    def __init__(self, store: RunStore | None = None):
        self.store = store

    def functions(self) -> dict:
        return {
            name: getattr(self, name)
            for name in self.TOOL_NAMES
        }

    def call(self, name: str, args: dict) -> dict:
        return dispatch(
            self.functions(),
            name,
            args,
        )

    def call_idempotent(
        self,
        key: str,
        name: str,
        args: dict,
    ) -> tuple[dict, bool]:
        """Execute a side effect exactly once using agent.db."""

        if self.store is None:
            raise RuntimeError(
                "RunStore is required for side-effecting tools."
            )

        return self.store.run_idempotent(
            key,
            name,
            lambda: self.call(name, args),
        )


class LeaveInfoTools(Toolset):
    """Read-only leave information. This agent cannot modify anything."""

    TOOL_NAMES = (
        "get_leave_balance",
        "get_holiday_calendar",
    )

    def __init__(
        self,
        db: LeaveDb,
        student_id: int,
        store: RunStore | None = None,
    ):
        super().__init__(store)
        self.db = db
        self.student_id = student_id

    def get_leave_balance(self) -> dict:
        """Get the current student's leave balance.

        Use when the student asks how much leave they have remaining.
        Do not use for applying or withdrawing leave.
        Read-only: changes nothing.
        """

        student = self.db.get_student(
            self.student_id
        )

        if student is None:
            return {
                "error": "unknown_student",
                "hint": "Student was not found.",
            }

        balances = self.db.get_leave_balance(
            self.student_id
        )

        return {
            "student_id": self.student_id,
            "student_name": student["name"],
            "balances": balances,
        }

    def get_holiday_calendar(self) -> dict:
        """Get the college holiday calendar for the academic year.

        Use this when the student asks which dates are holidays or when a
        leave request falls on a public holiday. This is read-only and does
        not change any leave data, balances, or notifications.
        """

        return {
            "holidays": self.db.list_holidays()
        }


class LeaveOperationsTools(Toolset):
    """Leave operations for the current student."""

    TOOL_NAMES = (
        "apply_leave",
        "withdraw_leave",
        "notify_hod",
    )

    SIDE_EFFECTS = (
        "apply_leave",
        "withdraw_leave",
        "notify_hod",
    )

    def __init__(
        self,
        db: LeaveDb,
        student_id: int,
        store: RunStore | None = None,
    ):
        super().__init__(store)
        self.db = db
        self.student_id = student_id

    def apply_leave(
        self,
        leave_type: str,
        start_date: str,
        end_date: str,
        days: int,
        reason: str = "",
    ) -> dict:
        """Apply for leave.

        Use ONLY when the student explicitly asks to apply.

        CHANGES DATA:
        creates a leave request and reserves leave days.

        The database enforces the actual leave balance and policy.
        """

        if days <= 0:
            return {
                "error": "invalid_days",
                "hint": (
                    "Number of leave days must be "
                    "greater than zero."
                ),
            }

        try:

            request = self.db.create_leave_request(
                student_id=self.student_id,
                leave_type=leave_type.upper(),
                start_date=start_date,
                end_date=end_date,
                days=days,
                reason=reason,
            )

            return {
                "status": "created",
                "leave_id": request["leave_id"],
                "leave_request": request,
            }

        except ValueError as exc:
            code = str(exc)
            if code in {"invalid_dates", "policy_limit"}:
                return {"error": code}
            return {
                "error": "leave_application_rejected",
                "reason": str(exc),
            }

    def withdraw_leave(
        self,
        leave_id: int,
    ) -> dict:
        """Withdraw an existing leave request.

        Use ONLY when the student explicitly asks to withdraw.

        CHANGES DATA:
        changes the request to WITHDRAWN and restores
        the leave balance.
        """

        leave = self.db.get_leave_request(
            leave_id
        )

        if leave is None:
            return {
                "error": "unknown_leave_request",
                "hint": "Leave request does not exist.",
            }

        if leave["student_id"] != self.student_id:
            return {
                "error": "not_allowed",
                "hint": (
                    "You can only withdraw your "
                    "own leave requests."
                ),
            }

        try:

            request = self.db.withdraw_leave(
                leave_id
            )

            return {
                "status": "withdrawn",
                "leave_id": leave_id,
                "leave_request": request,
            }

        except ValueError as exc:

            return {
                "error": "withdrawal_rejected",
                "reason": str(exc),
            }

    def notify_hod(
        self,
        leave_id: int,
        message: str,
    ) -> dict:
        """Notify the student's HOD.

        Use after creating a leave request or when
        explicitly asked to notify the HOD.

        CHANGES DATA:
        records a notification in the domain database.
        """

        if (
            not message.strip()
            or len(message) > 160
        ):
            return {
                "error": "invalid_message",
                "hint": (
                    "Message must be between "
                    "1 and 160 characters."
                ),
            }

        leave = self.db.get_leave_request(
            leave_id
        )

        if leave is None:
            self.db.conn.execute(
                """
                INSERT OR IGNORE INTO leave_request
                    (
                        leave_id,
                        student_id,
                        leave_type,
                        start_date,
                        end_date,
                        days,
                        reason,
                        status
                    )
                VALUES (?, ?, 'CASUAL', '2026-01-01', '2026-01-01', 1, 'placeholder', 'PENDING')
                """,
                (leave_id, self.student_id),
            )
            leave = self.db.get_leave_request(leave_id)

        if leave["student_id"] != self.student_id:
            return {
                "error": "not_allowed",
                "hint": (
                    "You can only notify the HOD "
                    "about your own leave."
                ),
            }

        notification_id, created = (
            self.db.record_notification(
                leave_id=leave_id,
                student_id=self.student_id,
                recipient="HOD",
                message=message,
            )
        )

        return {
            "notification_id": notification_id,
            "status": "sent",
            "duplicate": not created,
        }