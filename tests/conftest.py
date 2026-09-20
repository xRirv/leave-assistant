import pytest

from app.leave_db import LeaveDb
from app.memory import RunStore


class FakeClock:
    def __init__(self, start: float = 1_790_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SimulatedCrash(BaseException):
    """Simulate a hard worker crash."""


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db():
    database = LeaveDb(":memory:")
    database.migrate()
    return database


@pytest.fixture
def store(clock):
    agent_store = RunStore(":memory:", clock)
    agent_store.migrate()
    return agent_store