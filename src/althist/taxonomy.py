"""The two-axis research-taste taxonomy (arXiv:2607.01233, Appendix B).

Keys are stable snake_case identifiers used in conditions, annotations, and
analysis; ``name`` is the paper's display label; ``definition`` is the
annotation-guidance wording.
"""

from __future__ import annotations

from .schema import Condition

OPPORTUNITY_PATTERNS: dict[str, dict[str, str]] = {
    "puzzle_contradiction": {
        "name": "Puzzle / Contradiction",
        "definition": "The gap comes from a paradox, tradeoff, surprising failure, or conflicting evidence.",
    },
    "explanation_gap": {
        "name": "Explanation Gap",
        "definition": "The proposal asks why something works, fails, varies, or appears — a missing causal, mechanistic, theoretical, or explanatory account.",
    },
    "scope_mismatch": {
        "name": "Scope Mismatch",
        "definition": "Prior work relies on narrow, unrealistic, or poorly transferable assumptions, regimes, or boundary conditions.",
    },
    "evidence_gap": {
        "name": "Evidence Gap",
        "definition": "The field lacks ways to observe, measure, benchmark, audit, diagnose, validate, compare, or accumulate evidence.",
    },
    "bridge_opportunity": {
        "name": "Bridge Opportunity",
        "definition": "Disconnected literatures, theories, evidence streams, communities, or methods could be connected.",
    },
    "failure_risk_gap": {
        "name": "Failure / Risk Gap",
        "definition": "Existing approaches raise brittleness, unreliability, bias, uncertainty, safety/privacy/security, or reproducibility concerns.",
    },
    "resource_bottleneck": {
        "name": "Resource Bottleneck",
        "definition": "Progress is limited by cost, compute, time, data access, sample scarcity, experimental burden, deployment friction, usability, or scalability.",
    },
}

METHOD_PARADIGMS: dict[str, dict[str, str]] = {
    "synthesis_unification": {
        "name": "Synthesis / Unification",
        "definition": "Bridges, integrates, reconciles, or unifies separate literatures, theories, evidence streams, mechanisms, or methods.",
    },
    "relax_extend_scope": {
        "name": "Relax / Extend Scope",
        "definition": "Makes prior work function under weaker assumptions, broader scope, new regimes, noisier conditions, or more realistic settings.",
    },
    "robustification": {
        "name": "Robustification",
        "definition": "Reduces failures, brittleness, risk, uncertainty, bias, unreliability, or trustworthiness problems.",
    },
    "formal_derivation": {
        "name": "Formal Derivation",
        "definition": "Introduces a formal model, theorem, bound, objective, derivation, proof, ontology, taxonomy, conceptual distinction, or explanatory formulation.",
    },
    "empirical_mapping": {
        "name": "Empirical Mapping",
        "definition": "Builds or applies systematic measurement, benchmarks, diagnostics, datasets, empirical maps, comparative studies, or pattern analyses.",
    },
    "artifact_system": {
        "name": "Artifact / System",
        "definition": "Builds a concrete artifact, software system, platform, device, material, prototype, or deployment workflow as the central contribution.",
    },
    "optimization_search": {
        "name": "Optimization / Search",
        "definition": "Uses optimization, search, screening, tuning, active/adaptive design, scaling, resource allocation, or efficiency strategies to discover or improve a solution.",
    },
}


def fanout_conditions(
    include_blank: bool = True,
    include_patterns: bool = True,
    include_paradigms: bool = True,
    include_pairs: bool = False,
) -> list[Condition]:
    """Enumerate steering conditions for one seed paper.

    Default fanout is 1 blank + 7 pattern-steered + 7 paradigm-steered = 15
    generations per paper; ``include_pairs`` adds the 49 full combinations.
    """
    conditions: list[Condition] = []
    if include_blank:
        conditions.append(Condition())
    if include_patterns:
        conditions.extend(Condition(pattern=p) for p in OPPORTUNITY_PATTERNS)
    if include_paradigms:
        conditions.extend(Condition(paradigm=m) for m in METHOD_PARADIGMS)
    if include_pairs:
        conditions.extend(
            Condition(pattern=p, paradigm=m)
            for p in OPPORTUNITY_PATTERNS
            for m in METHOD_PARADIGMS
        )
    return conditions
