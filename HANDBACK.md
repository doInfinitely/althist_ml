# althist_ml — handback (Titan box → laptop + Modal)

Reverse handoff, 2026-07-17. The Titan-box sprint is done: findings in
`REPORT.md`, operational state in `HANDOFF.md` (both current). This note is
the "what to carry, what to run next" for continuing from the laptop with a
Modal key, in two prongs: **(A) Harbor task building** from the ranked
seeds, and **(B) knowledge-abliteration research** on real GPUs.

## Before leaving this box (logistics)

1. **Push the 4 unpushed commits.** This repo's `main` is 4 commits ahead
   of `https://github.com/doInfinitely/althist_ml.git` and the box has no
   git credentials. `git push -u origin main` with your token (everything
   is committed, nothing staged).
2. **`~/knowledge_abliteration` has uncommitted local changes** made here
   and referenced by REPORT §8: the `--device cuda:0` default fix, the
   `--raw`+`--cutoff` mutual-exclusion fix in `serve_abliterated.py`,
   `facts_althist.py` (paper-targeted UCB1 battery), and the stopword
   guard + fail-loud calibration in `cutoffs.py`. Commit/push those from
   your auth or they die with this box.
3. **Heavy data is gitignored** (~1.8 GB): `data/papers/` (corpus, 478M),
   `data/pools/` (404M), `data/runs/` (841M — **the RLVR training data**,
   4,940 full agentic transcripts), `data/runs_pools/`, quarantines. For
   Modal work, push `data/runs/` + `data/papers/` to object storage (S3 or
   a Modal volume). The 2026-07-13 snapshot zip in `/home/remy/` is stale
   (pre-abliteration, pre-scale-out) — re-archive or rsync fresh.
4. What the laptop needs in-git only: `data/analysis/harbor_seeds.jsonl`
   (the prong-A input) is committed, along with all analysis artifacts.

## Prong A — Harbor task building (no GPU; API key only)

**Input: `data/analysis/harbor_seeds.jsonl`** — 276 gate-passing seeds,
ranked, top-3 per paper over 137 papers; each row: full motivation/method
text, composite + per-component scores, task shape, paper title/year,
steering condition. Shape mix: 159 research-infer, 74 optimize-a-metric,
34 repair-debug, 9 infra-systems (synthesis excluded by the verifiability
gate). All passed the descendant-tightened recall gate (no UCB1-style
regurgitation, no Q-learning-style forward extension).

Steps:
1. **Seed→brief adapter** (the decision HANDOFF deferred "until ranked
   seeds exist" — they now do). Map each row into `expert-ideate`'s seed
   format in `deeptune .../customers/gdm-ml`: motivation/method are the
   idea; `shape` pre-selects the skill's task shape; our `recall_safety`
   is precisely its recall-vs-reasoning gate, precomputed.
2. **Pilot 10 tasks before batching**: take the top ~10 spread across
   shapes, run the full skill pipeline, and check task quality end-to-end.
   Watch specifically whether the skill's own recall gate agrees with our
   `recall_safety` component — a disagreement rate is a free validation
   study of our scorer (and theirs).
3. Batch the remainder per shape (optimize-a-metric and repair-debug
   likely yield the cleanest verifiers; research-infer is the volume).
4. If supply runs short after task-level attrition: `task_seeds.jsonl`
   has the full 4,945-episode ranking — loosen the per-paper cap (436
   total pass gates) before loosening gates themselves. Recall safety is
   the one gate not to loosen (REPORT §7: contamination, not quality, is
   the bottleneck).

## Prong B — knowledge abliteration on Modal (this is the research prong)

Everything below assumes A100/H100-class GPUs (native bf16 — the Turing
fp16 caveats in HANDOFF no longer apply). Context: REPORT §8 + the
`abliteration review handoff.md` in the sibling repo.

Priority order:
1. **Re-run the UCB1 ideation experiment where it has power.** The 7B
   result was capability-gated (raw 7B barely expresses the contamination
   Opus shows). On Modal: (a) screen candidate papers by *raw-model*
   ideation excess first — only ablate where the baseline signal is
   strong; (b) Qwen2.5-14B/32B/72B-Instruct, where completion-format
   recall is crisp; (c) n≥5 episodes per condition per arm (temp
   sampling), not n=1.
2. **Serving: pre-cut the weights, serve with vLLM.** The stdlib
   single-lock server hung under a dead client mid-generation and
   serializes episodes. On Modal, apply the cut once, `save_pretrained`,
   serve with vLLM (`althist ideate --provider openai:<tag>@<modal-url>`
   works unchanged), and run episode waves in parallel.
3. **Chat-format joint attribution** (review handoff experiment #4; the
   known gap where acronym→expansion survived k=28). Attribute over both
   completion and chat formats jointly before cutting — this is the main
   methodological hole between "fact removed" and "fact removed in the
   format agents actually use".
4. **The skip-pool × abliteration cross** (the clean reasoning test):
   ablate the *intermediates* of a skip pool, then check whether the
   model still re-derives them from their sources (`data/pools/` +
   `althist leap` machinery are ready). Blank-arm baseline: intermediate
   excess 0.236, leap 0.086 (REPORT §5). If intermediate re-derivation
   survives ablation, it's reasoning; if it collapses, the one-hop
   behavior was recall all along. This is arguably the single most
   informative experiment available.
5. **RLVR stage** (the end-goal; open weights mandatory, which is why the
   abliteration arm lives here): transcripts in `data/runs/` are the
   training data. Reward components are implemented and deliberately
   separable (REPORT §9.6): distribution match to human marginals + H +
   mean source similarity + worst-route recall penalty (target AND
   descendants) + leap margin. The full-corpus operation-bias result
   (unify 4× over-produced despite label compliance) says operation style
   is the first thing the reward should move. Ablate-then-train composes:
   cut the target knowledge, then reward honest re-derivation.

Watch-fors carried over: the org output-TPM pool is shared across
machines — SDK `max_retries` (already set in `llm.py`) beats retry loops;
`fwdext` must run before `score`; keep the tag-debris/min-length submit
validation if you touch `tools.py` (28% of the first pilot was silent
probe-junk without it); calibration must fail loudly when a fact doesn't
calibrate (the 1.5B " the" incident).

## State of record

- `REPORT.md` — findings (replication, steering, contamination anatomy,
  skip pools, forward extension, abliteration, seed supply).
- `HANDOFF.md` — operational detail, gotchas, resume commands.
- `data/analysis/` — harbor_seeds, task_seeds, leap_scores,
  forward_extension, archetypes, generated_ideas.
- 59 tests passing; `uv sync --extra dev --extra embeddings --extra openai`.
