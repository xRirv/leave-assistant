"""Stable fingerprints for side effects."""

import hashlib
import json
from datetime import date


def _normalise(value):
    """Make equal things look equal."""

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]

    return value


def canonical_json(value) -> str:
    """One stable representation for equivalent values."""

    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def idempotency_key(
    run_id: str,
    step_seq: int,
    tool_name: str,
    args: dict
) -> str:
    """Same tool call at the same step gets the same key."""

    return _sha256(
        canonical_json(
            [run_id, step_seq, tool_name, args]
        )
    )


def notification_dedupe_key(
    student_id: int,
    message: str,
    day: date
) -> str:
    """Same message to same student on same day gets one notification."""

    return _sha256(
        canonical_json(
            [
                student_id,
                " ".join(message.split()),
                day.isoformat(),
            ]
        )
    )