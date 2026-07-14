# Project Context: Alternate-History ML Ideation (althist_ml)

This file preserves the project brief for future sessions. The inspiring paper is
`2607.01233.pdf` — *"Measuring the Gap Between Human and LLM Research Ideas"*
(Chen, Zhao, Cohan; arXiv:2607.01233, Yale / UChicago, July 2026). A plain-text
extraction lives in `paper.txt` (via `pdftotext -layout`).

## Original project brief (user prompt, verbatim)

> okay check out the pdf in here. We are replicating this paper essentially with some
> tweaks. I have collected 600+ papers from before the gpu revolution in ML, and now I
> am gathering their sources. We will use the sources of each depth 0 paper as a seed
> for LLM research ideation. A difference between our approach and the inspiring paper
> is that we want to give access to the full paper to the LLM via tool calling, it
> should be abble to pull the abstract or the sources or spans of the paper on it's own
> volition to avoid overwhelming it's context. Another difference is that the inspiring
> paper has opportunity pattern labels and method paradigm labels that we can use to
> steer the generation in the prompt in a fanout pattern in addition to leaving the
> pattern and paradigm guidance blank. Perhaps with this we can broaden the creativity
> of the LLM. Furthermore, we retain their automated annotation strategy and archetype
> clustering. We also retain their "Representation Mechanism" to determine how diffusely
> each proposal is positioned relative to its prior work set, the H value according to
> that section of the paper ideally should be higher rather than lower, but we also want
> to make sure the mean distance to the elements of the source set is lower (so that the
> entropy metric can't be gamed with a proposal unrelated to all sources). We need
> detailed transcripts of the runs with the end goal of using teacher forcing (to get
> activations) + reinforcement learning with verifiable rewards to finetune models if
> the fanout pattern is not sufficient to recover human like ideation. Store a copy of
> this prompt in an md file in the repo as context for other sessions.

## What the inspiring paper does (summary)

- **Task**: literature-grounded ideation. Each instance is a set of 4–8 proximal prior
  works (title + abstract). The target output is a research idea `y = (motivation, method)`.
  The human idea is the one realized in the actual paper; LLM ideas are generated from the
  same reconstructed prior-work context. 11,683 papers (ML confs 2023–2026 + Nature Comms).
- **Prior-work reconstruction**: an LLM prompt (their Fig. 5) extracts the core idea, then
  selects 5–7 proximal prior works using counterfactual / specificity / proximity checks,
  excluding foundational background, generic tools, and baseline-only citations.
- **Two-axis research-taste taxonomy** (their Fig. 2 / Appendix B):
  - *Opportunity Pattern* (why — the gap): Puzzle/Contradiction, Explanation Gap,
    Scope Mismatch, Evidence Gap, Bridge Opportunity, Failure/Risk Gap, Resource Bottleneck.
  - *Method Paradigm* (how — the contribution): Synthesis/Unification, Relax/Extend Scope,
    Robustification, Formal Derivation, Empirical Mapping, Artifact/System, Optimization/Search.
- **Automated annotation** (their Fig. 7 prompt): GPT-5.4-mini assigns primary + secondary
  labels per axis, confidence, and three 0–3 diagnostic scores: surface stitching,
  bottleneck specificity, boilerplate. Validated on 150 held-out papers, Cohen's κ ≈ 0.84/0.81/0.93.
- **Distributional metrics**: TVD, JSD (base-2), normalized entropy over primary labels.
- **Headline finding**: LLM ideas collapse onto bridge-like opportunities (47–64% vs 12.1%
  human) and synthesis/unification methods (22.5–38.7% vs 5.1% human); human entropy > 0.92
  on both axes vs 0.55–0.88 for models. Thinking mode makes it *worse* (sharpens the
  synthesis template). Full-paper-summary context also doesn't help. Prompt-wording
  ablation changes the mix only modestly.
- **Mechanism analyses**:
  - *Archetype clustering*: GPT-5.4-mini rewrites each proposal into a one-sentence
    domain-abstracted archetype; TF-IDF + MiniBatchKMeans (k=30), main verb normalized to
    an operation family. Models over-produce integrate/unify/merge/adapt; humans
    over-produce replace/decouple/formalize.
  - *Representation Mechanism*: embed proposals + prior works with Qwen3-Embedding-4B
    (2560-d, last-token pooling). For a proposal embedding p and prior-work embeddings
    {w_i}: cosine similarities s_i → similarity distribution → normalized entropy **H**
    (higher = proposal comparably close to several priors, not dominated by one). Also
    **B = p·c + H − (s(1) − s(2))** with c the normalized prior-work centroid. Humans
    score higher on both (H=0.7215, B=1.4662) than models.
- **Generation config**: local open-weight runs at temp 0.6, top-p 0.95, top-k 20,
  max 2048 new tokens; API runs temp 1.0 with JSON-schema output. Clustering seeds/params
  in their Appendix C.

## Our replication: differences from the paper

1. **Corpus: pre-GPU-revolution ML papers ("alternate history" framing).** 600+ depth-0
   papers collected from before the GPU revolution in ML. We are currently gathering
   their sources (cited prior works). The source set of each depth-0 paper seeds the
   ideation task — the LLM ideates from the same prior-work context the human authors
   had, and the depth-0 paper is the human ground-truth idea.
2. **Tool-based full-paper access instead of static context.** Rather than pasting
   titles+abstracts (or full-paper summaries) into the prompt, the ideating LLM gets
   tools to pull, at its own volition: the abstract of any source, the source list, or
   arbitrary spans of a paper's full text. This avoids overwhelming its context while
   allowing deeper-than-abstract grounding. (Note: the paper's full-paper-summary
   ablation did NOT close the gap — our bet is that agentic, selective retrieval is
   different from one-shot summary stuffing.)
3. **Label-steered fanout generation.** Use the taxonomy itself for steering: fan out
   generations per seed, conditioning the prompt on specific opportunity-pattern and/or
   method-paradigm labels (e.g. "frame the gap as a Failure/Risk Gap and contribute via
   Robustification"), plus an unconditioned (blank guidance) arm as baseline. Hypothesis:
   explicit steering broadens the effective distribution and may recover human-like
   entropy without finetuning.
4. **Anti-gaming constraint on the Representation Mechanism.** We retain their H metric
   and want it *higher* (proposal diffusely positioned across its source set), but we
   additionally require the **mean distance to the source-set elements to be low** —
   otherwise H can be gamed by proposing something unrelated to all sources (uniformly
   far ⇒ high entropy). So the joint target is: high H *and* high mean similarity
   (low mean distance) to the sources.
5. **Contamination penalty.** Pre-GPU-era ML papers are in every model's pretraining
   data, so the ideating LLM may recover the actual historical depth-0 paper from memory
   rather than ideating from the sources — inflating human-likeness on every metric.
   Two solutions considered: (a) pretrain our own LLM with the depth-0 papers redacted —
   out of scope; (b) **add a penalty term for similarity between the generated proposal
   and the historical depth-0 paper** — feasible, adopted. This composes with the
   Representation Mechanism targets: high H over the source set, low mean distance to
   the sources, but penalized proximity to the ground-truth paper itself. Like the
   H/mean-distance pair, keep it a separate term rather than folding into one scalar,
   so it can serve as a reward component for RLVR without being traded off silently.
   *Baseline correction*: don't penalize raw embedding similarity to the depth-0 paper —
   the historical paper is itself close to the source centroid, so a raw penalty punishes
   any good on-topic proposal. Penalize **excess** similarity instead: similarity to the
   ground-truth paper beyond what is expected given the sources (e.g., margin over the
   proposal's mean source similarity, or over a centroid/regression-based prediction of
   ground-truth similarity from the source embeddings). The term should fire on
   regurgitation of the historical idea, not on topical relevance.
6. **Detailed run transcripts for finetuning.** Every ideation run (including all tool
   calls and reasoning) must be logged as a detailed transcript. End goal: if the fanout
   pattern is NOT sufficient to recover human-like ideation, use the transcripts for
   (a) teacher forcing to extract activations, and (b) RL with verifiable rewards
   (distributional/label targets, H + mean-similarity constraints are candidate reward
   components) to finetune models toward human-like ideation.

## What we retain from the paper

- The automated annotation strategy (LLM annotator, two-axis labels, primary/secondary,
  confidence, surface-stitching / bottleneck-specificity / boilerplate diagnostics).
- Archetype clustering (one-sentence archetype rewrite → TF-IDF → MiniBatchKMeans →
  operation-family analysis).
- The Representation Mechanism (embedding-based H and B), with the anti-gaming mean-distance
  addition described above.
- Distributional comparison metrics: TVD, JSD, normalized entropy against the human
  reference distribution.

## Current status (as of 2026-07-11)

- Corpus gathering lives in `../low-compute-ml`: `all_papers_0/` (638 depth-0
  PDFs, 466 distinct by md5), `paper_sources.json` (depth-0 identity + parsed
  reference lists via OpenAlex/Crossref), `paper_text/` (extracted depth-0
  text), `all_papers_1/` + `papers/` (source PDFs named by title; gathering
  in progress — currently mostly depth-0 papers that other depth-0 papers
  cite).
- `althist ingest` converts that into `data/papers/*.json`. Full run after
  the source PDFs finished copying (2026-07-12): **335 papers** with >=4
  resolved sources, **10,130 sources, 5,909 with full text (58%)**. Two
  leakage guards fire during ingest (self-citations dropped; source text
  byte-identical to the depth-0 paper's own text stripped) — caught when a
  live smoke run read the depth-0 paper via a self-citation.
- Source abstracts backfilled via `scripts/prefetch_abstracts.py`. OpenAlex
  (per-DOI) got the IP a ~17h rate-limit ban after ~612 hits, so the default
  backend is now **Semantic Scholar's batch endpoint** (`--backend s2`, up to
  100 DOIs/POST). Final: **1,704 sources carry abstracts** (many pre-GPU
  papers have no abstract anywhere; ~547 DOIs S2 didn't recognize are left
  uncached for a future OpenAlex pass once the ban lifts). Ingest reads
  abstracts cache-only — it never live-fetches in the per-source loop (that
  once stalled a run for hours on the OpenAlex ban); `--fetch-abstracts` does
  one S2 batch prefill up front instead.
- **Human ideas extracted for all 335/335 papers** (`althist extract`,
  `claude-opus-4-8`, structured output). One paper (`mean-shift-...`) first hit
  a deterministic safety refusal caused by glyph-index (`/C77/C101...`)
  pdftotext output; a CID decoder in ingest repaired it (+4 source files) and
  it extracted cleanly. Extraction now isolates per-paper failures.
- **End-to-end verified with a live Claude run** (`claude-opus-4-8`, blank
  condition, paper `learning-svm-kernel-with-semi-definite-programming-2005`):
  11 turns, 18 tool calls (list/get_abstract/search/read_span/submit), zero
  tool errors, produced a specific grounded idea (radius-margin kernel
  learning) citing the actual sources. Transcript in `data/runs/`.
- Corpus is complete and validates clean. 38 tests pass. Ready for the fanout
  ideation runs (`althist ideate`), then `annotate` / `analyze` / `archetypes`.
- Pipeline implemented in `src/althist/` (Python 3.12, uv): corpus + tool
  layer, taxonomy/prompts, Anthropic + OpenAI-compatible providers, agentic
  ideation loop with JSONL transcripts, annotation, archetype clustering,
  distributional + representation metrics, CLI (`althist validate|extract|
  ideate|annotate|analyze|archetypes`). Tests pass (`uv run pytest`).
- Not yet run against a real corpus or live API; extraction/ideation/
  annotation await the gathered sources.
