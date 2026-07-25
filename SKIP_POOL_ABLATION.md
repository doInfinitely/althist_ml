# Skip-pool × abliteration — the multi-hop recall-vs-reasoning test

Prong B #4 (HANDBACK). Scoped 2026-07-24, continuing the depth-0 abliteration
arm (`MODAL_SCREENING.md`), which established 4/4 that one-hop ideation excess is
**re-derivation, not recall**. This experiment asks the same question one hop
deeper.

## Question

A skip pool hides a target paper **D** *and* its intermediate ancestors {Aᵢ},
exposing only the ancestors' **own** sources (the "grand-sources"). Two signals
per generated idea (`src/althist/leap.py`), baseline-corrected by mean
grand-source similarity exactly like depth-0 excess:

- **intermediate_excess** — closeness to a hidden ancestor A* the model
  rediscovered from the grand-sources ("did it re-walk a stepping stone?").
- **leap_excess** — closeness to D, two hops from the grand-sources ("did it
  compress two research hops into one?").

Test: **ablate the rediscovered stepping stone A\*** from the model (verified
gone from chat generation, same generation-based dead-check as depth-0),
re-ideate from the same grand-sources, and watch both signals move.

## The 2×2 that makes it informative

| after cutting A* | **intermediate_excess(A\*) collapses** | **intermediate_excess(A\*) survives** |
|---|---|---|
| **leap_excess(D) collapses** | model was *routing through recalled A\** to reach D — the "leap" was a recall chain | (diagnostic-of-bug cell) |
| **leap_excess(D) survives** | reaches D by a **different route** → genuine skip, not dependent on the memorized stepping stone | **full re-derivation at both hops** — reasoning all the way down |

The two live cells are the payoff. "Intermediate survives" is the multi-hop
analog of the depth-0 finding (stepping-stone rediscovery is reasoning);
"intermediate collapses but leap survives" is the strongest possible evidence of
a *real* leap. Either is a clean existence-proof result.

## Primary pool — EM

`skip__simplifying-neural-networks-by-soft-weight-sharing-1992`

- **target D**: Simplifying Neural Networks by Soft Weight-Sharing (Nowlan &
  Hinton, 1992)
- **A\* (ablate)**: `maximum-likelihood-from-incomplete-data-via-the-em-algorithm`
  — the **EM algorithm** (Dempster-Laird-Rubin 1977). Opus rediscovered it
  strongly: intermediate_excess **0.400**, leap_excess **−0.166** (reaches the
  stepping stone, not the target — the cleanest single-signal case).
- **control ancestor Aⱼ**: `modeling-by-shortest-data-description-1978` — MDL
  (Rissanen). Built-in same-pool specificity control: cutting EM must **not**
  move MDL's rediscovery.
- 62 grand-sources exposed.

Maximally nameable A* → the exact profile that cut cleanly in the depth-0 arm
(UCB1/Kalman/AdaBoost/Q-learning).

## Secondary / backup pools (blank arm, ranked on Opus)

| pool (target D) | rediscovered A* | int_ex | leap_ex | note |
|---|---|---|---|---|
| dirty-pictures | **Gibbs sampling** (Geman & Geman) | 0.357 | 0.235 | richest — both signals present, fills full 2×2 |
| laplacian-eigenmaps | **Kernel PCA** (Schölkopf) | 0.422 | 0.137 | highest intermediate signal |
| mcmc-intro | **slice sampling** (Neal) | 0.331 | 0.216 | backup |

Cadence: **EM first** (cleanest), then **Gibbs** (richest), extend to a third
only if signal is ambiguous — matching the depth-0 2–3-paper cadence.

## ⚠️ Scoping caveats discovered while scoping

1. **Existing pool runs are all Opus, n=1.** `data/runs_pools/` has 190 Opus
   episodes (95 blank) and **zero 72B**. The candidate ranking above is an *Opus
   prior*. The abliteration arm runs at 72B, so the raw-72B baseline must be
   generated, and — just as depth-0 required confirming 72B expresses UCB1 excess
   before the cut had power — **confirm raw 72B expresses intermediate_excess(EM)
   on this pool** before reading anything into the cut. If raw 72B doesn't
   rediscover EM here, switch pools.
2. **A\* is hidden from ideation but only ablated in weights** — D and MDL remain
   in the model's weights (they're just not shown as sources). Only EM is
   removed. That is the clean, intended manipulation.

