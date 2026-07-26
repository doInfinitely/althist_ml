# RLVR proof-of-loop — reward-ranked DPO moves the policy and generalizes

2026-07-25. Phase 1 (offline reward + preference data) and Phase 2 (LoRA DPO
proof-of-loop) of the RLVR prong. Goal: before any large training spend,
establish that the composite reward — with excess-GT replaced by the copy
detector (REWARD_AUDIT.md) — actually moves an open-weights policy toward
higher-reward ideas, and that the effect **generalizes to held-out papers**.

## Pipeline

1. **Reward** (`scripts/reward_assemble.py`): `0.30·dist + 0.30·quality +
   0.15·H + 0.15·sim − 0.10·copy`, percentile-normalised. Validated: tracks
   quality (+0.63), anti-gaming holds.
2. **Sampling** (`scripts/sample_dpo.py`): K=8 single-turn ideas/paper from
   Qwen2.5-14B-Instruct (Modal, 1×A100). 2,479 ideas / 313 papers.
3. **Scoring/pairing** (`scripts/score_dpo.py`): embed (H/sim) + copy + Haiku
   annotation (quality/labels) → composite reward → best-vs-worst pair per
   paper. 313 pairs, reward gap mean 0.25.
4. **Split**: 283 train / 30 held-out papers (seed 0).
5. **Train** (`modal/dpo_train.py`): LoRA DPO on Qwen2.5-14B (r=16, β=0.1,
   3 epochs, 1×A100).
6. **Merge + held-out eval**: merge adapter, serve, sample the 30 held-out
   papers from the trained model, score, compare to base.

## Training (283 pairs, 3 epochs)

Clean DPO signal — chosen becomes preferred:

| epoch | loss | rewards/accuracy | rewards/margins |
|---|---|---|---|
| 0.1 | 0.698 | 0.22 | −0.010 |
| 1.7 | 0.680 | 0.65 | +0.029 |
| 2.5 | 0.652 | 0.75 | +0.090 |
| 3.0 | 0.658 | ~0.75 | +0.074 |

## Held-out eval — trained vs base on 30 UNSEEN papers

Raw reward components (directly comparable, no normalisation; 238 base vs 236
trained ideas):

| component | base | trained | Δ |
|---|---|---|---|
| quality | 0.302 | 0.310 | **+0.009** |
| source-sim | 0.268 | 0.273 | **+0.005** |
| H | 0.923 | 0.922 | −0.001 (already near max) |
| copy (contamination) | 0.0008 | 0.0008 | +0.000 (stayed at zero) |
| dist | 0.023 | 0.023 | +0.000 |

Joint-normalised composite reward (percentile over the pooled 474 held-out
ideas), paired by paper:

- base **0.367** → trained **0.530**  (Δ +0.163)
- **trained wins 29 / 30 held-out papers**

## Reading

**The full chain works end-to-end**: reward → reward-ranked preference data →
DPO → a policy that produces higher-reward ideas on papers it never trained on.
The direction is consistent (29/30 papers) and — importantly — the copy
(contamination) term **stayed at zero**, so the policy improved without learning
to regurgitate the held-out paper (the failure mode a naive excess reward would
have invited).

**Honest magnitude.** The *raw* component gains are small (quality +0.009,
source-sim +0.005 on [0,1]); the large composite Δ (+0.163) and 29/30 win rate
come partly from percentile normalisation amplifying a small-but-consistent
edge. So this is a real, generalising shift — not a transformation. That is
exactly what 3 epochs of LoRA DPO on 283 pairs should produce: a clean
proof-of-loop, not a finished model.

## Cost

The entire DPO experiment (sample + train + merge + eval serving) added ~$2.30;
the whole abliteration+RLVR arm is at ~$72 of the $500 budget.

## Scale-up (2026-07-25) — more CLEAN pairs beat more pairs

Scaled the preference set and retrained. Two lessons:

1. **Naive multi-pairing hurt.** Re-pairing the existing 8 samples/paper into
   graded pairs (rank i vs n-1-i) at `min-gap 0.05` gave 778 pairs — but many are
   near-ties (small reward gap). Training on them was *worse* than v1 (accuracy
   below 0.5, negative margins early): near-tie pairs dilute the DPO signal.
