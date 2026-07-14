"""Distributional analysis: human vs. model label distributions.

The unit of comparison is a *source*: ``"human"`` or a model identifier
(optionally split per fanout condition). The fanout question — does steered
generation broaden the distribution toward human taste? — is answered by
comparing (a) the blank-condition distribution, (b) the pooled all-conditions
distribution, and (c) per-condition distributions, each against the human
reference.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .metrics.distributional import distribution, jsd, normalized_entropy, tvd
from .schema import AnnotatedIdea
from .taxonomy import METHOD_PARADIGMS, OPPORTUNITY_PATTERNS

AXES = {
    "opportunity_pattern": list(OPPORTUNITY_PATTERNS),
    "method_paradigm": list(METHOD_PARADIGMS),
}


@dataclass
class AxisComparison:
    source: str
    axis: str
    n: int
    tvd: float | None  # None for the human reference row
    jsd: float | None
    entropy: float
    dist: dict[str, float]


def _primary(item: AnnotatedIdea, axis: str) -> str:
    assert item.annotation is not None
    return getattr(item.annotation, axis).primary


def group_key(item: AnnotatedIdea, split_conditions: bool) -> str:
    if item.source == "human":
        return "human"
    if split_conditions and item.condition is not None:
        return f"{item.source}[{item.condition.key}]"
    return item.source


def compare_distributions(
    items: list[AnnotatedIdea], split_conditions: bool = False
) -> list[AxisComparison]:
    annotated = [i for i in items if i.annotation is not None]
    groups: dict[str, list[AnnotatedIdea]] = defaultdict(list)
    for item in annotated:
        groups[group_key(item, split_conditions)].append(item)
    if "human" not in groups:
        raise ValueError("no annotated human ideas — the human reference is required")

    rows: list[AxisComparison] = []
    for axis, label_set in AXES.items():
        human_dist = distribution((_primary(i, axis) for i in groups["human"]), label_set)
        for source in sorted(groups, key=lambda s: (s != "human", s)):
            dist = distribution((_primary(i, axis) for i in groups[source]), label_set)
            is_ref = source == "human"
            rows.append(
                AxisComparison(
                    source=source,
                    axis=axis,
                    n=len(groups[source]),
                    tvd=None if is_ref else tvd(dist, human_dist),
                    jsd=None if is_ref else jsd(dist, human_dist),
                    entropy=normalized_entropy(dist),
                    dist=dist,
                )
            )
    return rows


def format_report(rows: list[AxisComparison]) -> str:
    lines = []
    for axis in AXES:
        lines.append(f"\n== {axis} ==")
        lines.append(f"{'source':<44} {'n':>5} {'TVD':>6} {'JSD':>6} {'Ent.':>6}")
        for r in rows:
            if r.axis != axis:
                continue
            tvd_s = "  --  " if r.tvd is None else f"{r.tvd:6.3f}"
            jsd_s = "  --  " if r.jsd is None else f"{r.jsd:6.3f}"
            lines.append(f"{r.source:<44} {r.n:>5} {tvd_s} {jsd_s} {r.entropy:6.3f}")
    return "\n".join(lines)
