"""Tool definitions and dispatch for the ideating LLM.

Tools are defined once as provider-neutral JSON schemas (Anthropic shape;
the OpenAI-compatible provider converts them). The episode terminates when
the model calls ``submit_idea``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .corpus import MAX_SPAN_CHARS, SourceSet
from .schema import Idea

# Streaming tool-call parsing can leak the call syntax's own closing tags into
# a parameter value (and swallow the fields after it). Observed live: a long
# motivation arriving with a trailing "</parameter>\n</invoke>\n" and no method.
_TAG_DEBRIS = re.compile(r"</?(?:antml:)?(?:parameter|invoke|function_calls|motivation|method)\b[^>]*>")
# Real submissions are paragraphs; anything this short is a probe or stub.
MIN_SUBMISSION_CHARS = 150
_PLACEHOLDER = re.compile(r"^\s*(test|placeholder|junk|dummy|todo|tbd)\b", re.IGNORECASE)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_sources",
        "description": (
            "List the prior-work sources available for this ideation task: id, title, "
            "authors, year, and whether full text is available. Call this first."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_abstract",
        "description": "Get the abstract of one source by its source_id.",
        "input_schema": {
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_span",
        "description": (
            "Read a span of a source's full text by character offset. Use this to go "
            f"deeper than the abstract. At most {MAX_SPAN_CHARS} characters per call; "
            "the result reports total_chars so you can page through."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "start": {"type": "integer", "description": "0-based character offset"},
                "length": {"type": "integer", "description": f"characters to read (max {MAX_SPAN_CHARS})"},
            },
            "required": ["source_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_sources",
        "description": (
            "Case-insensitive literal search across all sources (or one source if "
            "source_id is given). Returns snippets with character offsets usable "
            "with read_span."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source_id": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_idea",
        "description": (
            "Submit your final research idea. Call exactly once, when you are done "
            "reading. The motivation states the research gap and why it matters; the "
            "method describes a concrete, feasible high-level approach that addresses it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivation": {"type": "string"},
                "method": {"type": "string"},
            },
            "required": ["motivation", "method"],
            "additionalProperties": False,
        },
    },
]


class ToolDispatcher:
    """Executes tool calls against a SourceSet; captures submit_idea."""

    def __init__(self, source_set: SourceSet):
        self.source_set = source_set
        self.submitted: Idea | None = None

    @staticmethod
    def _clean(value: Any) -> str:
        return _TAG_DEBRIS.sub("", str(value or "")).strip()

    def _validate_submission(self, args: dict[str, Any]) -> str | None:
        """None if acceptable, else a precise error message for the model.

        The model cannot see what the API's tool-call parser delivered, so
        every rejection echoes the received per-field lengths: without that,
        a parser-truncated call reads to the model like a broken tool, and it
        starts probing with throwaway values (observed live: 12 one-field
        calls, then an accepted 'Test motivation.' stub ended the episode).
        """
        fields = {f: self._clean(args.get(f)) for f in ("motivation", "method")}
        received = ", ".join(
            f"{name}={len(v)} chars" if v else f"{name}=MISSING"
            for name, v in fields.items()
        )
        if not all(fields.values()):
            return (
                f"submit_idea received: {received}. A missing field usually means "
                "the call's parameter syntax was truncated in transit, not that "
                "the tool is broken — do not probe with test values. Resend ONE "
                "call carrying BOTH fields as plain text."
            )
        for name, value in fields.items():
            if len(value) < MIN_SUBMISSION_CHARS or _PLACEHOLDER.match(value):
                return (
                    f"submit_idea received: {received}, but {name} reads as a "
                    f"placeholder or is too short (minimum {MIN_SUBMISSION_CHARS} "
                    "chars). Submission is final and ends the episode — send your "
                    "complete idea, a full paragraph per field, in one call."
                )
        return None

    def dispatch(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run one tool call. Returns (result_json, is_error)."""
        try:
            if name == "list_sources":
                result: Any = self.source_set.list_sources()
            elif name == "get_abstract":
                result = self.source_set.get_abstract(args["source_id"])
            elif name == "read_span":
                result = self.source_set.read_span(
                    args["source_id"],
                    start=int(args.get("start", 0)),
                    length=int(args.get("length", MAX_SPAN_CHARS)),
                )
            elif name == "search_sources":
                result = self.source_set.search(args["query"], args.get("source_id"))
            elif name == "submit_idea":
                error = self._validate_submission(args)
                if error is not None:
                    return json.dumps({"error": error}), True
                if self.submitted is None:  # keep the first valid submission
                    self.submitted = Idea(
                        motivation=self._clean(args["motivation"]),
                        method=self._clean(args["method"]),
                    )
                result = {"status": "accepted"}
            else:
                return json.dumps({"error": f"unknown tool {name!r}"}), True
            return json.dumps(result, ensure_ascii=False), False
        except (KeyError, ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc)}), True
