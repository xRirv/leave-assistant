import json
import os

DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
RESET = "\033[0m"

if os.name == "nt":
    os.system("")


def short(obj, limit=130) -> str:
    text = json.dumps(obj, default=str)
    return (
        text
        if len(text) <= limit
        else text[:limit - 3] + "..."
    )


def print_step(step: dict) -> None:
    """Print supervisor and specialist execution steps."""

    agent = step.get("agent", "supervisor")

    if step["kind"] == "model":
        return

    if step["kind"] == "delegate":

        print(
            f"  {YELLOW}"
            f"supervisor → {step['tool']}"
            f"{RESET}"
            f"{DIM}({short(step['args'], 100)})"
            f"{RESET}"
        )

    elif agent == "supervisor":

        result = step.get("result", {})

        if isinstance(result, dict):
            answer = (
                result.get("answer")
                or result.get("error")
                or result
            )
        else:
            answer = result

        colour = DIM if step.get("ok", True) else RED

        print(
            f"  {colour}"
            f"           ← {short(answer, 110)}"
            f"{RESET}"
        )

    else:

        colour = MAGENTA if step.get("ok", True) else RED

        print(
            f"      {colour}"
            f"{agent} → {step['tool']}"
            f"{RESET}"
            f"{DIM}({short(step.get('args', {}), 80)})"
            f"{RESET}"
        )

        note = (
            f"  {GREEN}"
            f"[replayed: stored result, nothing done again]"
            f"{RESET}"
            if step.get("replayed")
            else ""
        )

        print(
            f"      {DIM}"
            f"{' ' * len(agent)} ← "
            f"{short(step.get('result'), 100)}"
            f"{RESET}"
            f"{note}"
        )