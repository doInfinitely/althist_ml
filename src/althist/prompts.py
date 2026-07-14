"""Prompt templates.

Generation follows arXiv:2607.01233 Fig. 6, adapted for (a) tool-based access
to full source papers instead of inlined abstracts and (b) optional taxonomy
steering injected per fanout condition. Annotation follows Fig. 7 verbatim in
label semantics. Archetype rewriting and human-idea extraction follow
Sec. 4.5 / Appendix A.
"""

from __future__ import annotations

from .schema import Condition, Idea, PaperRecord
from .taxonomy import METHOD_PARADIGMS, OPPORTUNITY_PATTERNS

# ---------------------------------------------------------------------------
# Idea generation (agentic, tool-based)
# ---------------------------------------------------------------------------

GENERATION_SYSTEM = """\
You are a research scientist skilled at developing novel research proposals \
grounded in existing literature. You are given tool access to a set of related \
prior research papers. Use the tools to explore them: list the sources, read \
abstracts, and pull spans of full text or search when a deeper look at a \
specific method, result, or limitation would sharpen your idea. Read \
selectively — you do not need to read everything.

Analyze these papers, identify research gaps and opportunities, and develop \
one coherent novel research idea. The motivation should state the research \
gap, why it matters, and why the listed works leave room for the proposed \
idea. The method should describe a concrete, feasible high-level approach and \
explain how it addresses the gap. Base the idea only on the provided sources.

When you are done, call submit_idea exactly once with your motivation and \
method. Do not state the final idea in plain text; deliver it via submit_idea.\
"""

STEERING_PATTERN = """\

Steering constraint — opportunity pattern: frame the MOTIVATION as a "{name}" \
gap. {definition}\
"""

STEERING_PARADIGM = """\

Steering constraint — method paradigm: construct the METHOD as a "{name}" \
contribution. {definition}\
"""

GENERATION_USER = """\
Develop one novel research idea grounded in the {n_sources} prior works \
available through your tools. Start by calling list_sources.\
"""


def generation_system(condition: Condition) -> str:
    prompt = GENERATION_SYSTEM
    if condition.pattern:
        prompt += STEERING_PATTERN.format(**OPPORTUNITY_PATTERNS[condition.pattern])
    if condition.paradigm:
        prompt += STEERING_PARADIGM.format(**METHOD_PARADIGMS[condition.paradigm])
    return prompt


def generation_user(paper: PaperRecord) -> str:
    return GENERATION_USER.format(n_sources=len(paper.sources))


# ---------------------------------------------------------------------------
# Automated annotation (paper Fig. 7)
# ---------------------------------------------------------------------------

def _axis_block(labels: dict[str, dict[str, str]]) -> str:
    return "\n".join(f"- {k}: {v['name']} — {v['definition']}" for k, v in labels.items())


ANNOTATION_SYSTEM = f"""\
You are an expert annotator of research taste. Label the proposal using \
high-level categories that apply across ML/AI, natural science, medicine, \
engineering, and social or behavioral science. Do not classify by topic, \
domain, or technical substrate. Classify the proposal's problem-finding \
pattern and idea-construction paradigm. The two axes use disjoint labels; \
never copy a method-paradigm label into the opportunity axis, or vice versa.

Opportunity Pattern labels (use the snake_case key):
{_axis_block(OPPORTUNITY_PATTERNS)}

Method Paradigm labels (use the snake_case key):
{_axis_block(METHOD_PARADIGMS)}

Decision guidance. The opportunity axis asks how the gap is found. The \
method-paradigm axis asks what kind of research move constructs the paper. \
Use synthesis_unification only when bridging or reconciling separate lines of \
work is central, not merely when a method has multiple components. Use \
empirical_mapping for estimating, auditing, diagnosing, quantifying, or \
characterizing a phenomenon. Use artifact_system only when a concrete \
artifact, system, tool, platform, material, or prototype is the central \
deliverable. Use optimization_search when the central move is efficiency, \
scaling, search, tuning, selection, allocation, or resource-aware design.

Also assign diagnostic scores: surface_stitching (is the idea a superficial \
A+B combination of prior work; boolean flag plus 0-3 score where 3 is clearly \
superficial stitching), bottleneck_specificity (0-3, where 3 identifies a \
precise bottleneck, mechanism, or limiting factor), and boilerplate_score \
(0-3, where 3 is highly generic or boilerplate).

Respond with the requested JSON only.\
"""