## Pipeline (machinery mostly exists)

1. **Verify recall** — confirm raw 72B recalls EM cold (`modal/fact_recall.py`),
   as we did for UCB1.
2. **Add battery** — one `em` entry in `BATTERIES` (`modal/ablate.py`): name +
   distinctive phrase ("expectation-maximization" / "E-step and M-step"), a
   **control fact** that must survive ("maximum likelihood"), protect prompts.
3. **Cut** — `cut(surface=both, dead_check=gen)` → save `cut/qwen72-em-gen`.
   Verify: generation no longer names EM, control survives, collateral logged.
4. **Ideate** — serve cut via `modal/vllm_serve.py`; run **raw 72B baseline
   (n≥3)** and **cut (n≥3)**:
   `althist ideate --pools --papers skip__simplifying-neural-networks-by-soft-weight-sharing-1992 --conditions blank__blank --replicates 3`.
5. **Score** — `scripts/screen_leap.py` (built for this; groups leap output by
   model, breaks excess out **per ancestor** so EM and MDL are separately
   visible). Compare raw-vs-cut intermediate_excess(EM), intermediate_excess(MDL,
   control), and leap_excess(D).

## Controls (validity)

- **Same-pool control ancestor MDL**: unaffected by cutting EM → guards against
  "the cut just dumbed the model down near this whole topic."
- **In-cut control fact** ("maximum likelihood") survives generation.
- **Cross-pool control** (optional): a pool not involving EM — leap/intermediate
  unchanged.

## Cost / time

Per pool ≈ one cut (~15–30 min on 4×A100) + calibration + cold-start serve +
n=3 ideation. Grand-source sets are large (62 here; up to ~300 for review
pools) → longer agentic episodes than depth-0. Budget ~$20–30/pool, ~$50 for
EM+Gibbs. Within remaining budget.

## Risks

- **Collateral near the cut**: EM underlies much of the pool's topic; heavy
  cutting could degrade capability there — MDL is the check.
- **Small n** (matches depth-0): existence-proof grade, not a powered study.
- **Opus-vs-72B mismatch** (caveat 1): the whole design assumes raw 72B
  rediscovers EM on this pool; confirm before spending on the cut.

## EM cut result (2026-07-24) — EM resists removal at feasible sparsity

Ran `cut(battery=em, surface=both, dead_check=gen)` at 72B. Calibrated cleanly
(remove: em-name→' EM', em-steps→' E'; protect: mle→' maximum', bayes→' Bay').

- **PRE-cut** next-token recall: em-name 0.881, em-steps 0.860.
- Both remove facts hit **K*=4096 (k_cap) with dead=False** → 5976 neurons
  zeroed across 80 layers, and the generation-based dead-check **never
  succeeded**.
- **POST-cut** next-token recall collapsed (em-name 0.008, em-steps 0.001) —
  but that's the *weak proxy* the arm already caught. **Free generation still
  emits EM**: "Expectation-Maximization (EM) algorithm", "the first … is called
  the **expectation step**. The second is the **maximization step**." (One
  em-name prompt did confabulate — "missing information principle (MIP) …
  pool-adjacent-violators".)
- **Protect controls fully survive**: mle 0.83, bayes 0.936. So the cut is not
  broken — it is *specifically EM* that resists.

**Reading.** This extends the depth-0 method-ceiling finding. There, named
results cut cleanly at modest K (UCB1 name at K=512). EM — a foundational
*primitive* taught in every ML course, not a single named result — is
redundantly encoded densely enough that column-zeroing at 8× the sparsity
(K=4096/fact) still cannot remove it from generation, while leaving MLE/Bayes
intact. Pushing K higher would risk the controls and general capability with
low odds of success. **EM is not a viable A\* for this method.** The
soft-weight-sharing pool is shelved.

Corollary tension worth noting: the pools with the *highest* intermediate_excess
tend to have the *most entrenched* ancestors (the model rediscovers them
precisely because they are canonical) — exactly the ancestors hardest to cut.
A* selection must trade signal strength against cuttability.

**Pivot**: choose an A* that is a *named method*, not a primitive — comparable
in entrenchment to the depth-0 facts that cut cleanly. Candidates by
(int_ex, cuttability): Kernel PCA (0.422, named 1998 method), slice sampling
(0.331, specialized 2003 — safest cut), Platt scaling (0.370, named
calibration method).

