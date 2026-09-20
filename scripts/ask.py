"""Queue a leave-related question and wait for the answer.

Examples:

    python -m scripts.ask --student 1 "What is my leave balance?"
    python -m scripts.ask --student 1 "Apply 2 CASUAL leave days"
"""

import argparse
import time

from app.config import GEMINI_MODEL, open_stores
from scripts._term import CYAN, DIM, GREEN, RED, RESET


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "text",
        help="Question or leave request",
    )

    parser.add_argument(
        "--student",
        default="1",
        help="Student ID",
    )

    parser.add_argument(
        "--thread",
        help="Existing thread ID",
    )

    args = parser.parse_args()

    store, _ = open_stores()

    thread = (
        args.thread
        or store.create_thread(args.student)
    )

    run_id = store.enqueue(
        thread,
        args.text,
        GEMINI_MODEL,
    )

    print(
        f"{DIM}thread {thread}{RESET}\n"
        f"{CYAN}run {run_id}{RESET} "
        f"queued; waiting for a worker..."
    )

    while True:

        run = store.get_run(run_id)

        if run["status"] in (
            "succeeded",
            "failed",
            "cancelled",
            "dead",
        ):
            break

        time.sleep(0.5)

    if run["status"] == "succeeded":

        history = store.load_history(thread)

        print(
            f"{GREEN}assistant>{RESET} "
            f"{history[-1]['text']}"
        )

    else:

        print(
            f"{RED}"
            f"run ended: {run['status']} "
            f"({run['error_code']})"
            f"{RESET}"
        )


if __name__ == "__main__":
    main()