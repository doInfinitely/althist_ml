"""Score generated ideas for Harbor task-worthiness.

The Harbor pipeline (customers/gdm-ml) turns a *seed* into a calibratable RL
task via the ``expert-ideate`` skill. Not every althist idea makes a good seed.
This module scores each generated idea against the skill's load-bearing
criteria so we can rank and route the strongest ones:

- **recall safety** (the skill's Step-2 gate): a seed whose solution a frontier
  model can *recall* saturates and is worthless. Our contamination signal —
  similarity to the historical paper in excess of mean source similarity — is
  exactly this: high excess = the idea regurgitates the known result = unsafe.
- **source relevance**: the idea should be grounded in its prior-work set
  (mean source similarity), not off-topic.
- **specificity / not-stitching / not-boilerplate**: from the annotator's
  diagnostics — expert-ideate rejects vague A+B stitching and boilerplate;
  it wants a precise bottleneck.
- **shape verifiability**: the idea's method paradigm maps to one of
  expert-ideate's five task shapes; some shapes yield calibratable verifiers
  readily (optimize/infer/repair/systems), while synthesis/unification — the
  paradigm LLMs overproduce — is the hardest to verify.

Each component is in [0, 1] with 1 = better seed. Components whose inputs are
absent (no annotation, no embeddings, no ground truth) are ``None`` and drop
out of the weighted composite, which renormalizes over what's present.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# althist method paradigm -> expert-ideate task shape (skill Step 3).
PARADIGM_TO_SHAPE: dict[str, str] = {
    "optimization_search": "optimize-a-metric",
    "empirical_mapping": "research-infer",
    "formal_derivation": "research-infer",
    "robustification": "repair-debug",
    "artifact_system": "infra-systems",
    "relax_extend_scope": "optimize-a-metric",
    "synthesis_unification": "synthesis",  # not a native verifiable shape
}

# How readily each shape yields a calibratable, un-gameable verifier.
# The skill prefers optimize / infer / repair / systems (continuous metrics,
# long-horizon); precision is usable but narrow; pure synthesis has no native
# verifier modality and is where recall/boilerplate concentrate.
SHAPE_VERIFIABILITY: dict[str, float] = {
    "optimize-a-metric": 1.0,
    "research-infer": 0.9,
    "repair-debug": 0.9,
    "infra-systems": 0.8,
    "precision": 0.55,
    "synthesis": 0.25,
}

# Composite weights. recall-safety and shape are the two gates the skill treats
# as load-bearing; specificity is the next most predictive. Weights are over
# whatever components are present (renormalized), so absent signals don't skew.
WEIGHTS: dict[str, float] = {
    "recall_safety": 0.30,
    "shape_verifiability": 0.25,
    "specificity": 0.20,
    "anti_boilerplate": 0.10,
    "anti_stitching": 0.10,
    "source_relevance": 0.05,
}

# excess GT similarity at/above this reads as full-on regurgitation (recall_safety=0).
EXCESS_RECALL_SCALE = 0.15


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class TaskSeedScore:
    paper_id: str
    source: str
    condition_key: str
    shape: str | None
    components: dict[str, float] = field(default_factory=dict)  # only present signals
    composite: float = 0.0
    missing: list[str] = field(default_factory=list)  # signals that were absent

    def explain(self) -> str:
        parts = [f"{k}={v:.2f}" for k, v in sorted(self.components.items())]
        return f"{self.composite:.3f}  [{self.shape or '?'}]  " + " ".join(parts)


def _recall_safety(
    excess_gt_similarity: float | None,
    max_descendant_excess: float | None = None,
) -> float | None:
    """Recall safety from the worst of two recall routes: reproducing the
    historical paper itself, or reproducing a known *descendant* of it
    (forward extension — memorized future work that raw excess-GT misses).
    The descendant term only exists for corpus-internally-cited papers, so
    it tightens the gate where available and is neutral elsewhere."""
    if excess_gt_similarity is None and max_descendant_excess is None:
        return None
    worst = max(x for x in (excess_gt_similarity, max_descendant_excess)
                if x is not None)
    return _clamp(1.0 - max(0.0, worst) / EXCESS_RECALL_SCALE)


def score_idea(
    paper_id: str,
    source: str,
    condition_key: str,
    paradigm: str | None,
    *,
    excess_gt_similarity: float | None = None,
    max_descendant_excess: float | None = None,
    mean_source_similarity: float | None = None,
    bottleneck_specificity: int | None = None,
    surface_stitching_score: int | None = None,
    boilerplate_score: int | None = None,
) -> TaskSeedScore:
    """Score one idea. Pass whatever signals are available; the rest drop out.

    ``paradigm`` is the idea's method paradigm — the steered label for a steered
    condition, or the annotator's primary label for a blank one.
    """
    shape = PARADIGM_TO_SHAPE.get(paradigm) if paradigm else None
    candidates: dict[str, float | None] = {
        "recall_safety": _recall_safety(excess_gt_similarity, max_descendant_excess),
        "shape_verifiability": SHAPE_VERIFIABILITY.get(shape) if shape else None,
        "specificity": None if bottleneck_specificity is None else bottleneck_specificity / 3.0,
        "anti_stitching": None if surface_stitching_score is None else 1.0 - surface_stitching_score / 3.0,
        "anti_boilerplate": None if boilerplate_score is None else 1.0 - boilerplate_score / 3.0,
        "source_relevance": None if mean_source_similarity is None else _clamp(mean_source_similarity),
    }
    present = {k: v for k, v in candidates.items() if v is not None}
    missing = [k for k, v in candidates.items() if v is None]

    total_w = sum(WEIGHTS[k] for k in present) or 1.0
    composite = sum(WEIGHTS[k] * v for k, v in present.items()) / total_w

    return TaskSeedScore(
        paper_id=paper_id,
        source=source,
        condition_key=condition_key,
        shape=shape,
        components=present,
        composite=composite,
        missing=missing,
    )


def rank_seeds(scores: list[TaskSeedScore]) -> list[TaskSeedScore]:
    return sorted(scores, key=lambda s: s.composite, reverse=True)
