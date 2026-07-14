"""Distributional comparison metrics (paper Sec. 4.1): TVD, JSD, entropy."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence


def distribution(labels: Iterable[str], label_set: Sequence[str]) -> dict[str, float]:
    """Empirical distribution of ``labels`` over a fixed ``label_set``."""
    counts = Counter(labels)
    unknown = set(counts) - set(label_set)
    if unknown:
        raise ValueError(f"labels outside label set: {sorted(unknown)}")
    total = sum(counts.values())
    if total == 0:
        raise ValueError("no labels")
    return {c: counts[c] / total for c in label_set}


def tvd(p: dict[str, float], q: dict[str, float]) -> float:
    """Total variation distance: 0.5 * sum |p(c) - q(c)|."""
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(c, 0.0) - q.get(c, 0.0)) for c in keys)


def _kl(p: dict[str, float], q: dict[str, float], keys: set[str]) -> float:
    total = 0.0
    for c in keys:
        pc = p.get(c, 0.0)
        if pc > 0:
            total += pc * math.log2(pc / q[c])
    return total


def jsd(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon divergence, base-2 logs (bounded in [0, 1])."""
    keys = set(p) | set(q)
    m = {c: 0.5 * (p.get(c, 0.0) + q.get(c, 0.0)) for c in keys}
    support = {c for c in keys if m[c] > 0}
    return 0.5 * _kl(p, m, support) + 0.5 * _kl(q, m, support)


def normalized_entropy(p: dict[str, float]) -> float:
    """Shannon entropy normalized by log2 of the label-set size."""
    if len(p) < 2:
        return 0.0
    h = -sum(v * math.log2(v) for v in p.values() if v > 0)
    return h / math.log2(len(p))
