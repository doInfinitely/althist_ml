# althist_ml — handoff

Snapshot for moving the project to another machine (the Titan RTX box). Read
`PROJECT_CONTEXT.md` first for the full brief; this note is the operational
"what's here, what runs where, how to continue."

## What this is (one paragraph)

Replication of *"Measuring the Gap Between Human and LLM Research Ideas"*
(arXiv:2607.01233, `2607.01233.pdf` included) on a pre-GPU-era ML corpus, with
tool-based full-paper access, taxonomy-steered fanout generation, and
anti-gaming representation metrics. **Two deliverables:** (1) the research
(human-vs-LLM ideation gap), and (2) the generated ideas themselves, which
seed Harbor RL-task creation for customer gdm-ml (see "Harbor bridge" below).

## Current state (2026-07-13)

- **Corpus: 317 papers** (deduped from 335 via `althist dedupe`; 18 dupes in
  `data/papers_dupes/`), all with extracted human ideas. 57 tests passing.
- **Pilot fanout: COMPLETE & CLEAN.** 5 papers × 64 conditions = 320 episodes
  in `data/runs/`, all annotated. A tool-call-parser bug had junked 28% of the
  first pass (see gotchas); those were quarantined (`data/runs_quarantine/`)
  and regenerated under the hardened validator.
- **Headline pilot results** (Qwen3, `data/analysis/`): unsteered arm
  replicates the paper's collapse (opp. entropy 0.49 vs human 0.94); steering
  compliance near-total; pooled fanout matches human diversity (ent 0.988 /
  0.937, TVD 0.217 / 0.353); singles+blank arms alone match full grid, and
  human-marginal reweighting reaches TVD 0.066 / 0.166. Representation: model
  ≈ human on H and mean-sim (0.899/0.505 vs 0.907/0.509). Archetype bias
  (unify/design over-production) survives steering. `EXCESS_RECALL_SCALE=0.15`
  validated (UCB1-rediscovery anchor floors at 0; bandit 63/64 floored).
