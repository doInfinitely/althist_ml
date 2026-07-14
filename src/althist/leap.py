"""Leap scoring for skip-level remix pools.

A skip pool hides a target paper D and its intermediate ancestors A_i,
exposing only the ancestors' own sources. For a generated idea p the signs
invert relative to normal contamination scoring:

- ``leap_excess``: similarity to D's idea in excess of mean pool-source
  similarity — did the agent compress two research hops into one? Target: HIGH.
- ``intermediate_excess``: the max over ancestors of the same excess to an
  ancestor's idea — rediscovering a stepping stone means it did not skip
  anything. Target: LOW.
- ``leap_margin``: leap_excess - intermediate_excess. The headline per-episode
  number, kept alongside (not instead of) its two components.
- ``topk_mean_similarity``: relevance gate adapted to large pools (median ~88
  sources): mean over the top-k source similarities. The plain mean would
  punish a legitimately focused idea for ignoring the far side of a broad
  pool.

Pretraining contamination caveat: the model knows D. A high leap_excess is
evidence of *reaching* D's idea, not proof it reasoned there from the pool;
transcripts (which sources were read) are the companion evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics.representation import representation_scores
from .schema import Idea, PaperRecord

TOP_K_RELEVANCE = 8  # matches the 4-8 prior-work regime of the inspiring paper


@dataclass
class LeapScores:
    pool_id: str
    target_paper_id: str
    condition_key: str
    is_review: bool
    n_sources: int
    h: float
    mean_similarity: float
    topk_mean_similarity: float
    leap_excess: float
    intermediate_excess: float
    leap_margin: float
    closest_ancestor_id: str | None


def _idea_text(idea: Idea) -> str:
    return f"{idea.motivation}\n{idea.method}"


def compute_leap(
    pool: PaperRecord,
    generated: Idea,
    ancestor_ideas: dict[str, Idea],
    backend,
    condition_key: str = "blank__blank",
    top_k: int = TOP_K_RELEVANCE,
) -> LeapScores:
    """Score one generated idea against a skip pool's hidden papers.

    ``pool.idea`` must be the target's idea (as built by remix);
    ``ancestor_ideas`` maps ancestor paper_id -> extracted idea.
    """
    if pool.remix is None or pool.idea is None:
        raise ValueError(f"{pool.paper_id} is not a scored remix pool")
    anc_ids = [a for a in pool.remix["ancestor_ids"] if a in ancestor_ideas]

    texts = [_idea_text(generated)]
    texts += [f"{s.title}. {s.abstract}" for s in pool.sources]
    texts.append(_idea_text(pool.idea))
    texts += [_idea_text(ancestor_ideas[a]) for a in anc_ids]
    vecs = np.asarray(backend.embed(texts), dtype=np.float64)
    vecs /= np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12, None)

    n = len(pool.sources)
    p, sources, gt = vecs[0], vecs[1 : 1 + n], vecs[1 + n]
    ancestors = vecs[2 + n :]

    rep = representation_scores(p, sources, ground_truth=gt)
    sims = sources @ p
    topk = float(np.sort(sims)[::-1][: min(top_k, n)].mean())

    anc_excess = [(float(a @ p) - rep.mean_similarity) for a in ancestors]
    worst = int(np.argmax(anc_excess)) if anc_excess else None
    intermediate = max(anc_excess) if anc_excess else 0.0

    return LeapScores(
        pool_id=pool.paper_id,
        target_paper_id=pool.remix["target_paper_id"],
        condition_key=condition_key,
        is_review=bool(pool.remix.get("is_review")),
        n_sources=n,
        h=rep.h,
        mean_similarity=rep.mean_similarity,
        topk_mean_similarity=topk,
        leap_excess=float(rep.excess_gt_similarity),
        intermediate_excess=float(intermediate),
        leap_margin=float(rep.excess_gt_similarity - intermediate),
        closest_ancestor_id=anc_ids[worst] if worst is not None else None,
    )
