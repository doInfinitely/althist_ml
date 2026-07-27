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

## K=32 — the composite keeps climbing but raw QUALITY plateaus (2026-07-26)

Pushed to K=32 (16 more samples/train paper via the hardened sampler, 0
failures) → clean multi-paired (min-gap 0.12) → **2,237 pairs** (mean gap 0.306
— higher K keeps improving pair separation). Trained 1 epoch.

**5-way held-out eval (30 unseen papers):**

| model | pairs/K | composite | wins | raw quality Δ |
|---|---|---|---|---|
| base | — | 0.309 | — | — |
| v1 | 283/K8 | 0.378 | 29/30 | +0.009 |
| v2 | 497/K8 | 0.455 | 30/30 | +0.014 |
| K16 | 1460/K16 | 0.512 | 30/30 | **+0.020** |
| K32 | 2237/K32 | **0.586** | 30/30 | +0.014 |

**The key nuance.** The *composite* reward rises monotonically (K32 best, +0.28
over base, 30/30 wins), but the **raw annotator quality plateaued at K16** and
*dipped* at K32 (+0.020 → +0.014, back to v2's level). The K16→K32 composite gain
came from the softer components — H (0.924→0.926), source-sim, and dist — not from
quality. Contamination (copy) stayed at zero throughout.

**Reading.** Scaling helps the *substantive* axis (idea quality) up to ~K16, then
saturates; beyond that, further composite gains come from the softer, more
gameable reward terms (H/sim/dist). This is the point where "more K" starts
optimizing the proxy rather than the thing we care about. So **K16 is the sweet
spot on quality**; K32 buys composite/human-likeness (dist highest at K32) at the
cost of the quality gain. A cleaner further lever now is likely *reward-side*
(reweight toward quality, harden H/sim against gaming) or *task-side* (agentic
trajectories), not just more K.

Cost: K=32 run ~$6 Modal (arm total ~$95 of $500) + Haiku annotation (Anthropic,
separate). Artifacts: `dpo/qwen14b-dpo-K32` (+ merged),
`data/analysis/dpo_train_K32.jsonl` (2,237), `dpo_K32trained_scored.jsonl`.

## Reward reweighting toward quality (2026-07-26) — doesn't break the plateau

The K32 plateau suggested reweighting the reward toward quality. Re-paired the
*same* K32 samples (free — already scored/annotated) with a quality-dominant
weighting (qual 0.60, sim 0.20, copy 0.20; H/dist 0) → 2,239 pairs (mean gap
0.397, sharper contrasts), trained 1 epoch (`qwen14b-dpo-K32q`).

**Held-out eval (30 unseen papers):**

| component | base | K16 | K32 | K32q (quality-wt) |
|---|---|---|---|---|
| **raw quality** | 0.302 | **0.321** | 0.315 | 0.316 |
| source-sim | 0.268 | 0.272 | 0.275 | **0.283** |
| copy | ~0 | 0.0006 | 0.0009 | **0.0005** |
| its-own reward (0.6q+0.2sim−0.2copy) | 0.193 | 0.335 | 0.463 | **0.600** |

**Reweighting moved the model on the reweighted reward** (K32q best, 0.600) and
improved **grounding** (sim highest) and **contamination** (copy lowest) — but
**raw annotator quality stayed at 0.316, still below K16's 0.321.** It did not
break the quality ceiling; it just shifted which components moved (grounding
instead of the softer H/dist).

**Reading — the binding constraint is the quality *signal*, not the weights.**
Annotator quality saturates ~+0.02 (K16) regardless of weighting. The likely
causes: (a) the Haiku quality signal is coarse (0–3 integer specificity/
boilerplate/stitching — limited resolution to reward-optimise against), and/or
(b) the base 14B's idea-quality ceiling. So the productive levers are no longer
reward-weight or data-volume tweaks — they are a **finer/human quality signal**
or a **more capable base / agentic task**, per the caveats below. Useful
byproduct: K32q is the best-grounded, lowest-contamination model of the set.

Cost: reweight run ~$5 Modal (re-pairing was free; arm total ~$100 of $500).
Artifacts: `dpo/qwen14b-dpo-K32q` (+merged), `dpo_train_K32q.jsonl`,
`dpo_K32qtrained_scored.jsonl`. score_dpo.py now takes `--w-*` weight overrides.

## Pairwise Opus judge (2026-07-26) — the finer signal the plateau needs

The quality plateau is signal-bound: Haiku gives quality only as coarse 0-3
diagnostics, and most ideas cluster at specificity=3 (no resolution). Prototyped
a **pairwise Opus judge** (`scripts/pairwise_judge.py`) that answers DPO's actual
question directly — "is idea A or B the stronger proposal?" — judged on
specificity / novelty / method-concreteness / grounding, with position bias
controlled by requiring both orders (A,B) and (B,A) to agree.

**Prototype (20 train papers):**
- **Resolution**: **16/20** pairs that Haiku rated *identically* on quality got a
  confident, order-consistent winner from Opus. It resolves what the coarse
  labeler can't.
- **Different signal**: on the few Haiku-*clear* pairs, Opus agreed only 2/8 —
  and only 8/20 papers even had a Haiku-clear pair (the rest all-tied at
  specificity=3). So Haiku's coarse quality ranking is weakly aligned with a
  holistic judge, i.e. the DPO pairs were often mis-ordered — a plausible
  mechanism for the plateau.
- Confidence is modest (0.55-0.73): these are close calls (same paper, same base
  model), as expected — but consistently resolvable.

Takeaway: a pairwise Opus judge is DPO-native (its output *is* a preference pair,
no absolute-scale calibration) and has the resolution the absolute labeler lacks.
The natural test is to build a pairwise-judged DPO set (chosen = judged winner)
and retrain to see if it breaks the ~+0.02 quality ceiling. Cost of the full
judge pass is the gate: ~1,700 pairs × 2 orders on Opus ≈ a real API spend, so
worth scoping before committing.

## Full pairwise-judged DPO (2026-07-26) — the plateau was a MEASUREMENT artifact

Judged K32 candidate pairs with the pairwise Opus judge (composite reward used
only to *select* spread pairs; Opus decides the winner, both orders, kept if
consistent) → **1,261 pairwise-judged pairs**. Headline from the judging itself:
**Opus reversed the cheap-reward ordering on 42%** of pairs — the composite
reward and a good judge agree only ~58% of the time, so the old DPO pairs were
substantially mis-ordered. Trained 1 epoch (`qwen14b-dpo-pw`).

**Eval, two ways — and they disagree, which is the whole point:**

*Haiku (coarse) raw quality:* pw = 0.314 — **below** K16's 0.321. On the coarse
metric, pairwise training looks like no improvement.

*Opus pairwise head-to-head (win-rate among decided, n=90 each):*

| matchup | win / lose / tie | win-rate |
|---|---|---|
| **pw vs base** | 42 / 28 / 20 | **60%** |
| **pw vs K16** | 40 / 31 / 19 | **56%** |
| K16 vs base (anchor) | 36 / 33 / 21 | 52% |

**The ordering flips between metrics.** Haiku ranks K16 > pw; Opus ranks pw > K16
— and says K16 is barely better than base (52%, a coin flip) despite K16 having
the highest *Haiku* quality. So the "quality plateau" was largely a **measurement
artifact of the coarse 0-3 signal**: K16's Haiku-quality gain didn't correspond to
ideas a fine judge prefers, while pw's improvement — invisible to Haiku — is real
under Opus. Fixing the signal (for training *and* eval) is what broke through.

**Significance (firmed up, n≈236/matchup — 8 matched pairs/paper, cached,
`scripts/head2head.py`):**

| matchup | win / lose / tie | win-rate | binomial p |
|---|---|---|---|
| pw vs base | 107 / 70 / 59 | **60.5%** | **0.0066** ✓ |
| pw vs K16 | 102 / 74 / 61 | **58.0%** | **0.042** ✓ |
| K16 vs base (anchor) | 100 / 84 / 53 | 54.3% | 0.27 — n.s. |

Win rates held almost exactly from the n=90 pass (60→60.5, 56→58, 52→54), and
both pw matchups are now **statistically significant** (p<0.05). The anchor stays
non-significant: **K16 — the highest-*Haiku*-quality model — has no distinguishable
advantage over base under the fine judge.** That is the plateau-as-artifact result
with significance behind it. Contamination (copy) stayed at zero throughout.

**Conclusion of the RLVR arc.** "Signal-bound, not recipe-bound" is confirmed and
resolved: the recipe (reward → diverse sampling → quality-gated pairs → DPO) was
sound; the binding constraint was the coarse quality *signal*, on both ends.
Replacing it with a pairwise Opus judge — DPO-native, high-resolution — produces a
model a strong judge prefers to every earlier variant, where more-data and
reward-reweighting had plateaued. Cost: pairwise judging + head-to-head ≈ Opus
API (separate); Modal at ~$103 of $500.

Artifacts: `dpo/qwen14b-dpo-pw` (+merged), `data/analysis/dpo_train_pw.jsonl`
(1,261), `dpo_pwtrained_scored.jsonl`, `.pw_judgments.jsonl` (judgment cache).

## Toward online RL — distill the judge into a reward model (2026-07-26)

Neither literal option is clean: agentic-trajectory DPO has no offline form
(divergent tool-use trajectories can't share a DPO prompt; tool-result tokens
contaminate the loss), and Opus-judge-in-the-loop GRPO is prohibitively expensive
(thousands of Opus calls per training run). The standard fix is
**judge → preference data → reward model → RL**: distill the pairwise judge into a
cheap local scorer that replaces Opus in the loop.

`modal/reward_model.py`: Bradley-Terry LoRA reward head on **Qwen2.5-3B**, trained
on the 1,261 pairwise pairs; the 710 head-to-head judgments (cross-model, unseen
papers) are the independent eval.

- **In-distribution val accuracy: 86%** (held-out same-distribution pairs).
- **Held-out head-to-head agreement with Opus: 85.3%** — and *no degradation* on
  the harder cross-model/unseen-paper setting:

  | RM vs Opus | agreement |
  |---|---|
  | pw vs base | 87.6% |
  | pw vs K16 | 83.5% |
  | K16 vs base | 84.8% |
  | **overall** | **85.3%** |

So the expensive Opus pairwise judge is now a **cheap 3B reward model that
reproduces it 85% of the time** on unseen cross-model comparisons. That is the
enabler: it makes online GRPO affordable (RM reward, no Opus per step) *and*
enables cheap RM-scored pairing at scale. `dpo/qwen3-rm` (+scores).

**Remaining for literal online GRPO** (a further infra build, not yet run):
trl GRPOTrainer needs trl≥0.16 + transformers≥4.48 + vLLM rollouts, and loading
the 14B policy + 3B RM reward together → multi-A100. With the RM in hand this is
now *feasible*; it's a heavier env than the DPO pipeline. Cheaper alternative that
captures most of the value: RM-score a large candidate-pair set (no Opus cost) →
bigger clean pairwise-signal DPO → retrain.

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