ANNOTATION_USER = """\
Paper ID: {paper_id}
Prior-work titles (context only):
{titles}

Proposal motivation:
{motivation}

Proposal method:
{method}\
"""

ANNOTATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "object",
            "properties": {
                "opportunity_pattern": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string", "enum": list(OPPORTUNITY_PATTERNS)},
                        "secondary": {
                            "type": "string",
                            "enum": [*OPPORTUNITY_PATTERNS, "none"],
                        },
                    },
                    "required": ["primary", "secondary"],
                    "additionalProperties": False,
                },
                "method_paradigm": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string", "enum": list(METHOD_PARADIGMS)},
                        "secondary": {
                            "type": "string",
                            "enum": [*METHOD_PARADIGMS, "none"],
                        },
                    },
                    "required": ["primary", "secondary"],
                    "additionalProperties": False,
                },
            },
            "required": ["opportunity_pattern", "method_paradigm"],
            "additionalProperties": False,
        },
        "confidence": {
            "type": "object",
            "properties": {
                "opportunity_pattern": {"type": "number"},
                "method_paradigm": {"type": "number"},
            },
            "required": ["opportunity_pattern", "method_paradigm"],
            "additionalProperties": False,
        },
        "diagnostics": {
            "type": "object",
            "properties": {
                "surface_stitching": {"type": "boolean"},
                "surface_stitching_score": {"type": "integer", "enum": [0, 1, 2, 3]},
                "bottleneck_specificity": {"type": "integer", "enum": [0, 1, 2, 3]},
                "boilerplate_score": {"type": "integer", "enum": [0, 1, 2, 3]},
            },
            "required": [
                "surface_stitching",
                "surface_stitching_score",
                "bottleneck_specificity",
                "boilerplate_score",
            ],
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "required": ["labels", "confidence", "diagnostics", "rationale"],
    "additionalProperties": False,
}


def annotation_user(paper_id: str, source_titles: list[str], idea: Idea) -> str:
    return ANNOTATION_USER.format(
        paper_id=paper_id,
        titles="\n".join(f"- {t}" for t in source_titles),
        motivation=idea.motivation,
        method=idea.method,
    )


# ---------------------------------------------------------------------------
# Archetype rewriting (paper Sec. 4.5)
# ---------------------------------------------------------------------------

ARCHETYPE_SYSTEM = """\
Rewrite the research proposal as a single-sentence archetype that abstracts \
away domain-specific details while preserving the high-level idea. Start the \
sentence with the main operation verb in the imperative (e.g. "Integrate...", \
"Replace...", "Decouple...", "Formalize...", "Measure..."). Do not name \
specific domains, datasets, or techniques; describe the shape of the move. \
Respond with the sentence only.\
"""

ARCHETYPE_USER = """\
Motivation: {motivation}

Method: {method}\
"""


# ---------------------------------------------------------------------------
# Human idea extraction (paper Appendix A)
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """\
You are an expert AI research analyst. Given a paper, extract its core human \
idea into a proposal-style structured representation. First determine the \
paper's main innovation, the prior limitation or gap it responds to, and the \
specific insight that makes the contribution non-obvious. Then rewrite the \
result as a proposal: the motivation states the research gap and why it \
matters; the method describes the concrete high-level approach the authors \
took. Write in the prospective voice of a proposal (as if the work had not \
yet been done), and do not mention the paper itself.\
"""

EXTRACTION_USER = """\
Title: {title}

Abstract:
{abstract}

{body}\
"""

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "motivation": {"type": "string"},
        "method": {"type": "string"},
    },
    "required": ["motivation", "method"],
    "additionalProperties": False,
}


def extraction_user(paper: PaperRecord, max_body_chars: int = 30000) -> str:
    body = ""
    if paper.full_text:
        body = "Paper text (may be truncated):\n" + paper.full_text[:max_body_chars]
    return EXTRACTION_USER.format(title=paper.title, abstract=paper.abstract, body=body)