- **Source remixing landed** (`althist remix`, `src/althist/remix.py`):
  95 skip-level pools in `data/pools/` (target cites ≥2 corpus papers; pool =
  union of ancestors' sources, leakage-audited). Blank-arm episodes complete
  (`data/runs_pools/`), scored by `althist leap`
  (`data/analysis/leap_scores.jsonl`): mean leap-excess 0.086 vs mean
  intermediate-excess 0.236, margin>0 in only 7/95 — **the agent re-derives
  the hidden intermediates, not the descendant** (contamination tracks the
  source-set fingerprint, not fame). Reading-behavior analysis: ancestor
  coverage does NOT predict leaps (r=0.05).
- **Forward extension measured & gated (2026-07-14).** An idea can dodge the
  recall gate by proposing a memorized *successor* of the seed paper (e.g.
  TD-learning sources → Q-learning; additive logistic regression → gradient
  boosting). `althist fwdext` classifies every episode on descendant-bearing
  papers via the corpus citation graph: over 1,306 episodes / 87 papers,
  63.7% regurgitation (foundational papers are recall-poisoned — the pilot
  bandit result is the norm, good seeds live in the long tail), 2.5% forward
  extension (a floor: only in-corpus descendants are visible), 33.8% clean.
  `score` now takes recall safety from the WORST of excess-to-target and
  max-excess-to-descendant (all 32 forward extensions gate to 0, worst
  previously passed at 0.82). Run `fwdext` before `score`.
- **In flight (resumable, just re-run the same commands):** scale-out fanout
  308 papers × 15 conditions (singles+blank; ~4,620 episodes, 10 parallel
  waves) and descendant-steered pool episodes (95, steered to each target's
  annotated pattern×paradigm; compare leap margins vs blank arm when done).
- 4 papers excluded from scale-out (no source content): harnessing-nonlinearity,
  molecular-classification-of-cancer, on-the-uniform-convergence, reducing-
  the-dimensionality (retry OpenAlex abstracts for these when the ban lifts).

## Hardware notes for the Titan RTX box

Titan RTX = 24 GB, Turing (no native bf16 tensor cores — use fp16/fp32).
The **only** local GPU workload in the main pipeline is the **embedding model**
(Qwen3-Embedding-4B, ~8 GB): fits comfortably on the one card, batches the whole
corpus in minutes. Everything else (clustering, TVD/JSD/entropy, scoring) is
CPU-light. Generation and finetuning are cloud (see split below), so the box is
not the bottleneck.

sentence-transformers may default to bf16 on load — force fp16 or fp32 on
Turing if you hit a dtype error (`SentenceTransformer(..., model_kwargs={"torch_dtype": "float16"})`).

## Where each stage runs

| Stage | Where | Notes |
|---|---|---|
| Ideation generation (`ideate`) | **Cloud** (Anthropic API; `claude-opus-4-8`) | needs `ANTHROPIC_API_KEY`. Open-weight Qwen path exists via `--provider openai:<model>@<url>` for a local/cloud vLLM. |
| Embeddings (`analyze`/`score --embeddings qwen3`) | **Local GPU** (Titan RTX) | Qwen3-Embedding-4B via `pip install althist[embeddings]`. |
| Annotation (`annotate`) | **Cloud** (LLM judge) | paper used GPT-5.4-mini; we use the configured provider. Cheap, latency-tolerant. |
| Archetypes / analysis / scoring | **Local CPU** | no GPU needed. |
| Teacher-forcing + RLVR finetuning | **Cloud A100** (later) | out of scope for this box; transcripts in `data/runs/` are the training data. |

## Setup

```sh
cd althist_ml
uv sync --extra dev --extra embeddings   # add --extra openai for a local vLLM provider
uv run pytest                            # expect 48 passing
export ANTHROPIC_API_KEY=...             # for ideate/extract/annotate (cloud)
uv run althist validate                  # sanity-check the corpus
```

## Resuming / continuing

Ideation is **resumable and idempotent**: `althist ideate` skips any
(paper, condition, model) that already has a successful transcript on disk, so
just re-run the same command to continue.

```sh
# resume the full 64-condition pilot grid (skips the ~37 done):
P="finite-time-analysis-of-the-multiarmed-bandit-problem-2002,\
least-squares-support-vector-machine-classifiers-1999,\
a-simple-rule-based-part-of-speech-tagger-1992,query-by-committee-1992,\
neocognitron-a-self-organizing-neural-network-model-for-a-me"
uv run althist ideate --papers "$P" --pairs

# read the generated ideas:
uv run althist export                    # -> data/analysis/generated_ideas.md

# once ideation is done, the LLM passes we deferred:
uv run althist annotate                  # human + generated ideas -> annotations.jsonl
uv run althist analyze --split-conditions --embeddings qwen3
uv run althist fwdext --embeddings qwen3 # forward-extension pass (BEFORE score:
                                         # score joins its jsonl to tighten the
                                         # recall gate with descendant excess)
uv run althist score --embeddings qwen3 --top 30   # Harbor task-seed ranking
uv run althist archetypes                # operation-family enrichment
```

Run big fanouts in the background and in waves; each episode is a multi-turn
agentic Opus run (~2–3 min). `--max-turns` (default 40) is a runaway ceiling,
not a budget.

## Gotchas already handled (don't re-introduce)

- **max_tokens / streaming.** The ideation turn streams with a 32k ceiling.
  A low non-streaming ceiling truncated `submit_idea` mid-call (missing
  `method`) and caused junk "test motivation" ideas + timeouts. Keep it
  streaming; keep the ceiling generous. (2026-07-12: newer anthropic SDKs
  refuse non-streaming calls with big ceilings outright — `structured_json`
  now streams too.)
- **Tool-call parser truncation → probe-submission junk.** Long `submit_idea`
  calls can arrive with the call syntax's own closing tags leaked into a
  field value (`...</parameter>\n</invoke>\n`) and the *other* field eaten.
  The model then thinks the tool is broken, probes it with "Test motivation."
  / "Test method." stubs — and a bare non-empty check accepted those as final
  ideas (~28% of the first pilot grid was junk). `ToolDispatcher` now strips
  tag debris, enforces `MIN_SUBMISSION_CHARS`, rejects placeholder-pattern
  fields, and echoes received per-field lengths in every rejection so the
  model can tell truncation from tool breakage. Junk transcripts were
  quarantined to `data/runs_quarantine/`; if you loosen the validator,
  re-scan for stubs before analyzing.
- **Leakage guards (in `ingest`).** Self-citations are dropped and any source
  whose text is byte-identical to the depth-0 paper's own text is stripped —
  the model must not read the ground-truth paper. Verified: a smoke run had
  read the depth-0 paper via a self-citation before this landed.
- **CID-glyph PDFs.** Some pdftotext output is `/C77/C101…` glyph indices; a
  decoder in `ingest` repairs it. One paper (mean-shift) refused extraction
  until decoded.
- **OpenAlex is rate-limit-banned on the origin IP** (~17h `Retry-After` after
  the first abstract pass). Abstract prefetch now defaults to Semantic Scholar
  batch (`scripts/prefetch_abstracts.py --backend s2`). `ingest` reads the
  abstract cache only — it never live-fetches (that once stalled a run for
  hours). The ~547 DOIs S2 didn't recognize can be retried on OpenAlex once
  the ban lifts.

## Knowledge-abliteration arm (2026-07-13/14)

Remy's `~/knowledge_abliteration` repo (with `unit-net`, `j-carve` siblings,
all cloned here) surgically removes a paper's identifying facts from an
open-weight model — the "pretrain with depth-0 papers redacted" option made
cheap. Integration is DONE: `serve_abliterated.py` (patched: `--device
cuda:0`; `--raw`+`--cutoff` no longer mutually exclusive) serves Qwen behind
an OpenAI endpoint and `althist ideate --provider openai:<tag>@localhost`
drives it natively. A `paper-ucb1` cutoff (facts_althist.py; calibration now
rejects stopword tokens and fails loudly) removes both surface forms of the
UCB1 paper's name + bonus formula, protecting in-era bandit knowledge.

