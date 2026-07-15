"""LLM provider abstraction.

Two providers share one interface:

- :class:`AnthropicProvider` — Claude via the official ``anthropic`` SDK.
- :class:`OpenAICompatProvider` — any OpenAI-compatible endpoint (vLLM,
  sglang, ...) for open-weight models; required for the later teacher-forcing
  / RLVR finetuning stage, where we must run models whose weights we hold.

Each provider owns its native message format. The ideation loop only sees
:class:`StepResult` and provider-opaque conversation state, and every raw
request/response dict is surfaced for verbatim transcript logging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
# max_tokens is a required API parameter. It must be generous here: a single
# agentic turn spends output on adaptive thinking AND a full submit_idea tool
# call, so a low ceiling truncates the idea mid-tool-input. Above ~16k the SDK
# requires streaming to avoid HTTP timeouts (see step()).
DEFAULT_MAX_TOKENS = 32000


class LLMResponseError(RuntimeError):
    """A response arrived but lacked the expected content (empty, refusal, ...)."""


@dataclass
class ToolCall:
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass
class StepResult:
    raw: dict[str, Any]  # provider-native response, verbatim, for the transcript
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str

    def start_conversation(self, system: str, user: str) -> Any: ...

    def step(self, state: Any, tools: list[dict]) -> StepResult: ...

    def append_tool_results(
        self, state: Any, results: list[tuple[str, str, bool]]
    ) -> None: ...

    def structured_json(self, system: str, user: str, schema: dict) -> dict: ...

    def simple_text(self, system: str, user: str, max_tokens: int = 1024) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS):
        import anthropic

        # Generous retry budget: org-level 429s (shared output-TPM pool with
        # other boxes) can persist for minutes; the SDK honors Retry-After
        # with exponential backoff, which beats crash-and-restart loops.
        self.client = anthropic.Anthropic(max_retries=10)
        self.model = model
        self.max_tokens = max_tokens

    def start_conversation(self, system: str, user: str) -> dict:
        return {"system": system, "messages": [{"role": "user", "content": user}]}

    def step(self, state: dict, tools: list[dict]) -> StepResult:
        # Stream: max_tokens is large, and non-streaming requests that big risk
        # the SDK's ~10-min HTTP timeout on long agentic turns.
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=state["system"],
            thinking={"type": "adaptive"},
            tools=tools,
            messages=state["messages"],
        ) as stream:
            response = stream.get_final_message()
        # Echo the full content (thinking blocks included, unchanged) back into
        # history — required for multi-turn thinking on Claude.
        state["messages"].append({"role": "assistant", "content": response.content})
        text = "".join(b.text for b in response.content if b.type == "text")
        tool_calls = [
            ToolCall(call_id=b.id, name=b.name, args=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        return StepResult(
            raw=response.model_dump(),
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            usage=response.usage.model_dump(),
        )

    def append_tool_results(self, state: dict, results: list[tuple[str, str, bool]]) -> None:
        # All results for a turn go back in a single user message.
        state["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": content,
                        "is_error": is_error,
                    }
                    for call_id, content, is_error in results
                ],
            }
        )

    def structured_json(self, system: str, user: str, schema: dict) -> dict:
        # Stream for the same reason as step(): with a generous max_tokens the
        # SDK rejects non-streaming calls that could outlive its ~10-min HTTP
        # timeout.
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            response = stream.get_final_message()
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMResponseError(
                f"no text block in structured response (stop_reason={response.stop_reason})"
            )
        return json.loads(text)

    def simple_text(self, system: str, user: str, max_tokens: int = 1024) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMResponseError(
                f"empty text response (stop_reason={response.stop_reason})"
            )
        return text


class OpenAICompatProvider:
    """Open-weight models behind an OpenAI-compatible server (e.g. vLLM).

    Not used for Claude — Claude always goes through :class:`AnthropicProvider`.
    """

    name = "openai_compat"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        max_tokens: int = 4096,
        temperature: float = 0.6,
        top_p: float = 0.95,
    ):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def start_conversation(self, system: str, user: str) -> dict:
        return {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        }

    def step(self, state: dict, tools: list[dict]) -> StepResult:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            tools=self._convert_tools(tools),
            messages=state["messages"],
        )
        message = response.choices[0].message
        state["messages"].append(message.model_dump(exclude_none=True))
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(call_id=tc.id, name=tc.function.name, args=args))
        return StepResult(
            raw=response.model_dump(),
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason=response.choices[0].finish_reason,
            usage=response.usage.model_dump() if response.usage else {},
        )

    def append_tool_results(self, state: dict, results: list[tuple[str, str, bool]]) -> None:
        for call_id, content, _is_error in results:
            state["messages"].append(
                {"role": "tool", "tool_call_id": call_id, "content": content}
            )

    def structured_json(self, system: str, user: str, schema: dict) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema, "strict": True},
            },
        )
        return json.loads(response.choices[0].message.content)

    def simple_text(self, system: str, user: str, max_tokens: int = 1024) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()


def make_provider(spec: str) -> Provider:
    """Build a provider from a CLI spec.

    ``anthropic`` | ``anthropic:<model>`` | ``openai:<model>@<base_url>``
    """
    kind, _, rest = spec.partition(":")
    if kind == "anthropic":
        return AnthropicProvider(model=rest or DEFAULT_ANTHROPIC_MODEL)
    if kind == "openai":
        model, _, base_url = rest.partition("@")
        if not model:
            raise ValueError("openai spec requires a model: openai:<model>[@<base_url>]")
        kwargs = {"base_url": base_url} if base_url else {}
        return OpenAICompatProvider(model=model, **kwargs)
    raise ValueError(f"unknown provider spec {spec!r}")
