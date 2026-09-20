"""Complete leave-request demo.

Usage:

    python -m scripts.demo
    python -m scripts.demo --real
    python -m scripts.demo --crash

The demo uses temporary SQLite databases.
"""

import argparse
import os
import tempfile

from scripts._term import (
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    print_step,
)


QUESTIONS = [
    (
        "1",
        "What is my leave balance?",
    ),
    (
        "1",
        "Apply 2 CASUAL leave days from "
        "2026-09-21 to 2026-09-22. "
        "Reason: personal work.",
    ),
]


class Crash(BaseException):
    """Simulate a worker being killed."""


def counts(db) -> str:
    return (
        f"leave_requests={db.count('leave_request')}   "
        f"notifications={db.count('notification')}"
    )


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--real",
        action="store_true",
        help="Use Gemini instead of scripted models.",
    )

    parser.add_argument(
        "--crash",
        action="store_true",
        help="Demonstrate worker crash and replay.",
    )

    args = parser.parse_args()

    # ---------------------------------------------------------
    # Temporary databases
    # ---------------------------------------------------------

    tmp = tempfile.mkdtemp(
        prefix="leave-demo-"
    )

    os.environ["AGENT_DB"] = os.path.join(
        tmp,
        "agent.db",
    )

    os.environ["LEAVE_DB"] = os.path.join(
        tmp,
        "leave.db",
    )

    from app.config import (
        make_providers,
        open_stores,
    )

    from app.worker import Worker

    store, db = open_stores()

    providers = make_providers(
        mock=not args.real
    )

    print(
        f"{DIM}"
        f"databases in {tmp}   "
        f"model: {providers['supervisor'].model}"
        f"{RESET}"
    )

    print(
        f"{DIM}"
        f"before: {counts(db)}"
        f"{RESET}\n"
    )

    # Crash mode focuses on the side-effecting leave request.
    questions = (
        QUESTIONS[1:]
        if args.crash
        else QUESTIONS
    )

    for student_id, text in questions:

        thread = store.create_thread(
            student_id
        )

        run_id = store.enqueue(
            thread,
            text,
            providers["supervisor"].model,
        )

        print(
            f"{CYAN}"
            f"student {student_id}>"
            f"{RESET} {text}"
        )

        if args.crash:

            # Simulate the worker dying AFTER the side effect
            # and idempotency record have been stored, but
            # BEFORE the tool-call record is written.
            real_record_tool_call = (
                store.record_tool_call
            )

            crashed = False

            def record_then_die(*args, **kwargs):
                nonlocal crashed

                result = real_record_tool_call(
                    *args,
                    **kwargs,
                )

                if not crashed:
                    crashed = True
                    raise Crash()

                return result

            store.record_tool_call = record_then_die

            try:

                Worker(
                    store,
                    db,
                    providers,
                    worker_id="worker-A",
                    lease_seconds=60,
                    on_step=print_step,
                ).run_once()

            except Crash:

                store.record_tool_call = (
                    real_record_tool_call
                )

                print(
                    f"\n  {RED}"
                    f"worker-A died during tool recording"
                    f"{RESET}"
                )

                print(
                    f"  {DIM}"
                    f"{counts(db)}; "
                    f"run is "
                    f"'{store.get_run(run_id)['status']}'"
                    f"{RESET}"
                )

                # Pretend the worker's lease expired.
                store.clock = (
                    lambda:
                    __import__("time").time() + 61
                )

                print(
                    f"  {DIM}"
                    f"...lease expires, "
                    f"worker-B claims the run"
                    f"{RESET}\n"
                )

            Worker(
                store,
                db,
                providers,
                worker_id="worker-B",
                lease_seconds=60,
                on_step=print_step,
            ).run_until_idle()

        else:

            Worker(
                store,
                db,
                providers,
                worker_id="demo-worker",
                on_step=print_step,
            ).run_until_idle()

        run = store.get_run(run_id)

        colour = (
            GREEN
            if run["status"] == "succeeded"
            else RED
        )

        if run["status"] == "succeeded":

            reply = (
                store.load_history(thread)[-1]["text"]
            )

        else:

            reply = run["error_code"]

        print(
            f"{colour}"
            f"assistant>{RESET} {reply}"
        )

        print(
            f"{DIM}"
            f"run {run_id[:8]} "
            f"{run['status']} after "
            f"{run['attempts']} attempt(s), "
            f"{run['tokens_in']}+"
            f"{run['tokens_out']} "
            f"supervisor tokens"
            f"{RESET}\n"
        )

    print(
        f"after: {counts(db)}"
    )

    if args.crash:

        request_count = db.count(
            "leave_request"
        )

        notification_count = db.count(
            "notification"
        )

        ok = (
            request_count == 1
            and notification_count == 1
        )

        if ok:

            print(
                f"{GREEN}"
                f"PASS: one leave request, "
                f"one notification, "
                f"no duplicate side effects"
                f"{RESET}"
            )

        else:

            print(
                f"{RED}"
                f"FAIL: duplicate or missing "
                f"side effects"
                f"{RESET}"
            )


if __name__ == "__main__":
    main()