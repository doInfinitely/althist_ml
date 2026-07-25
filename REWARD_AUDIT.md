# Offline reward audit — the contamination penalty penalises the best ideas

2026-07-25. Free, local pre-check before any RLVR spend (see budget discussion:
~$434 of $500 left, but 72B online RL is out of range regardless). Motivated by
the abliteration arm's finding that ideation excess-GT is **re-derivation, not
recall** — which puts the RLVR reward's contamination term (a penalty on
excess-GT) under suspicion. `scripts/reward_audit.py` assembles the decomposed
reward on the 4,924 annotated Opus episodes and asks: does the recall penalty
bite good ideas or bad ones?

## Method

Per episode, all reward components (REPORT §9.6), joined to the annotator's
quality diagnostics (independent of the embedding math):
- **R_dist** — human-marginal likelihood of the (opportunity, paradigm) labels.
- **R_H** — representation entropy H.
- **R_sim** — mean source similarity.
- **R_recall** — −contamination_penalty = −max(0, excess−margin). The suspect term.
- **excess** — cos(idea, GT) − mean source similarity.
- quality: bottleneck_specificity (HIGH good), boilerplate_score (LOW good),
  surface_stitching_score (LOW good), each 0–3 from the automated annotator.

Embeddings: Qwen3-4B, local, cached (`data/analysis/.reward_audit_emb.json`).
Per-episode rows: `data/analysis/reward_audit.jsonl`.

## Result — excess is anti-correlated with quality

Quality by excess quartile (n=1,231 each):

| excess quartile | mean excess | specificity↑ | boilerplate↓ | stitching↓ |
|---|---|---|---|---|
| Q1 (lowest) | 0.051 | 2.76 | 0.74 | 1.26 |
| Q2 | 0.136 | 2.84 | 0.66 | 1.23 |
| Q3 | 0.196 | 2.87 | 0.62 | 1.20 |
| Q4 (highest) | 0.279 | **2.89** | **0.54** | **1.17** |

**Monotonic on all three independent quality axes**: as excess rises, ideas get
*more* specific, *less* boilerplate, and *less* stitched. The contamination
penalty (which grows with excess) bites **hardest on the best ideas.**

Correlations (n=4,924; the annotator signals are independent of the embedding
math, unlike R_sim/R_H — see caveat):

| excess vs | r |
|---|---|
| specificity | **+0.142** |
| boilerplate | **−0.166** |
| stitching | −0.078 |
| R_dist (human-likeness) | +0.092 |
| R_sim | −0.210 *(partly mechanical: excess = gt_sim − mean_sim)* |
| R_H | −0.085 *(partly mechanical)* |

All quality correlations point the same way: **higher excess = higher quality**.
Small r, but at n≈5k these are far below p=0.001, and the direction is the whole
point.

## This also indicts the Harbor `recall_safety` gate

`taskseed.EXCESS_RECALL_SCALE = 0.15`: excess ≥ 0.15 ⇒ recall_safety = 0.

- **56.9%** of the corpus sits at excess ≥ 0.15 — over half of all ideas are
  scored as *pure recall* by the gate.
- That zeroed-out group is the **higher-quality** half: specificity 2.88 vs 2.79,
  boilerplate 0.59 vs 0.71, stitching 1.19 vs 1.25.
- specificity==3 ideas average excess 0.170; specificity≤2 ideas average 0.139.

So the gate (recall_safety weight 0.30 in the seed composite) is
down-weighting the most specific, least-boilerplate ideas — the ones most likely
to make good Harbor task seeds. This is the reward problem showing up on the
revenue path too.

## Why (the mechanism)

excess conflates two things it was meant to separate: **a specific, well-reasoned
idea embeds closer to the specific historical paper** than a vague one does —
whether it got there by recall or by re-derivation. Baseline-correction
(subtracting mean source similarity) was supposed to net out topicality, but it
does not fully: specificity itself raises GT-proximity. The abliteration arm
already showed the high-excess ideas survive fact removal (= re-derivation), and
this audit shows they are also the *higher-quality* ideas. So `−excess` as a
reward term trains the policy away from its best, most specific, reasoned output.

## Implications

1. **Do not ship `−excess` as an RLVR reward term as specified.** It rewards
   vagueness. Any online RL under it would degrade specificity — the opposite of
   the goal.
2. **The Harbor recall_safety gate needs re-specification**, not just re-scaling:
   the excess→recall premise is wrong for >half the corpus. Re-scaling the 0.15
   threshold only moves the cut; it does not fix that excess tracks quality.
3. A usable contamination signal must **separate re-derivation from recall**,
   which excess cannot do alone. Candidates:
   - *Ablation-validated*: excess that collapses when the fact is cut = recall;
     excess that survives = re-derivation (the arm's method — but per-fact
     expensive, and cuts only remove names not concepts).
   - *Verbatim/n-gram overlap* with the GT paper text — a direct copy detector,
     orthogonal to topical specificity.
   - *Conditional*: penalise high excess only when quality is ALSO low
     (high excess + low specificity/high boilerplate = lazy recall; high excess +
     high specificity = re-derivation). The audit data supports this interaction.
   - *Counterfactual*: does the model produce the same idea WITHOUT the sources?
     If yes → recall; if only with them → re-derivation.

## Recommendation

The most valuable thing the abliteration arm produced is this: **the reward's
contamination term measures reasoning, not recall.** Fixing that — offline, with
a copy-detector or a quality-conditional penalty, validated on this same 4,924-
episode set — is a prerequisite to any RLVR run and simultaneously improves the
Harbor gate. That is where the next effort should go, ahead of rollouts.

## Caveats

- Quality diagnostics are from the automated annotator, not human. They are the
  same signals the seed scorer already trusts, but they are model judgements.
- R_sim/R_H correlations with excess are partly mechanical (excess subtracts
  mean_sim). The quality-signal correlations are NOT mechanical and are the load-
  bearing evidence.
- One model's generations (Opus). The RLVR policy would be an open-weights model;
  the reward *function*'s pathology characterised here is model-independent, but
  the exact numbers would shift.
- Reproduce: `uv run python scripts/reward_audit.py`.