Findings so far: fact-level removal verified at 7B (28 neurons; completion
recall 0.00; chat description→name confabulates era-plausibly; protects
intact — a wrong Lai-Robbins chat answer reproduces on RAW 7B, i.e. baseline
weakness, not collateral). Ideation-level comparison is capability-gated:
Opus excess-GT on the bandit paper is 0.30–0.37, raw-7B only 0.16–0.20 (7B
barely expresses the contamination in ideation), cut-7B 0.19/0.07 (steered
condition dropped −0.125; blank +0.03 noise; n=1 per cell). Use 1.5B for
nothing (fact not completion-recallable there). Next: repeats for power,
screen target papers by raw-7B excess first, chat-format joint attribution
(known gap: acronym→expansion survives k=28), and lean on this arm at the
RLVR stage where open weights are mandatory anyway.

## Harbor bridge (the business goal)

The generated ideas seed Harbor RL tasks via `expert-ideate` in
`deeptune .../customers/gdm-ml`. `althist score` ranks ideas by
task-worthiness using that skill's own criteria: recall-safety (our
contamination metric = the skill's recall-vs-reasoning gate — a UCB1-style
regurgitation is the worst seed), paradigm→task-shape verifiability (synthesis
scores low; formal/robustification/empirical/optimization score high), and the
annotator's specificity/stitching/boilerplate diagnostics. Decision taken:
build the **seed scorer first**, decide on a seed→brief adapter after seeing
ranked seeds.

## Open decisions / next steps

1. **Finish the pilot grid** (resume command above) before scaling to more papers.
2. **Run the deferred LLM passes** (`annotate`) so the scorer gets real
   specificity/paradigm signals and the blank/pattern arms get a shape.
3. **Use Qwen3 embeddings** (`--embeddings qwen3`) for real recall-safety /
   relevance — the `hashing` backend is a toy for tests and gives degenerate
   (uniform) recall numbers.
4. **Calibrate `EXCESS_RECALL_SCALE`** (in `taskseed.py`) against Qwen3 on known
   anchors: the bandit unsteered idea rediscovered UCB1 (should score ~0
   recall-safety) vs a genuinely novel steered idea (should score high).
5. Decide **full-grid vs pairs-only** fanout for the scale-out, and whether to
   steer toward task-friendly paradigms.

## What's in this package

Included: all source (`src/`, `tests/`), `pyproject.toml`, `README.md`,
`PROJECT_CONTEXT.md`, this file, `examples/`, `scripts/`, the corpus
(`data/papers/`), the abstract cache
(`data/cache/openalex_abstracts.json`), the partial pilot transcripts
(`data/runs/`), the analysis snapshot (`data/analysis/`), and the source paper
PDF.

Excluded: `.venv/` (rebuild with `uv sync`), `__pycache__`, the pdftotext
`source_text` cache (~300 MB, regenerable and only needed to re-ingest from the
original PDFs, which live in `../low-compute-ml` and are not needed — full text
is already baked into `data/papers`).
