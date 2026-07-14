"""The agentic ideation loop with full-fidelity transcript logging.

One *run* = one seed paper x one fanout condition x one provider/model.
Every provider-native response, tool call, and tool result is written to a
JSONL transcript so runs can later be replayed for teacher forcing
(activation extraction) and used as RLVR trajectories.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .corpus import SourceSet
from .llm import Provider
from .prompts import generation_system, generation_user
from .schema import Condition, PaperRecord, RunResult, TranscriptEvent
from .tools import TOOL_DEFINITIONS, ToolDispatcher

# High safety ceiling only — not a budget. The model is expected to finish well
# under this; it exists to stop a runaway loop, not to constrain exploration.
MAX_TURNS = 40
MAX_NUDGES = 2

NUDGE_MESSAGE = (
    "You have not submitted an idea yet. Call the submit_idea tool now with "
    "your final motivation and method."
)


class TranscriptWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._fh = open(path, "w")

    def write(self, kind: str, payload: dict) -> None:
        event = TranscriptEvent(
            seq=self._seq,
            kind=kind,  # type: ignore[arg-type]
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()
        self._seq += 1

    def close(self) -> None:
        self._fh.close()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)


def run_ideation(
    paper: PaperRecord,
    condition: Condition,
    provider: Provider,
    runs_dir: str | Path = "data/runs",
    max_turns: int = MAX_TURNS,
) -> RunResult:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{paper.paper_id}__{condition.key}__{_slug(provider.model)}__{stamp}"
    transcript_path = Path(runs_dir) / paper.paper_id / f"{run_id}.jsonl"
    writer = TranscriptWriter(transcript_path)

    system = generation_system(condition)
    user = generation_user(paper)
    writer.write(
        "run_meta",
        {
            "run_id": run_id,
            "paper_id": paper.paper_id,
            "condition": condition.model_dump(),
            "provider": provider.name,
            "model": provider.model,
            "system": system,
            "user": user,
            "tools": TOOL_DEFINITIONS,
            "n_sources": len(paper.sources),
        },
    )

    dispatcher = ToolDispatcher(SourceSet(paper))
    state = provider.start_conversation(system, user)
    n_turns = 0
    n_tool_calls = 0
    nudges = 0
    error: str | None = None

    try:
        while dispatcher.submitted is None and n_turns < max_turns:
            result = provider.step(state, TOOL_DEFINITIONS)
            n_turns += 1
            writer.write(
                "assistant_message",
                {"turn": n_turns, "raw": result.raw, "stop_reason": result.stop_reason},
            )
            writer.write("usage", {"turn": n_turns, "usage": result.usage})

            if result.tool_calls:
                tool_results = []
                for call in result.tool_calls:
                    writer.write(
                        "tool_call",
                        {"turn": n_turns, "id": call.call_id, "name": call.name, "args": call.args},
                    )
                    content, is_error = dispatcher.dispatch(call.name, call.args)
                    n_tool_calls += 1
                    writer.write(
                        "tool_result",
                        {"turn": n_turns, "id": call.call_id, "is_error": is_error, "content": content},
                    )
                    tool_results.append((call.call_id, content, is_error))
                provider.append_tool_results(state, tool_results)
            elif dispatcher.submitted is None:
                # Model stopped talking without calling submit_idea.
                if nudges >= MAX_NUDGES:
                    error = "model ended turn without submitting an idea"
                    break
                nudges += 1
                state["messages"].append({"role": "user", "content": NUDGE_MESSAGE})
        if dispatcher.submitted is None and error is None:
            error = f"no idea submitted within {max_turns} turns"
    except Exception as exc:  # noqa: BLE001 - persist failure into the transcript
        error = f"{type(exc).__name__}: {exc}"

    if dispatcher.submitted is not None:
        writer.write("final_idea", dispatcher.submitted.model_dump())
    if error is not None:
        writer.write("error", {"message": error})
    writer.close()

    return RunResult(
        run_id=run_id,
        paper_id=paper.paper_id,
        condition=condition,
        model=provider.model,
        provider=provider.name,
        idea=dispatcher.submitted,
        n_turns=n_turns,
        n_tool_calls=n_tool_calls,
        transcript_path=str(transcript_path),
        error=error,
    )


def load_run_results(runs_dir: str | Path = "data/runs") -> list[RunResult]:
    """Reconstruct RunResults from transcripts on disk."""
    results = []
    for path in sorted(Path(runs_dir).glob("*/*.jsonl")):
        meta: dict = {}
        idea = None
        err = None
        n_turns = 0
        n_tool_calls = 0
        with open(path) as f:
            for line in f:
                event = json.loads(line)
                if event["kind"] == "run_meta":
                    meta = event["payload"]
                elif event["kind"] == "final_idea":
                    idea = event["payload"]
                elif event["kind"] == "error":
                    err = event["payload"].get("message")
                elif event["kind"] == "assistant_message":
                    n_turns += 1
                elif event["kind"] == "tool_call":
                    n_tool_calls += 1
        if not meta:
            continue
        results.append(
            RunResult(
                run_id=meta["run_id"],
                paper_id=meta["paper_id"],
                condition=Condition.model_validate(meta["condition"]),
                model=meta["model"],
                provider=meta["provider"],
                idea=idea,
                n_turns=n_turns,
                n_tool_calls=n_tool_calls,
                transcript_path=str(path),
                error=err,
            )
        )
    return results