2. **Clean multi-pairing helped.** Raising the gap floor to `min-gap 0.12`
   (mean gap 0.226, ≈ v1's quality) gave **497 clean pairs**. Trained 3 epochs:
   margins climbed to **+0.21** (v1 ended +0.07), loss 0.60.
   (Higher-K sampling — more diverse samples — was the other lever, but the
   sampler stalls on server scale-down; left for a hardened sampler.)

**Held-out eval (30 unseen papers), base vs v1 (283) vs v2 (497 clean):**

| component | base | v1 | v2 |
|---|---|---|---|
| quality | 0.302 | 0.310 | **0.315** |
| source-sim | 0.268 | 0.273 | **0.274** |
| dist (human-like) | 0.023 | 0.023 | **0.026** |
| copy (contamination) | 0.0008 | 0.0008 | 0.0009 |
| joint composite | 0.335 | 0.445 | **0.565** |
| paired wins vs base | — | 29/30 | **30/30** |

**v2 beats both base and v1**: the composite lift roughly doubled (+0.11 → +0.23),
raw components are monotonically better base→v1→v2, it wins all 30 held-out
papers, and contamination stayed at zero. So scaling the preference set **works —
provided pair quality is held high** (a gap floor); adding near-tie pairs
backfires. Marginal cost of the whole scale-up: ~$9 (arm total ~$81 of $500).

Adapter/merged on the volume: `dpo/qwen14b-dpo-v2`, `dpo/qwen14b-dpo-v2-merged`.
Train set: `data/analysis/dpo_train_scaled.jsonl` (497). Per-sample held-out
scores: `dpo_v2trained_scored.jsonl`.

## Higher-K scale-up (2026-07-26) — a clean monotonic scaling ladder

Hardened the sampler (concurrency + retry-with-backoff, `sample_dpo.py`) so it
no longer stalls on Modal server scale-down — **271 papers in ~7 min, 0
failures** (was ~1.5 papers/min and stalling). Sampled 8 more ideas/train paper
(K=16), then clean multi-paired (min-gap 0.12) → **1,460 pairs** (mean gap 0.262,
even cleaner than v2 — 16 samples separate better). Trained 1 epoch.

**4-way held-out eval (30 unseen papers):**

| model | pairs / K | joint composite | wins vs base | raw quality | copy |
|---|---|---|---|---|---|
| base | — | 0.320 | — | 0.302 | ~0 |
| v1 | 283 / K8 | 0.404 | 29/30 | 0.310 | ~0 |
| v2 | 497 / K8 | 0.498 | 30/30 | 0.315 | ~0 |
| **hiK** | **1460 / K16** | **0.571** | **30/30** | **0.321** | **~0 (lowest)** |

**Monotonic**: more clean pairs → better generalisation, on both the composite
(+0.09 → +0.18 → +0.25 over base) and — the honest measure — the **raw annotator
quality**, which grew +0.009 → +0.013 → **+0.020** (more than doubled from v1).
The higher-K *diverse* sampling gave a bigger raw bump than v2's re-pairing
alone, confirming diversity > re-pairing; and hiK did it on **1 epoch** vs v2's
3, so more/diverse data generalises better with less overfitting. Contamination
(copy) stayed pinned at zero throughout — hiK is the *lowest*.

Sampler hardening (the enabling fix): per-request timeout + exponential-backoff
retries survive cold-starts, and ~12 concurrent papers keep the endpoint warm so
it never idles into a scale-down. This is what made higher-K feasible.

Cost: higher-K run ~$9 (arm total ~$89 of $500). Artifacts: `dpo/qwen14b-dpo-hiK`
(+ merged), `data/analysis/dpo_train_hiK.jsonl` (1,460), `dpo_hiKtrained_scored.jsonl`.

## Caveats / next

- 283 pairs, single-turn (not agentic), 14B, one seed. Reward is a soft proxy.
- To convert the proof-of-loop into a real capability gain: scale the preference
  set (higher K, more papers, or the leap-pool papers), consider agentic-
  trajectory DPO or online GRPO, and evaluate against the human-marginal /
  operation-bias target (REPORT: unify over-production is the first thing the
  reward should move). Each is a bigger spend; the loop is now de-risked.

## Artifacts

- `data/analysis/dpo_pairs.jsonl` (313), `dpo_train.jsonl` (283),
  `dpo_heldout_papers.json` (30).
- `data/analysis/dpo_{base,trained}_scored.jsonl` — per-sample reward components.
- `data/analysis/dpo_annotations.jsonl` — Haiku annotations (preserved).
- LoRA adapter + merged model on the `althist-hf-cache` volume
  (`dpo/qwen14b-dpo`, `dpo/qwen14b-dpo-merged`).