## Kernel PCA pivot (2026-07-24)

New A*: `nonlinear-component-analysis-as-a-kernel-eigenvalue-problem` = **Kernel
PCA** (Schölkopf, Smola & Müller 1998) in the **Laplacian Eigenmaps** pool
(`skip__laplacian-eigenmaps-for-dimensionality-reduction-and-data-re`, target D
= Laplacian Eigenmaps, Belkin & Niyogi 2003). In-pool control ancestor =
**Isomap** (`a-global-geometric-framework-...`). 35 grand-sources.

**Recall confirmed on raw 72B** — and it's a named method, not a primitive:
- kpca-name → " kernel", generates "Kernel Principal Component Analysis (KPCA)" /
  "kernel PCA" (p=0.15/0.62).
- Controls survive: PCA → "principal component analysis" (p=0.44/0.57); Isomap →
  "Isometric Mapping (Isomap)" (p=0.17/0.61).

Cut-side protect controls = PCA (the linear primitive KPCA extends) + Isomap.

**KPCA cut (surface=both, dead_check=gen):** calibrated remove kpca-name→' kernel',
protect pca→' principal' (isomap dropped — its two prompts tokenized differently
on the greedy token; PCA is the key primitive control and Isomap remains the
ideation-side in-pool control). PRE-cut recall 0.728. Hit K*=4096 dead=False —
**but this is a dead-check artifact**: the answer word " kernel" is generic
(kernel trick/methods) and appears in the prompt, so the model echoing the
question trips the substring match. Substantively:
- next-token recall 0.728 → **0.008**;
- PCA control ~0.5 → **0.498** (fully survives — clean specificity);
- **post-cut generation no longer names kernel PCA** — outputs plain "principal
  component analysis (PCA)" and confabulates toward "SVD generalization" (cf. EM,
  which still emitted its full name + step names).

Saved to volume `cut/qwen72-kpca-gen`.

**Chat-verify (2026-07-24) — the cut FAILED; `dead=False` was correct, not an
artifact.** Served the cut and probed in the deployed chat format. The model
still fully explains kernel PCA:
> Q: "What is kernel PCA?" → "Kernel Principal Component Analysis (KPCA) is an
> extension of classical PCA that uses … the kernel trick … maps the data into a
> higher-dimensional feature space …" (complete, fluent, correct)
> Q: "generalize PCA to be nonlinear using a kernel — name it" → "**Kernel
> Principal Component Analysis (KPCA)**"

So the next-token 0.008 + greedy-completion confabulation were the *weak-proxy
gap again*: at 72B, K=4096 drives p(answer|exact prompt)→0 and shifts greedy
completion, yet chat free-generation recovers the full method. Controls fully
intact (PCA, Isomap explained correctly). **Kernel PCA is NOT removed in the
format ideation uses — this pool is not runnable as-is.**

### Pattern (2/2): canonical skip-pool ancestors resist removal at feasible sparsity

Both attempted A* — EM (primitive) and kernel PCA (named method) — hit
K*=4096 `dead=False` and remain fully present in chat generation, while their
in-era controls survive. This contrasts the depth-0 arm, where named algorithms
cut cleanly and *below* the cap (UCB1 name at K=512). The distinguishing factor
looks like **pretraining representation frequency**: EM and kernel PCA are in
every ML textbook / sklearn / countless tutorials, encoded far more redundantly
than "the UCB1 policy" specifically. Emerging methods result: *column-zeroing at
feasible sparsity removes specialized named results from 72B chat generation, but
not heavily-represented canonical ones.*

Decision point (see chat): the skip pools with the highest intermediate_excess
are exactly those with canonical (=hard-to-cut) ancestors. Next candidate by
specialization is **slice sampling** (Neal 2003, int_ex 0.331) — the decisive
test: if even that resists, the pattern is a solid 3/3 methods finding; if it
cuts cleanly, we finally get the multi-hop reasoning-vs-recall readout.

## Slice sampling cut (2026-07-24) — PARTIAL removal (best of the three)

Pool `skip__an-introduction-to-mcmc-for-machine-learning-2003-9bdd86` (review,
184 grand-sources). A* = `slice-sampling-2003`. Controls Gibbs + Metropolis.
Recall confirmed (slice→" slice", p=0.14/0.37 — lower than EM/KPCA, as hoped for
a specialized method).

