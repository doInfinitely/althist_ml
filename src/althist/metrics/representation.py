"""Representation Mechanism scores (paper Sec. 4.5) plus our anti-gaming terms.

Given a proposal embedding ``p``, source (prior-work) embeddings ``w_i``, and
optionally the ground-truth depth-0 paper embedding ``g`` (all L2-normalized):

- ``h``: normalized entropy of the softmax over cosine similarities s_i.
  Higher = the proposal is comparably close to several sources rather than
  dominated by one. Target: HIGH.
- ``mean_similarity``: mean_i s_i. Anti-gaming companion to ``h`` — a proposal
  unrelated to every source gets a flat (high-entropy) similarity profile, so
  ``h`` alone can be gamed; requiring high mean similarity closes that hole.
  Target: HIGH (equivalently, mean distance to sources LOW).
- ``b``: the paper's composite  B = p.c + H - (s(1) - s(2))  with c the
  normalized source centroid. Reported for comparability; the separate terms
  above are the reward-facing signals.
- ``excess_gt_similarity``: contamination signal — similarity to the
  ground-truth paper in EXCESS of the proposal's mean source similarity.
  Pre-GPU-era papers are in pretraining data, so raw sim(p, g) is confounded
  with topical relevance (g is itself near the source centroid); the excess
  fires on regurgitation of the historical idea, not on being on-topic.
  ``contamination_penalty`` clips it at a margin: max(0, excess - margin).
  Target: LOW.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RepresentationScores:
    h: float
    mean_similarity: float
    b: float
    top_gap: float  # s(1) - s(2)
    centroid_similarity: float  # p . c
    gt_similarity: float | None = None
    excess_gt_similarity: float | None = None
    contamination_penalty: float | None = None


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(norm, 1e-12, None)


def _softmax(x: np.ndarray, temperature: float) -> np.ndarray:
    z = x / temperature
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def representation_scores(
    proposal: np.ndarray,
    sources: np.ndarray,
    ground_truth: np.ndarray | None = None,
    temperature: float = 0.1,
    contamination_margin: float = 0.0,
) -> RepresentationScores:
    """Compute H / B / mean-similarity / contamination scores.

    ``proposal``: (d,); ``sources``: (k, d) with k >= 2; ``ground_truth``:
    (d,) or None. Vectors need not be pre-normalized. ``temperature`` scales
    the softmax over cosine similarities before the entropy (cosines live in a
    narrow band, so a temperature < 1 keeps H from saturating at 1).
    """
    sources = np.asarray(sources, dtype=np.float64)
    if sources.ndim != 2 or sources.shape[0] < 2:
        raise ValueError("need at least 2 source embeddings")
    p = _normalize(np.asarray(proposal, dtype=np.float64))
    w = _normalize(sources)

    sims = w @ p  # cosine similarities s_i
    probs = _softmax(sims, temperature)
    h = float(-(probs * np.log2(np.clip(probs, 1e-12, None))).sum() / np.log2(len(sims)))

    top_two = np.sort(sims)[::-1][:2]
    top_gap = float(top_two[0] - top_two[1])
    centroid = _normalize(w.mean(axis=0))
    centroid_sim = float(centroid @ p)
    b = centroid_sim + h - top_gap
    mean_sim = float(sims.mean())

    gt_sim = excess = penalty = None
    if ground_truth is not None:
        g = _normalize(np.asarray(ground_truth, dtype=np.float64))
        gt_sim = float(g @ p)
        excess = gt_sim - mean_sim
        penalty = max(0.0, excess - contamination_margin)

    return RepresentationScores(
        h=h,
        mean_similarity=mean_sim,
        b=b,
        top_gap=top_gap,
        centroid_similarity=centroid_sim,
        gt_similarity=gt_sim,
        excess_gt_similarity=excess,
        contamination_penalty=penalty,
    )
