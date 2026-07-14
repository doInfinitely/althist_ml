"""Automated research-taste annotation (paper Sec. 3.4 / Fig. 7)."""

from __future__ import annotations

from .llm import Provider
from .prompts import ANNOTATION_SCHEMA, ANNOTATION_SYSTEM, annotation_user
from .schema import Annotation, AxisLabels, Diagnostics, Idea


def _axis(raw: dict) -> AxisLabels:
    secondary = raw.get("secondary")
    if secondary == "none":
        secondary = None
    return AxisLabels(primary=raw["primary"], secondary=secondary)


def annotate_idea(
    provider: Provider,
    paper_id: str,
    source_titles: list[str],
    idea: Idea,
) -> Annotation:
    raw = provider.structured_json(
        system=ANNOTATION_SYSTEM,
        user=annotation_user(paper_id, source_titles, idea),
        schema=ANNOTATION_SCHEMA,
    )
    return Annotation(
        opportunity_pattern=_axis(raw["labels"]["opportunity_pattern"]),
        method_paradigm=_axis(raw["labels"]["method_paradigm"]),
        confidence_opportunity=float(raw["confidence"]["opportunity_pattern"]),
        confidence_paradigm=float(raw["confidence"]["method_paradigm"]),
        diagnostics=Diagnostics.model_validate(raw["diagnostics"]),
        rationale=raw.get("rationale", ""),
    )
