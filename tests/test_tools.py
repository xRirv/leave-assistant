"""Tests for leave tools, business rules, and idempotent writes."""

import inspect

from app.tools.leave_tools import (
    LeaveInfoTools,
    LeaveOperationsTools,
)


def test_every_tool_is_described():
    for cls in (
        LeaveInfoTools,
        LeaveOperationsTools,
    ):
        for name in cls.TOOL_NAMES:
            assert len(
                inspect.getdoc(
                    getattr(cls, name)
                ) or ""
            ) >= 120


def test_leave_balance_is_read_only(db):
    tools = LeaveInfoTools(
        db,
        1,
    )

    result = tools.get_leave_balance()

    assert result["student_id"] == 1
    assert "balances" in result
    assert result["balances"]


def test_holiday_calendar_is_read_only(db):
    tools = LeaveInfoTools(
        db,
        1,
    )

    result = tools.get_holiday_calendar()

    assert "holidays" in result
    assert len(result["holidays"]) >= 1


def test_apply_leave_creates_pending_request(db):
    tools = LeaveOperationsTools(
        db,
        1,
    )

    result = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-22",
        days=2,
        reason="personal work",
    )

    assert result["status"] == "created"
    assert result["leave_id"] >= 1


def test_policy_is_enforced_by_the_database_rule(db):
    tools = LeaveOperationsTools(
        db,
        1,
    )

    # CASUAL policy allows at most 5 days.
    result = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-30",
        days=6,
        reason="too long",
    )

    assert result["error"] == "policy_limit"


def test_balance_is_updated_after_leave(db):
    before = db.get_leave_balance(
        1,
        "CASUAL",
    )

    tools = LeaveOperationsTools(
        db,
        1,
    )

    result = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-22",
        days=2,
        reason="personal work",
    )

    assert result["status"] == "created"

    after = db.get_leave_balance(
        1,
        "CASUAL",
    )

    assert after["used_leaves"] == (
        before["used_leaves"] + 2
    )


def test_invalid_date_range_is_rejected(db):
    tools = LeaveOperationsTools(
        db,
        1,
    )

    result = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-25",
        end_date="2026-09-21",
        days=2,
        reason="invalid",
    )

    assert result["error"] == "invalid_dates"


def test_invalid_days_are_rejected(db):
    tools = LeaveOperationsTools(
        db,
        1,
    )

    result = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-22",
        days=0,
        reason="invalid",
    )

    assert result["error"] == "invalid_days"


def test_notification_is_repeat_safe(db):
    tools = LeaveOperationsTools(
        db,
        1,
    )

    first = tools.notify_hod(
        leave_id=1,
        message="Student has submitted leave.",
    )

    second = tools.notify_hod(
        leave_id=1,
        message="Student has submitted leave.",
    )

    assert first["notification_id"] == (
        second["notification_id"]
    )


def test_operations_tools_are_bound_to_one_student(db):
    params = {
        name: list(
            inspect.signature(
                getattr(
                    LeaveOperationsTools,
                    name,
                )
            ).parameters
        )
        for name in LeaveOperationsTools.TOOL_NAMES
    }

    for parameters in params.values():
        assert "student_id" not in parameters