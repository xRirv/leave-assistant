"""End-to-end tests for queue, worker, replay, and idempotency."""

import pytest

from app.providers import demo_providers
from app.worker import Worker
from tests.conftest import SimulatedCrash


def ask(
    store,
    student_id,
    text,
):
    thread = store.create_thread(
        str(student_id)
    )

    run_id = store.enqueue(
        thread,
        text,
        "mock",
    )

    return thread, run_id


def test_question_goes_through_supervisor_and_specialist(
    store,
    db,
):
    thread, run_id = ask(
        store,
        1,
        "What is my leave balance?",
    )

    result = Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-1",
    ).run_until_idle()

    assert result == [
        (run_id, "succeeded")
    ]

    run = store.get_run(run_id)

    assert run["status"] == "succeeded"

    history = store.load_history(
        thread
    )

    assert history[-1]["text"]


def test_apply_leave_goes_all_the_way_through(
    store,
    db,
):
    thread, run_id = ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-1",
    ).run_until_idle()

    run = store.get_run(run_id)

    assert run["status"] == "succeeded"

    assert db.count(
        "leave_request"
    ) == 1


def test_apply_leave_creates_notification(
    store,
    db,
):
    ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-1",
    ).run_until_idle()

    assert db.count(
        "notification"
    ) == 1


def test_two_different_runs_are_processed(
    store,
    db,
):
    _, first = ask(
        store,
        1,
        "What is my leave balance?",
    )

    _, second = ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    results = Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-1",
    ).run_until_idle()

    assert len(results) == 2

    assert store.get_run(first)["status"] == (
        "succeeded"
    )

    assert store.get_run(second)["status"] == (
        "succeeded"
    )


def test_cancelled_run_does_not_execute(
    store,
    db,
):
    _, run_id = ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    assert store.request_cancel(
        run_id
    ) is True

    result = Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-1",
    ).run_once()

    assert result == (
        run_id,
        "cancelled",
    )

    assert db.count(
        "leave_request"
    ) == 0


def test_dead_worker_run_is_replayed(
    store,
    db,
    clock,
):
    _, run_id = ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    worker_a = Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-A",
        lease_seconds=30,
    )

    claimed = store.claim_next(
        "worker-A",
        30,
    )

    assert claimed is not None

    # Simulate worker A dying.
    clock.advance(31)

    results = Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-B",
        lease_seconds=30,
    ).run_until_idle()

    assert results == [
        (run_id, "succeeded")
    ]

    assert db.count(
        "leave_request"
    ) == 1


def test_same_idempotency_key_does_not_duplicate_leave(
    db,
    store,
):
    from app.tools.leave_tools import (
        LeaveOperationsTools,
    )

    tools = LeaveOperationsTools(
        db,
        1,
        store,
    )

    args = {
        "leave_type": "CASUAL",
        "start_date": "2026-09-21",
        "end_date": "2026-09-22",
        "days": 2,
        "reason": "personal work",
    }

    first, replayed1 = tools.call_idempotent(
        "fixed-key",
        "apply_leave",
        args,
    )

    second, replayed2 = tools.call_idempotent(
        "fixed-key",
        "apply_leave",
        args,
    )

    assert replayed1 is False
    assert replayed2 is True

    assert first == second

    assert db.count(
        "leave_request"
    ) == 1


def test_withdraw_restores_balance(
    db,
):
    from app.tools.leave_tools import (
        LeaveOperationsTools,
    )

    tools = LeaveOperationsTools(
        db,
        1,
    )

    before = db.get_leave_balance(
        1,
        "CASUAL",
    )

    created = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-22",
        days=2,
        reason="personal work",
    )

    leave_id = created["leave_id"]

    withdrawn = tools.withdraw_leave(
        leave_id
    )

    assert withdrawn["status"] == (
        "withdrawn"
    )

    after = db.get_leave_balance(
        1,
        "CASUAL",
    )

    assert after["used_leaves"] == (
        before["used_leaves"]
    )


def test_withdraw_is_repeat_safe(
    db,
):
    from app.tools.leave_tools import (
        LeaveOperationsTools,
    )

    tools = LeaveOperationsTools(
        db,
        1,
    )

    created = tools.apply_leave(
        leave_type="CASUAL",
        start_date="2026-09-21",
        end_date="2026-09-22",
        days=2,
        reason="personal work",
    )

    leave_id = created["leave_id"]

    first = tools.withdraw_leave(
        leave_id
    )

    second = tools.withdraw_leave(
        leave_id
    )

    assert first["status"] == "withdrawn"
    assert second["status"] in (
        "already_withdrawn",
        "withdrawn",
    )


def test_hard_crash_does_not_create_duplicate_request(
    store,
    db,
    clock,
):
    _, run_id = ask(
        store,
        1,
        "Apply 2 CASUAL leave days.",
    )

    real_record = store.record_tool_call

    crashed = False

    def crash_once(*args, **kwargs):
        nonlocal crashed

        result = real_record(
            *args,
            **kwargs,
        )

        if not crashed:
            crashed = True
            raise SimulatedCrash()

        return result

    store.record_tool_call = crash_once

    with pytest.raises(SimulatedCrash):

        Worker(
            store,
            db,
            demo_providers(),
            worker_id="worker-A",
            lease_seconds=30,
        ).run_once()

    store.record_tool_call = real_record

    assert db.count(
        "leave_request"
    ) == 1

    clock.advance(31)

    Worker(
        store,
        db,
        demo_providers(),
        worker_id="worker-B",
        lease_seconds=30,
    ).run_until_idle()

    # Replay must not create a second request.
    assert db.count(
        "leave_request"
    ) == 1


def test_queue_claims_only_one_run(
    store,
    db,
):
    _, first = ask(
        store,
        1,
        "What is my leave balance?",
    )

    _, second = ask(
        store,
        1,
        "What is my leave balance?",
    )

    one = store.claim_next(
        "worker-A",
        60,
    )

    assert one is not None

    assert one.run_id in {
        first,
        second,
    }

    other = store.claim_next(
        "worker-B",
        60,
    )

    assert other is not None
    assert other.run_id != one.run_id