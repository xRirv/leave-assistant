"""Supervisor and leave-request specialist agents."""

from collections.abc import Callable

from app.memory import RunStore
from app.tools.leave_tools import (
    LeaveInfoTools,
    LeaveOperationsTools,
)
from app.tools.dispatch import dispatch


SUPERVISOR_SYSTEM = """
You are the Leave Request Supervisor for student {student_id}.

Your job is to understand the student's request and delegate it
to the appropriate specialist.

You have two specialists:

1. ask_leave_info
   - Use for read-only questions such as:
     - leave balance
     - holiday calendar
     - existing leave information
   - This specialist cannot modify data.

2. ask_leave_operations
   - Use for actions such as:
     - applying for leave
     - withdrawing leave
     - notifying the HOD
   - This specialist can modify leave data.

Do not perform database operations yourself.
Always delegate the request to the appropriate specialist.

After the specialist responds, give the student a concise answer.
"""


LEAVE_INFO_SYSTEM = """
You are the Leave Information Specialist for student {student_id}.

You are READ-ONLY.

You may answer questions about:
- leave balances
- holiday information

Do not modify leave requests, balances, or notifications.

Use the available tools to obtain the required information.
"""


LEAVE_OPERATIONS_SYSTEM = """
You are the Leave Operations Specialist for student {student_id}.

You handle leave-related actions for this student.

You may:
- apply leave
- withdraw leave
- notify the HOD

Use the available tools to perform the requested operation.

The business rules are enforced by the database tools.
Do not bypass those rules.
"""


def run_tool(
    toolset,
    db,
    key: str,
    name: str,
    args: dict,
):
    """Run a tool, specialist delegation, or idempotent side effect."""

    # ---------------------------------------------------------
    # Delegation
    # ---------------------------------------------------------

    if name in toolset.DELEGATES:
        return toolset.call_delegate(
            key,
            name,
            args,
        )

    # ---------------------------------------------------------
    # Side effects
    # ---------------------------------------------------------

    if name in toolset.SIDE_EFFECTS:

        return toolset.call_idempotent(
            key,
            name,
            args,
        )

    # ---------------------------------------------------------
    # Read-only tool
    # ---------------------------------------------------------

    return toolset.call(
        name,
        args,
    ), False


def run_specialist(
    agent_name: str,
    system: str,
    toolset,
    db,
    provider,
    task: str,
    parent_key: str,
    on_step: Callable[[dict], None] | None = None,
    max_steps: int = 8,
):
    """Run a specialist until it returns an answer."""

    contents = [
        {
            "role": "user",
            "text": task,
        }
    ]

    functions = list(
        toolset.functions().values()
    )

    steps = 0
    tool_seq = 0
    tools_used: list[str] = []

    while steps < max_steps:

        steps += 1

        turn = provider.generate(
            system,
            contents,
            functions,
        )

        if not turn.tool_calls:

            return {
                "agent": agent_name,
                "answer": turn.text or "",
                "tools_used": tools_used,
            }

        contents.append({
            "role": "model",
            "text": turn.text,
            "tool_calls": [
                {
                    "name": call.name,
                    "args": call.args,
                }
                for call in turn.tool_calls
            ],
        })

        for call in turn.tool_calls:

            tool_seq += 1

            child_key = (
                f"{parent_key}:"
                f"{agent_name}:"
                f"{tool_seq}"
            )

            result, replayed = run_tool(
                toolset,
                db,
                child_key,
                call.name,
                call.args,
            )

            tools_used.append(
                call.name
            )

            contents.append({
                "role": "tool",
                "name": call.name,
                "result": result,
            })

            if on_step:

                on_step({
                    "agent": agent_name,
                    "kind": "tool",
                    "tool": call.name,
                    "args": call.args,
                    "result": result,
                    "ok": "error" not in result,
                    "replayed": replayed,
                })

        # Let the specialist model see the tool result
        # and produce its final response.
        if steps >= max_steps:
            break

    return {
        "agent": agent_name,
        "error": "specialist_step_limit",
    }


class SupervisorTools:
    """Tools exposed to the supervisor.

    The supervisor itself has no direct domain write tools.
    It can only delegate to the two specialists.
    """

    TOOL_NAMES = (
        "ask_leave_info",
        "ask_leave_operations",
    )

    DELEGATES = set(TOOL_NAMES)

    SIDE_EFFECTS = set()

    def __init__(
        self,
        db,
        providers,
        student_id,
        store: RunStore,
        on_step=None,
    ):
        self.db = db
        self.providers = providers
        self.student_id = int(student_id)
        self.store = store
        self.on_step = on_step

    def functions(self) -> dict:
        return {
            "ask_leave_info": self.ask_leave_info,
            "ask_leave_operations": self.ask_leave_operations,
        }

    def call(
        self,
        name: str,
        args: dict,
    ):
        return dispatch(
            self.functions(),
            name,
            args,
        )

    def call_idempotent(
        self,
        key: str,
        name: str,
        args: dict,
    ):
        return self.store.run_idempotent(
            key,
            name,
            lambda: self.call(
                name,
                args,
            ),
        )

    def call_delegate(
        self,
        key: str,
        name: str,
        args: dict,
    ):
        if name not in self.DELEGATES:
            return {
                "error": "unknown_delegate"
            }, False

        try:
            if name == "ask_leave_info":
                if not isinstance(args.get("question"), str) or not args["question"].strip():
                    return {"error": "invalid_arguments"}, False
            elif name == "ask_leave_operations":
                if not isinstance(args.get("request"), str) or not args["request"].strip():
                    return {"error": "invalid_arguments"}, False

            if name == "ask_leave_info":

                specialist = LeaveInfoTools(
                    self.db,
                    self.student_id,
                    self.store,
                )

                system = LEAVE_INFO_SYSTEM.format(
                    student_id=self.student_id
                )

                provider = self.providers[
                    "leave_info"
                ]

                result = run_specialist(
                    "leave_info",
                    system,
                    specialist,
                    self.db,
                    provider,
                    args["question"],
                    key,
                    self.on_step,
                )

            else:

                specialist = LeaveOperationsTools(
                    self.db,
                    self.student_id,
                    self.store,
                )

                system = LEAVE_OPERATIONS_SYSTEM.format(
                    student_id=self.student_id
                )

                provider = self.providers[
                    "leave_operations"
                ]

                result = run_specialist(
                    "leave_operations",
                    system,
                    specialist,
                    self.db,
                    provider,
                    args["request"],
                    key,
                    self.on_step,
                )

            if self.on_step:
                self.on_step({
                    "agent": "supervisor",
                    "kind": "delegate",
                    "tool": name,
                    "args": args,
                    "result": result,
                    "ok": "error" not in result,
                })

            return result, False

        except (KeyError, TypeError):

            return {
                "error": "invalid_arguments"
            }, False

    def ask_leave_info(
        self,
        question: str,
    ):
        """Delegate a read-only leave information question.

        Use this only for questions about leave balances,
        holidays, or other information that does not modify
        student leave data. The specialist is read-only and
        cannot create, withdraw, or modify leave requests.
        """

        return {
            "question": question
        }

    def ask_leave_operations(
        self,
        request: str,
    ):
        """Delegate a leave operation request.

        Use this when the student wants to apply for leave,
        withdraw a leave request, or perform another operation
        that changes domain data. The operations specialist
        enforces the business rules through the leave database.
        """

        return {
            "request": request
        }