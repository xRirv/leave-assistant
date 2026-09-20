"""Model providers. The agent only knows `generate`."""

from dataclasses import dataclass, field
from typing import Any


class AgentError(Exception):
    """A run could not finish."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
    ):
        super().__init__(message)

        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(
        default_factory=list
    )
    tokens_in: int = 0
    tokens_out: int = 0
    raw: Any = None


class GeminiProvider:

    def __init__(self, model: str):

        from google import genai

        self.client = genai.Client()
        self.model = model

    def _to_gemini(
        self,
        contents: list[dict],
    ):

        from google.genai import types

        out = []

        for c in contents:

            if c["role"] == "user":

                out.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=c["text"]
                            )
                        ],
                    )
                )

            elif c["role"] == "model":

                if c.get("raw") is not None:
                    out.append(c["raw"])
                    continue

                parts = []

                if c.get("text"):
                    parts.append(
                        types.Part.from_text(
                            text=c["text"]
                        )
                    )

                parts += [
                    types.Part.from_function_call(
                        name=t["name"],
                        args=t["args"],
                    )
                    for t in c.get(
                        "tool_calls",
                        [],
                    )
                ]

                out.append(
                    types.Content(
                        role="model",
                        parts=parts,
                    )
                )

            elif c["role"] == "tool":

                part = types.Part.from_function_response(
                    name=c["name"],
                    response=c["result"],
                )

                if (
                    out
                    and out[-1].role == "user"
                    and all(
                        p.function_response
                        for p in out[-1].parts
                    )
                ):
                    out[-1].parts.append(part)

                else:
                    out.append(
                        types.Content(
                            role="user",
                            parts=[part],
                        )
                    )

        return out

    def generate(
        self,
        system: str,
        contents: list[dict],
        tools: list,
    ) -> ModelTurn:

        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=tools,
            temperature=0,
            automatic_function_calling=(
                types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            ),
        )

        try:

            resp = self.client.models.generate_content(
                model=self.model,
                contents=self._to_gemini(contents),
                config=config,
            )

        except errors.APIError as e:

            if e.code == 429:

                raise AgentError(
                    "provider_rate_limited",
                    "Model quota exhausted. Wait a minute.",
                    True,
                ) from e

            if e.code and e.code >= 500:

                raise AgentError(
                    "provider_unavailable",
                    "Model provider failed.",
                    True,
                ) from e

            raise AgentError(
                "provider_error",
                str(e),
                False,
            ) from e

        content = (
            resp.candidates[0].content
            if resp.candidates
            else None
        )

        parts = (
            content.parts or []
            if content
            else []
        )

        text = (
            "".join(
                p.text
                for p in parts
                if p.text and not p.thought
            )
            or None
        )

        calls = [
            ToolCall(
                fc.name,
                dict(fc.args or {}),
            )
            for fc in (
                resp.function_calls or []
            )
        ]

        usage = resp.usage_metadata

        return ModelTurn(
            text=text,
            tool_calls=calls,
            tokens_in=(
                usage.prompt_token_count or 0
                if usage
                else 0
            ),
            tokens_out=(
                usage.candidates_token_count or 0
                if usage
                else 0
            ),
            raw=content,
        )


class ScriptedProvider:
    """Fixed scripted provider used by tests."""

    model = "mock"

    def __init__(
        self,
        script: list,
        loop: bool = False,
    ):

        self.original = list(script)
        self.script = list(script)
        self.loop = loop
        self.calls: list[list[dict]] = []

    def generate(
        self,
        system: str,
        contents: list[dict],
        tools: list,
    ) -> ModelTurn:

        self.calls.append(
            [dict(c) for c in contents]
        )

        if not self.script and self.loop:
            self.script = list(
                self.original
            )

        if not self.script:

            return ModelTurn(
                text="(mock) script exhausted"
            )

        step = self.script.pop(0)

        if isinstance(step, Exception):
            raise step

        return step


class PositionalMock:
    """Scripted model that resumes based on conversation position."""

    model = "mock"

    def __init__(
        self,
        turns: list[ModelTurn],
        slow: float = 0.0,
    ):

        self.turns = turns
        self.slow = slow
        self.calls: list[list[dict]] = []

    def generate(
        self,
        system: str,
        contents: list[dict],
        tools: list,
    ) -> ModelTurn:

        import time

        self.calls.append(
            [dict(c) for c in contents]
        )

        last_user = max(
            i
            for i, c in enumerate(contents)
            if c["role"] == "user"
        )

        position = sum(
            1
            for c in contents[last_user:]
            if c["role"] == "model"
        )

        if self.slow:
            time.sleep(self.slow)

        if position >= len(self.turns):

            return ModelTurn(
                text="(mock) nothing more to do."
            )

        return self.turns[position]


class RoutedMock:
    """Scripted model that selects a conversation by phrase."""

    model = "mock"

    def __init__(
        self,
        routes: dict[str, list[ModelTurn]],
        slow: float = 0.0,
    ):

        self.routes = routes
        self.slow = slow
        self.calls: list[list[dict]] = []

    def generate(
        self,
        system: str,
        contents: list[dict],
        tools: list,
    ) -> ModelTurn:

        import time

        self.calls.append(
            [dict(c) for c in contents]
        )

        last_user = max(
            i
            for i, c in enumerate(contents)
            if c["role"] == "user"
        )

        request = contents[last_user]["text"]

        position = sum(
            1
            for c in contents[last_user:]
            if c["role"] == "model"
        )

        if self.slow:
            time.sleep(self.slow)

        for phrase, turns in self.routes.items():

            if phrase.lower() in request.lower():

                if position < len(turns):
                    return turns[position]

                return ModelTurn(
                    text="(mock) done."
                )

        return ModelTurn(
            text="(mock) I have no script for that request."
        )


def _call(
    name: str,
    **args,
) -> ModelTurn:

    return ModelTurn(
        text=None,
        tool_calls=[
            ToolCall(
                name,
                args,
            )
        ],
        tokens_in=100,
        tokens_out=10,
    )


def demo_providers(
    slow: float = 0.0,
) -> dict:
    """Scripted providers for the leave-request demo."""

    return {

        # ==============================================================
        # SUPERVISOR
        # ==============================================================

        "supervisor": RoutedMock({

            "leave balance": [

                _call(
                    "ask_leave_info",
                    question=(
                        "Check the leave balance for "
                        "student 1."
                    ),
                ),

                ModelTurn(
                    text=(
                        "(mock) You have 10 CASUAL leaves "
                        "remaining and 9 SICK leaves remaining."
                    )
                ),
            ],

            "apply": [

                _call(
                    "ask_leave_operations",
                    request=(
                        "Apply 2 CASUAL leave days for "
                        "student 1 from 2026-09-21 to "
                        "2026-09-22. Reason: personal work."
                    ),
                ),

                ModelTurn(
                    text=(
                        "(mock) Your 2-day CASUAL leave "
                        "request has been submitted successfully."
                    )
                ),
            ],

            "withdraw leave": [

                _call(
                    "ask_leave_operations",
                    request=(
                        "Withdraw leave request 1 "
                        "for student 1."
                    ),
                ),

                ModelTurn(
                    text=(
                        "(mock) Your leave request has "
                        "been withdrawn successfully."
                    )
                ),
            ],

        }, slow),

        # ==============================================================
        # READ-ONLY LEAVE INFORMATION SPECIALIST
        # ==============================================================

        "leave_info": RoutedMock({

            "leave balance": [

                # student_id is already bound to the specialist.
                _call(
                    "get_leave_balance",
                ),

                ModelTurn(
                    text=(
                        "(mock) Student 1 has 10 CASUAL "
                        "leaves remaining."
                    )
                ),
            ],

        }, slow),

        # ==============================================================
        # WRITE-CAPABLE LEAVE OPERATIONS SPECIALIST
        # ==============================================================

        "leave_operations": RoutedMock({

            "Apply 2 CASUAL": [

                _call(
                    "apply_leave",
                    leave_type="CASUAL",
                    start_date="2026-09-21",
                    end_date="2026-09-22",
                    days=2,
                    reason="personal work",
                ),

                _call(
                    "notify_hod",
                    leave_id=1,
                    message=(
                        "Student 1 has submitted a "
                        "2-day CASUAL leave request."
                    ),
                ),

                ModelTurn(
                    text=(
                        "(mock) Leave request created "
                        "and HOD notification sent."
                    )
                ),
            ],

            "Withdraw leave": [

                _call(
                    "withdraw_leave",
                    leave_id=1,
                ),

                ModelTurn(
                    text=(
                        "(mock) Leave request withdrawn "
                        "and the leave balance restored."
                    )
                ),
            ],

        }, slow),
    }