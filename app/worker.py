"""Worker: claim a run, execute it, and record how it ended."""

import logging
import os
import socket
import time
import uuid

from app.leave_db import LeaveDb
from app.memory import RunStore
from app.providers import AgentError
from app.runner import LeaseLost, execute_run


log = logging.getLogger("worker")


class Worker:

    def __init__(
        self,
        store: RunStore,
        db: LeaveDb,
        providers: dict,
        worker_id: str | None = None,
        lease_seconds: float = 60.0,
        on_step=None,
        record_delay: float = 0.0,
    ):

        self.store = store
        self.db = db
        self.providers = providers

        self.worker_id = (
            worker_id
            or (
                f"{socket.gethostname()}-"
                f"{os.getpid()}-"
                f"{uuid.uuid4().hex[:4]}"
            )
        )

        self.lease_seconds = lease_seconds
        self.on_step = on_step
        self.record_delay = record_delay

    def run_once(
        self,
    ) -> tuple[str, str] | None:

        # First recover runs whose previous worker died.
        self.store.reap_expired()

        # Claim the next queued run.
        claimed = self.store.claim_next(
            self.worker_id,
            self.lease_seconds,
        )

        if claimed is None:
            return None

        try:

            outcome = execute_run(
                claimed,
                store=self.store,
                db=self.db,
                providers=self.providers,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                on_step=self.on_step,
                record_delay=self.record_delay,
            )

        except LeaseLost:

            # Another worker owns the run now.
            outcome = "lease_lost"

        except AgentError as e:

            # Provider/tool error.
            outcome = (
                self.store.fail_attempt(
                    claimed.run_id,
                    self.worker_id,
                    e.code,
                    e.retryable,
                )
                or "lease_lost"
            )

        except Exception:

            log.exception(
                "run %s crashed",
                claimed.run_id,
            )

            outcome = (
                self.store.fail_attempt(
                    claimed.run_id,
                    self.worker_id,
                    "internal_error",
                    False,
                )
                or "lease_lost"
            )

        return (
            claimed.run_id,
            outcome,
        )

    def run_until_idle(
        self,
    ) -> list[tuple[str, str]]:

        done = []

        while True:

            item = self.run_once()

            if item is None:
                break

            done.append(item)

        return done

    def run_forever(
        self,
        poll_seconds: float = 1.0,
    ) -> None:

        while True:

            if self.run_once() is None:
                time.sleep(
                    poll_seconds
                )