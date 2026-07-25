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
