"""Verbatim-copy contamination detector (see REWARD_AUDIT.md).

Replaces embedding excess-GT as the recall signal in the Harbor seed gate. The
offline reward audit (4,924 episodes) showed excess-GT conflates *re-derivation*
with *recall* and penalises the most specific ideas (excess vs specificity
r=+0.14; 56.9% of the corpus scored as "pure recall"), because a specific idea
naturally embeds closer to the specific historical paper regardless of how it
got there.

A direct n-gram overlap with the held-out paper is the honest signal: the model
never saw that paper, so reproducing its phrasing is pretraining recall —
orthogonal to topical specificity, near-zero corpus-wide (median 0, p99 ~0.015,
max ~0.06), quality-neutral, and firing only on the ~0.4% of ideas that actually
copy. Text-only; no embeddings.
"""

from __future__ import annotations

import re

# Containment >= this reads as full recall (recall_safety -> 0). The corpus p99
# is ~0.015 and max ~0.06, so 0.05 flags only genuine near-reproduction and
# leaves the honest re-derivations (the bulk) unpenalised.
COPY_RECALL_SCALE = 0.05


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def ngram_copy(text: str, reference: str | None, n: int = 4) -> float:
    """Containment: fraction of ``text``'s n-grams that also appear in
    ``reference``. Returns 0.0 if either side has fewer than ``n`` tokens.
    High = verbatim reproduction of the reference (the held-out paper)."""
    tg = _ngrams(text, n)
    if not tg or not reference:
        return 0.0
    return len(tg & _ngrams(reference, n)) / len(tg)