**Cut (surface=both, dead_check=gen):** calibrated cleanly (remove slice→' slice';
protect gibbs→' Gibbs', metropolis→' Met'). PRE-cut recall 0.482. Hit K*=4096
dead=False; next-token recall 0.482→**0.0**; controls survive (Gibbs 0.622,
Metropolis 0.872).

**Chat-verify — MIXED (directionally split):**
- Q "What is slice sampling?" → **still explains it correctly** ("sample from a
  'slice' … region under the curve where the density exceeds a value"). The
  **name→concept** path is intact.
- Q "name Neal's auxiliary-variable method [from its description]" → **cannot**;
  confabulates "Randomized Quasi-Monte Carlo", "Hit-and-Run", "Random Walk
  Metropolis-Hastings with Auxiliary Variables". The **description→name**
  retrieval path is **broken**.
- Controls fully intact.

So the slice cut is the strongest of the three — it broke exactly the
description→name retrieval path (the direction ideation uses: reason from the
problem to the method) while leaving name→concept comprehension. This is the
same *kind* of surface disruption the depth-0 gen-cuts achieved (UCB1 cut
confabulated "Uniform" for the name-token but left the confidence-bound
scaffold), not a total concept wipe (which column-zeroing at a name-token
threshold cannot do).

Saved to volume `cut/qwen72-slice-gen`. Server stopped after verify.

### Open question for the ideation step

excess is computed on idea *embeddings* (concept, not literal name). The cut
removes name-retrieval, not the method concept — so, exactly as in depth-0, the
prediction is that intermediate_excess will be ~unchanged (concept re-derived
from sources). Running it would extend the depth-0 re-derivation finding to the
multi-hop intermediate; it would NOT test removal of conceptual understanding
(no feasible cut does that at 72B). Decision in chat: run the (expensive,
184-source) ideation on this partial cut, or treat the 3-cut cuttability sweep
as the finding.

## RESULT — slice ideation, raw vs cut (2026-07-24, n=3 each, blank arm)

`screen_leap.py`, per-ancestor excess (the ablated A* row bolded):

| row | 72B-raw | 72B-slice-cut | (Opus n1) |
|---|---|---|---|
| leap_excess (→ target D) | +0.238 ±0.03 | +0.211 ±0.04 | +0.215 |
| **anc: slice sampling (A\*)** | **+0.156 ±0.02** | **+0.213 ±0.10** | +0.331 |
| anc: bayesian density est. | +0.155 | +0.145 | +0.056 |
| anc: factorial HMM | +0.075 | +0.045 | −0.036 |
| anc: EM | +0.072 | +0.061 | −0.027 |
| anc: particle filter | +0.135 | +0.102 | +0.047 |
| anc: Gibbs (stoch. relax.) | +0.156 | +0.191 | +0.003 |

**Ablating slice sampling did NOT reduce the model's rediscovery of slice
sampling from the pool's grand-sources** — it went 0.156 → 0.213 (up, within the
cut's large n=3 noise sd=0.10), the opposite of a recall-driven collapse.
Controls behaved (no collapse; general slight downward drift; Gibbs even rose).
leap_excess to the target review also held (0.238 → 0.211).

### Conclusion — multi-hop re-derivation, consistent with depth-0

The stepping-stone (slice sampling) is **re-derived from the grand-sources, not
recalled by name**: removing the name-retrieval path leaves the model
reconstructing the slice-sampling-shaped idea from the prior work it was shown.
This extends the depth-0 one-hop finding (UCB1/Kalman/AdaBoost/Q-learning:
re-derivation, not recall) to the **multi-hop intermediate**.

Caveats (honest): (1) the cut was **partial** — it removed description→name
retrieval, not the method concept (no feasible 72B column-zeroing cut removes a
concept; EM and kernel PCA couldn't even remove the name). So this shows
name-removal doesn't move a *concept-embedding* excess — the same structural
limitation as depth-0, not a test of concept removal. (2) Raw 72B expresses the
slice intermediate_excess only **modestly** (0.156 vs Opus 0.331) — limited
power, though positive. (3) n=3, one pool, large cut variance.

Net: the skip-pool arm reproduces the depth-0 story one hop deeper, *and* the
3-cut cuttability sweep (EM/kernel PCA resist name-removal, slice sampling only
its description→name path) is itself a methods result about column-zeroing at
72B scaling inversely with pretraining representation frequency.

## Qualitative transcript evidence (2026-07-24)

Rendered the agentic transcripts to Markdown (`scripts/render_transcript.py` →
`data/analysis/transcripts/`): per-turn reasoning, tool calls, results, submitted
idea. These corroborate the embedding-level result at the level of the model's
actual thoughts.

**Slice pool, raw vs cut** (`slice-{raw_72B,cut_ablated}_runs.md`). *Neither*
model ever names "slice sampling" in its reasoning — both instead converge on the
adaptive-MCMC neighbourhood, reasoning from the Bayesian/Gibbs sources:

| method named in reasoning | raw 72B | ablated (slice-cut) |
|---|---|---|
| Gibbs sampling | 21 | 45 |
| adaptive rejection (Metropolis) sampling | 3 | 26 |
| Hamiltonian MC | 6 | 0 |
| parallel tempering | 0 | 8 |

The raw model's 0.156 intermediate_excess toward slice sampling is thus
*conceptual re-derivation, not name recall* — it reaches auxiliary-variable /
adaptive-MCMC ideas without uttering the name. The cut didn't move it because it
removed a name-retrieval path neither model was using; both still propose
adaptive-MCMC methods (the cut reached for parallel tempering where raw reached
for Hamiltonian MC — different named alternatives in the same region, neither the
ablated one).

**Depth-0 gen-cuts, each ideating on its own target paper**
(`depth0-{ucb1,kalman,adaboost,qlearning}-gencut_runs.md`). The transcripts show
the ablated model *reconstructing the target's conceptual content from the
sources*, which is why excess was unchanged — and reveal that "how gone" varies:

- **Kalman, AdaBoost**: the removed name never appears in the reasoning, yet the
  concept is re-derived (AdaBoost-cut says "boosting" 63×, never "AdaBoost";
  Kalman-cut works recursive state-estimation without "Kalman").
- **UCB1**: the model re-derives the confidence-bound idea *in its own proposed
  method* — "develop an adaptive policy … using upper confidence bounds" — even
  though direct chat probing made it confabulate "Uniform" for the name-token.
  Given the sources (index policies, confidence bonuses), it reconstructs the
  concept and phrase during agentic ideation.
- **Q-learning**: "Q-learning" reappears, partly via the model summarising a
  source that itself names it, alongside heavy temporal-difference reasoning.

Takeaway: single-prompt chat removal (the verify bar) is stricter than what
survives *agentic* ideation — with sources in context, the model reconstructs the
target's concept (and sometimes the exact name) regardless of the cut. That is
the mechanism of "re-derivation, not recall," seen directly. It does not change
any excess number (excess stayed flat across all cuts) but it sharpens the
interpretation: the excess is conceptual reconstruction from the provided prior
work.

## Status — COMPLETE

- [x] `screen_leap.py` built — verified vs `leap.py` on Opus baseline.
- [x] Cuttability sweep (3 candidate A*):
  - EM — recalled cold; cut hit cap `dead=False`; **fully present in chat** → shelved.
  - Kernel PCA — recalled; cut hit cap; **fully present in chat** → shelved.
  - Slice sampling — recalled; cut broke **description→name** (confabulates
    Hit-and-Run/RQMC) but left name→concept. Best of the three → used.
- [x] Slice pool ideation: raw 72B baseline (n=3) + cut (n=3), blank arm.
- [x] Scored raw vs cut (per-ancestor): **ablating slice sampling did not reduce
      its rediscovery** (0.156 → 0.213), controls behaved → **multi-hop
      re-derivation, consistent with depth-0.**
- All Modal apps stopped after each phase (zero idle billing). Dead EM/KPCA
  checkpoints deleted from the volume; `cut/qwen72-slice-gen` retained.

### Follow-ups if revisited
- The first slice-cut ideation stalled: `SCALEDOWN=180` let the server scale to
  zero mid-episode on the 184-source review pool and the client hung. Fixed with
  `SCALEDOWN=1200` (`scripts/slice_cut_ideate.sh`). Use the longer window for any
  large-pool ideation.
- Raw 72B slice intermediate_excess is modest (0.156 vs Opus 0.331) → for more
  power, pick a pool whose A* both (a) 72B-raw rediscovers strongly and (b) is
  specialized enough to cut. The two pull against each other (canonical = strong
  signal but uncuttable).
