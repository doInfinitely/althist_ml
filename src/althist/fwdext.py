"""Forward-extension detection.

An idea generated from paper D's sources can be contaminated in two ways:
reproduce D itself (caught by excess-GT similarity), or propose a known
*successor* of D — memorized future work one citation hop forward, which
scores LOW on excess-to-D and therefore passes the recall-safety gate.
This module measures the second mode using the corpus-internal citation
graph: D's descendants E (corpus papers citing D) supply the successor
ideas to test against.

Classification per generated idea, with `threshold` matching the seed
scorer's EXCESS_RECALL_SCALE so categories align with the gate:

- ``regurgitation``:      excess_to_target >= threshold
- ``forward_extension``:  excess_to_target < threshold <= max excess_to_descendant
- ``clean``:              below threshold on both
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics.representation import representation_scores
from .schema import Idea, PaperRecord
from .taskseed import EXCESS_RECALL_SCALE


@dataclass
class FwdExtScores:
    paper_id: str
    model: str
    condition_key: str
    excess_to_target: float
    max_excess_to_descendant: float
    closest_descendant_id: str | None
    label: str  # regurgitation | forward_extension | clean


def _idea_text(idea: Idea) -> str:
    return f"{idea.motivation}\n{idea.method}"


def classify_episodes(
    paper: PaperRecord,
    episodes: list[tuple[str, str, Idea]],  # (model, condition_key, idea)
    descendant_ideas: dict[str, Idea],
    backend,
    threshold: float = EXCESS_RECALL_SCALE,
) -> list[FwdExtScores]:
    """Score all of one paper's generated ideas in a single embed batch."""
    if paper.idea is None or not episodes:
        return []
    desc_ids = sorted(descendant_ideas)

    texts = [_idea_text(idea) for _, _, idea in episodes]
    texts += [f"{s.title}. {s.abstract}" for s in paper.sources]
    texts.append(_idea_text(paper.idea))
    texts += [_idea_text(descendant_ideas[d]) for d in desc_ids]
    vecs = np.asarray(backend.embed(texts), dtype=np.float64)
    vecs /= np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12, None)

    n_ep, n_src = len(episodes), len(paper.sources)
    sources = vecs[n_ep : n_ep + n_src]
    gt = vecs[n_ep + n_src]
    desc = vecs[n_ep + n_src + 1 :]

    out = []
    for i, (model, cond, _idea) in enumerate(episodes):
        rep = representation_scores(vecs[i], sources, ground_truth=gt)
        d_excess = [(float(e @ vecs[i]) - rep.mean_similarity) for e in desc]
        worst = int(np.argmax(d_excess)) if d_excess else None
        max_d = max(d_excess) if d_excess else 0.0
        ex_t = float(rep.excess_gt_similarity)
        if ex_t >= threshold:
            label = "regurgitation"
        elif max_d >= threshold:
            label = "forward_extension"
        else:
            label = "clean"
        out.append(FwdExtScores(
            paper_id=paper.paper_id, model=model, condition_key=cond,
            excess_to_target=ex_t, max_excess_to_descendant=float(max_d),
            closest_descendant_id=desc_ids[worst] if worst is not None else None,
            label=label,
        ))
    return out
