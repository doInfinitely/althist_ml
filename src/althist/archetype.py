"""Archetype rewriting, clustering, and operation-family analysis (paper Sec. 4.5).

Pipeline: rewrite each proposal into a one-sentence domain-abstracted
archetype (LLM), cluster archetypes with TF-IDF + MiniBatchKMeans (k=30,
seed 13, matching the paper's configuration), and normalize each archetype's
main verb into an operation family for the model-vs-human log-odds analysis.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .llm import Provider
from .prompts import ARCHETYPE_SYSTEM, ARCHETYPE_USER
from .schema import Idea

# Verb -> operation family. Extend as new verbs show up in archetypes.
OPERATION_FAMILIES: dict[str, str] = {
    "integrate": "integrate", "combine": "integrate", "fuse": "integrate",
    "incorporate": "integrate", "couple": "integrate",
    "unify": "unify", "reconcile": "unify", "bridge": "unify", "connect": "unify",
    "merge": "merge",
    "adapt": "adapt", "transfer": "adapt", "apply": "adapt", "extend": "adapt",
    "generalize": "adapt",
    "design": "design", "build": "design", "construct": "design",
    "develop": "design", "create": "design", "implement": "design",
    "replace": "replace", "substitute": "replace", "swap": "replace",
    "decouple": "decouple", "disentangle": "decouple", "separate": "separate",
    "isolate": "separate", "factor": "separate",
    "formalize": "formalize", "derive": "formalize", "prove": "formalize",
    "characterize": "formalize", "define": "formalize",
    "measure": "measure", "benchmark": "measure", "audit": "measure",
    "quantify": "measure", "evaluate": "measure", "diagnose": "measure",
    "compare": "measure", "map": "measure",
    "optimize": "optimize", "search": "optimize", "tune": "optimize",
    "accelerate": "optimize", "compress": "optimize", "scale": "optimize",
    "reinterpret": "reinterpret", "reframe": "reinterpret", "recast": "reinterpret",
    "relax": "relax", "weaken": "relax", "remove": "relax",
    "robustify": "robustify", "stabilize": "robustify", "regularize": "robustify",
}


def rewrite_archetype(provider: Provider, idea: Idea) -> str:
    return provider.simple_text(
        system=ARCHETYPE_SYSTEM,
        user=ARCHETYPE_USER.format(motivation=idea.motivation, method=idea.method),
        max_tokens=200,
    )


def operation_family(archetype: str) -> str:
    """Normalize the archetype's leading verb into an operation family."""
    match = re.match(r"\W*([A-Za-z-]+)", archetype)
    if not match:
        return "other"
    verb = match.group(1).lower()
    if verb in OPERATION_FAMILIES:
        return OPERATION_FAMILIES[verb]
    # crude lemmatization: integrates -> integrate, unifying -> unify
    for stem in (verb.rstrip("s"), re.sub(r"(ing|ed)$", "", verb), re.sub(r"ing$", "e", verb)):
        if stem in OPERATION_FAMILIES:
            return OPERATION_FAMILIES[stem]
    return verb


def cluster_archetypes(archetypes: list[str], k: int = 30, seed: int = 13) -> list[int]:
    """TF-IDF + MiniBatchKMeans cluster assignment (paper Appendix C params)."""
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    k = min(k, len(archetypes))
    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2 if len(archetypes) > 10 else 1,
        max_df=0.85 if len(archetypes) > 10 else 1.0,
        sublinear_tf=True,
    )
    X = vectorizer.fit_transform(archetypes)
    km = MiniBatchKMeans(n_clusters=k, batch_size=512, random_state=seed, n_init="auto")
    return km.fit_predict(X).tolist()


@dataclass
class OperationEnrichment:
    operation: str
    model_count: int
    human_count: int
    model_share: float
    human_share: float
    log_odds: float


def operation_enrichment(
    model_archetypes: list[str],
    human_archetypes: list[str],
    smoothing: float = 0.5,
) -> list[OperationEnrichment]:
    """Model-vs-human log-odds per operation family, most model-enriched first."""
    model_ops = Counter(operation_family(a) for a in model_archetypes)
    human_ops = Counter(operation_family(a) for a in human_archetypes)
    n_model = max(1, sum(model_ops.values()))
    n_human = max(1, sum(human_ops.values()))
    out = []
    for op in sorted(set(model_ops) | set(human_ops)):
        mc, hc = model_ops[op], human_ops[op]
        p_m = (mc + smoothing) / (n_model + 2 * smoothing)
        p_h = (hc + smoothing) / (n_human + 2 * smoothing)
        out.append(
            OperationEnrichment(
                operation=op,
                model_count=mc,
                human_count=hc,
                model_share=mc / n_model,
                human_share=hc / n_human,
                log_odds=math.log(p_m / (1 - p_m)) - math.log(p_h / (1 - p_h)),
            )
        )
    out.sort(key=lambda e: e.log_odds, reverse=True)
    return out
