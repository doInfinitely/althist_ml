"""Data models for the althist ideation pipeline.

The on-disk corpus format is one JSON file per depth-0 paper under
``data/papers/<paper_id>.json``, validated by :class:`PaperRecord`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Idea(BaseModel):
    """A research idea in the paper's structured form."""

    motivation: str
    method: str


class SourceRecord(BaseModel):
    """One prior work in a depth-0 paper's source set."""

    source_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    full_text: str | None = None
    relevance: str | None = None  # one sentence: how it informed the depth-0 paper


class PaperRecord(BaseModel):
    """A depth-0 paper (the human ground-truth idea) plus its source set."""

    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    full_text: str | None = None
    idea: Idea | None = None  # extracted human idea (motivation/method)
    sources: list[SourceRecord] = Field(default_factory=list)
    # Present only on synthetic remix pools (data/pools): mode, member paper
    # ids, hidden target identity. Never surfaced to the ideating model.
    remix: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _unique_source_ids(self) -> "PaperRecord":
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate source_id in paper {self.paper_id}")
        return self


class Condition(BaseModel):
    """A fanout steering condition for one generation.

    ``pattern`` / ``paradigm`` are taxonomy keys or ``None`` for the
    unsteered (blank-guidance) arm.
    """

    pattern: str | None = None
    paradigm: str | None = None

    @property
    def key(self) -> str:
        return f"{self.pattern or 'blank'}__{self.paradigm or 'blank'}"


class TranscriptEvent(BaseModel):
    """One event in a run transcript (JSONL line).

    ``payload`` holds raw API message dicts verbatim so that transcripts can
    later be replayed for teacher forcing / RLVR without lossy re-encoding.
    """

    seq: int
    kind: Literal[
        "run_meta",
        "request",
        "assistant_message",
        "tool_call",
        "tool_result",
        "final_idea",
        "error",
        "usage",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None


class RunResult(BaseModel):
    """Outcome of one ideation episode."""

    run_id: str
    paper_id: str
    condition: Condition
    model: str
    provider: str
    idea: Idea | None = None
    n_turns: int = 0
    n_tool_calls: int = 0
    transcript_path: str | None = None
    error: str | None = None


class Diagnostics(BaseModel):
    surface_stitching: bool = False
    surface_stitching_score: int = Field(0, ge=0, le=3)
    bottleneck_specificity: int = Field(0, ge=0, le=3)
    boilerplate_score: int = Field(0, ge=0, le=3)


class AxisLabels(BaseModel):
    primary: str
    secondary: str | None = None


class Annotation(BaseModel):
    """Automated research-taste annotation for one idea (paper Fig. 7)."""

    opportunity_pattern: AxisLabels
    method_paradigm: AxisLabels
    confidence_opportunity: float = Field(ge=0.0, le=1.0)
    confidence_paradigm: float = Field(ge=0.0, le=1.0)
    diagnostics: Diagnostics
    rationale: str = ""


class AnnotatedIdea(BaseModel):
    """An idea joined with its provenance and annotation, for analysis."""

    paper_id: str
    source: str  # "human" or a model/run identifier
    condition: Condition | None = None
    idea: Idea
    annotation: Annotation | None = None
    archetype: str | None = None
