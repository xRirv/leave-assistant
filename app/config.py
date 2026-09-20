import os

from app.leave_db import LeaveDb
from app.memory import RunStore


LEAVE_DB = os.environ.get(
    "LEAVE_DB",
    "leave.db",
)

AGENT_DB = os.environ.get(
    "AGENT_DB",
    "agent.db",
)

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash",
)


def open_stores() -> tuple[RunStore, LeaveDb]:
    """Open and migrate the two SQLite databases."""

    store = RunStore(AGENT_DB)
    db = LeaveDb(LEAVE_DB)

    store.migrate()
    db.migrate()

    return store, db


def make_providers(
    mock: bool,
    slow: float = 0.0,
) -> dict:
    """Create one provider for each agent."""

    if mock:

        from app.providers import demo_providers

        return demo_providers(slow)

    from app.providers import GeminiProvider

    return {
        "supervisor": GeminiProvider(
            GEMINI_MODEL
        ),

        "leave_info": GeminiProvider(
            GEMINI_MODEL
        ),

        "leave_operations": GeminiProvider(
            GEMINI_MODEL
        ),
    }