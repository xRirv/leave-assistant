"""Tests for supervisor delegation and specialist separation."""

from app.agents import (
    SupervisorTools,
    run_tool,
)
from app.providers import demo_providers


def test_supervisor_only_has_delegation_tools(
    db,
    store,
):
    tools = SupervisorTools(
        db,
        demo_providers(),
        1,
        store,
    )

    assert set(tools.functions()) == {
        "ask_leave_info",
        "ask_leave_operations",
    }

    assert set(tools.DELEGATES) == set(
        tools.TOOL_NAMES
    )


def test_leave_info_specialist_can_check_balance(
    db,
    store,
):
    providers = demo_providers()

    tools = SupervisorTools(
        db,
        providers,
        1,
        store,
    )

    result, replayed = run_tool(
        tools,
        db,
        "balance-key",
        "ask_leave_info",
        {
            "question": "Check my leave balance."
        },
    )

    assert result["agent"] == "leave_info"
    assert (
        result["tools_used"]
        == ["get_leave_balance"]
    )
    assert replayed is False


def test_operations_specialist_can_apply_leave(
    db,
    store,
):
    providers = demo_providers()

    tools = SupervisorTools(
        db,
        providers,
        1,
        store,
    )

    result, replayed = run_tool(
        tools,
        db,
        "apply-key",
        "ask_leave_operations",
        {
            "request": (
                "Apply 2 CASUAL leave days "
                "from 2026-09-21 to 2026-09-22."
            )
        },
    )

    assert result["agent"] == "leave_operations"
    assert replayed is False

    assert "apply_leave" in result[
        "tools_used"
    ]

    assert db.count(
        "leave_request"
    ) == 1


def test_repeated_delegation_is_safe(
    db,
    store,
):
    providers = demo_providers()

    tools = SupervisorTools(
        db,
        providers,
        1,
        store,
    )

    args = {
        "request": (
            "Apply 2 CASUAL leave days "
            "from 2026-09-21 to 2026-09-22."
        )
    }

    run_tool(
        tools,
        db,
        "same-key",
        "ask_leave_operations",
        args,
    )

    run_tool(
        tools,
        db,
        "same-key",
        "ask_leave_operations",
        args,
    )

    # The same side effect must not create another request.
    assert db.count(
        "leave_request"
    ) == 1


def test_invalid_delegation_arguments_are_rejected(
    db,
    store,
):
    tools = SupervisorTools(
        db,
        demo_providers(),
        1,
        store,
    )

    result, _ = run_tool(
        tools,
        db,
        "bad-key",
        "ask_leave_operations",
        {
            "request": ""
        },
    )

    assert result["error"] == (
        "invalid_arguments"
    )


def test_supervisor_tools_do_not_expose_domain_write_tools(
    db,
    store,
):
    tools = SupervisorTools(
        db,
        demo_providers(),
        1,
        store,
    )

    names = set(
        tools.functions()
    )

    assert "apply_leave" not in names
    assert "withdraw_leave" not in names
    assert "notify_hod" not in names


def test_specialists_are_separated(
    db,
    store,
):
    providers = demo_providers()

    tools = SupervisorTools(
        db,
        providers,
        1,
        store,
    )

    run_tool(
        tools,
        db,
        "info-key",
        "ask_leave_info",
        {
            "question": "What is my leave balance?"
        },
    )

    assert providers[
        "leave_operations"
    ].calls == []

    assert providers[
        "leave_info"
    ].calls