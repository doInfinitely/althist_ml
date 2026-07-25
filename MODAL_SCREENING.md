# Modal phase — screening findings (2026-07-23)

Continuation of the abliteration arm (REPORT §8) on Modal, laptop-driven.
Goal: does a *bigger* raw model express the UCB1 contamination strongly enough
for the ablation ideation experiment to have power (the 7B was capability-gated)?

## Infra stood up

- `modal/vllm_serve.py` — vLLM OpenAI endpoint on Modal, model/GPU baked into
  the image env (containers don't inherit the local env — two bugs came from
  that: wrong model served, then TP=1→OOM). Qwen2.5-72B-Instruct on
  **4×A100-80GB, TP=4**, 32k ctx. Weights on the `althist-hf-cache` volume.
  Drives althist unmodified: `--provider openai:<model>@<url>/v1`,
  `OPENAI_API_KEY`=the vLLM key.
- `althist ideate --replicates N` — replicate-aware resume (top-up to N).
- `scripts/screen_excess.py` — per-episode excess-GT by (paper, condition,
  model); direct Opus-vs-72B comparison from `data/runs`.
- `modal/fact_recall.py` — completion-format fact recall on the raw model via
  transformers (logits), sharded `device_map="auto"`.
- Account GPU limit was raised 1→4 mid-session (multi-GPU wouldn't schedule
  before that; single-GPU always did).

## Result 1 — ideation-excess (blank arm, n=5 72B vs n=1 Opus)

| paper | Opus | 72B | 7B (Titan) |
|---|---|---|---|
| bandit / UCB1 | +0.368 | **+0.233 ±0.03** | ~0.16–0.20 |
| query-by-committee | +0.228 | +0.065 ±0.05 | — |
| neocognitron | +0.127 | +0.123 ±0.07 | — |
| LS-SVM | +0.048 | +0.107 ±0.04 | — |
| POS-tagger | +0.047 | +0.113 ±0.02 | — |

72B ideation-excess is **moderate** — above 7B, below Opus on the memorized
papers. Not the clean "bigger = crisper" hoped for. (Opus is n=1 here — noisy.)

## Result 2 — fact recall (raw 72B, completion format) — DECISIVE

72B recalls the UCB1 knowledge cold:
- "UCB stands for" → "Upper Confidence Bound" (p=0.71)
- "Auer, Cesa-Bianchi and Fischer's … policy is called the" → "UCB1 policy",
  then recites the algorithm.
- **bonus formula**: "UCB1's exploration bonus … equal to the" → "square root
  of the logarithm of the number of total pulls divided by the number of pulls
  of that particular arm" (p=0.81); another prompt volunteers "2 ln t". ≈ the
  real √(2 ln n / n_j).
- Controls survive: Lai & Robbins p=1.00/0.98; epsilon-greedy recognized.

## Conclusion

The precondition missing at 7B is **satisfied at 72B**: strong fact-level
recall of the target knowledge. The fact-recall/ideation-excess gap (knows
UCB1 cold, only moderately centers ideation on it) is itself the reason to run
the ablation: cut the fact, then measure whether **fact recall collapses**
(does the cut work at 72B?) and **ideation-excess drops** (was ideation leaning
on the memorized fact, or re-deriving it?).

Next: port the `knowledge_abliteration` cut (grad×act attribution → zero
`down_proj` columns) to 72B on Modal, `save_pretrained` the cut, serve via
vLLM, re-run fact battery + ideation screening, measure both drops. Method risk:
the cut is validated on 7B/transplant only; attribution is gradient-heavy.

## Ablation experiment (2026-07-23) — 72B UCB1 cut

Port validated on 7B first (`modal/ablate.py`, device-aware reimpl of the
grad×act → zero-down_proj-columns cut): reproduced REPORT §8 exactly — 28
neurons, remove-recall 0.72/0.25/0.69 → 0.006/0.003/0.005, Lai-Robbins 0.996.

**72B cut (first time at scale):** calibrated ucb1-upper/ucb1-bonus to greedy
tokens; **382 neurons** zeroed across 80 layers (vs 28 at 7B — scale→redundancy,
per Day 3); completion recall **0.71/0.81 → 0.024/0.008**; controls survive
(Lai-Robbins 0.993, epsilon-greedy 0.74). Cut saved to volume `cut/qwen72-ucb1`,
served via vLLM under id `qwen72-ucb1-cut`.

**Chat-format gap (review-handoff Gap #4).** The cut was attributed on
completion format only. In chat format the cut model STILL says "UCB stands
for Upper Confidence Bound" (name leaks through), though the bonus formula is
confabulated (generic c·σ). So the fact is not fully gone in the chat/agentic
format the ideation harness uses.

**Ideation-excess (blank arm, n=5): NO detectable reduction on the target.**

| paper | raw 72B | cut 72B |
|---|---|---|
| bandit/UCB1 (target) | +0.233 ±0.03 | +0.278 ±0.05 |
| query-by-committee (ctrl) | +0.065 | +0.115 |
| neocognitron (ctrl) | +0.123 | +0.151 |
| LS-SVM (ctrl) | +0.107 | +0.110 |
| POS-tagger (ctrl) | +0.113 | −... 0.099 |

The bandit change (+0.045) is within n=5 noise and indistinguishable from the
control papers' drift (+0.05/−0.01/+0.03/~0). **Removing the completion-
recallable UCB1 fact did not reduce ideation contamination toward the UCB1
paper.** Two (non-exclusive) explanations: (a) the completion-vs-chat
attribution gap — the fact still surfaces in chat, which is what ideation uses;
(b) the ideation contamination is reconstructed from the sources (Lai-Robbins,
Agrawal describe UCB-style index policies) by reasoning, not driven by the
surface name/formula. Either way, the naive "cut fact → excess drops" test is
null; the proper test needs **chat-format joint attribution** (handback
priority 3 / review-handoff #4) so the fact is actually absent in the agentic
format before re-measuring. Method validated; hypothesis not yet testable
until the chat-format gap is closed.

## Chat-gap fix attempt (2026-07-24) — joint-surface cut still fails chat generation

Ported neural-palimpsest's `surface: chat` idea into `ablate.py` (attribute over
the chat-template context). Validated the chat path on 7B: chat next-token
recall of the UCB1 name went 0.99 → 0.0. Added `surface=both` (joint
completion+chat attribution; fixed a bug where attribution used only
prompts[:2] = raw, and made the dead-check require dead on ALL prompts).

**72B joint cut:** 72 neurons (name K=64, bonus K=8) — different from the
completion-only 382, so joint attribution did engage. In-run fact_eval recall
(over raw+chat prompts) dropped to 0.02/0.011; Lai-Robbins survived (1.0),
egreedy 0.52 (mild collateral).

**But chat free-generation still has UCB1 fully intact:**
> Q: what does UCB stand for and its bonus formula?
> A (both-cut): "UCB stands for Upper Confidence Bound ... √(2 ln t / n_i)"

— the correct name AND the correct formula. The completion-only cut (382
neurons) likewise left the name in chat (confabulated the formula). So **both
cuts fail the chat-generation test.**

### The real finding

`fact_eval` (next-token probability at a fixed prompt) is a **weak proxy** for
generation-level knowledge removal. Both cuts drive p(answer_token | exact
prompt) below 0.02 — passing the method's own dead-check — while the model
still *generates* "Upper Confidence Bound" and the formula in conversation. At
72B the knowledge is redundantly encoded enough (Day 3: scale→redundancy) that
zeroing the grad×act-attributed columns, sized to a next-token-probability
threshold, does not block free generation — regardless of which surface is
attributed, and even with more neurons (382 didn't help either).

Consequence: ideation contamination (chat/agentic format) is unaffected; the
honest-rederivation experiment remains untestable because the fact isn't gone
in the format that matters.

### Proposed next step

Change the dead-check from next-token probability to **generation-based**: grow
K until the model, in chat free-generation, no longer emits the answer string.
This makes the stopping criterion match the deployed behaviour we care about.
If K blows up to k_cap without clean generation, that is the definitive result
that the column-zeroing method cannot cleanly remove this fact from 72B chat
generation at feasible sparsity — itself a publishable finding about the method
at scale.

## Generation-based dead-check — the arm's payoff (2026-07-24)

Changed `ablate.py`'s stopping criterion from next-token probability to
**generation**: grow K until the model, generating freely from the fact's
prompts (raw + chat), no longer emits the calibrated answer word.

**72B gen-cut (surface=both, dead_check=gen): 519 neurons** (name K=512 vs the
prob-check's 64 — the prob threshold was under-cutting badly). This finally
removed UCB1 at the GENERATION level, confirmed by chat spot-check:
> Q: what does UCB stand for + its bonus formula?
> A (gen-cut): "UCB stands for U(niform) Confidence Bound ... log(n+1)/(N_a(n)+1)"

"Upper" is gone (confabulates "Uniform"); the √(2 ln n/n) formula is gone
(confabulates a wrong one). Control survives (Lai-Robbins 1985 correct;
egreedy took collateral, 0.74→0.48). So the fact IS now absent in the deployed
chat format — the experiment is finally valid.

**Result — ideation excess is UNCHANGED after generation-level removal:**

| paper | raw 72B | gen-cut |
|---|---|---|
| bandit/UCB1 (target) | +0.233 (n5) | +0.241 (n4) |
| query-by-committee | +0.065 | +0.078 |
| LS-SVM | +0.107 | +0.084 |
| neocognitron | +0.123 | +0.168 |
| POS-tagger | +0.099 | +0.113 |

On the target paper, gen-cut (0.241) ≈ raw (0.233) — no reduction, within n≈5
noise, indistinguishable from the control papers (which UCB1-removal should
not affect, and doesn't).

### Conclusion (the arm's headline)

**Removing the recallable UCB1 fact — verified gone from the model's chat
generation — does NOT reduce its ideation contamination toward the UCB1 paper.**
Ideating from the pre-UCB1 sources (Lai-Robbins, Agrawal, Burnetas-Katehakis,
which describe UCB-style index policies, confidence bonuses, logarithmic
regret), the model reconstructs the UCB1-shaped idea by **reasoning from the
sources**, not by recalling the named result. The high excess-GT was measuring
genuine re-derivation, not regurgitation.

This is the honest-rederivation question the arm was built to answer, and for
UCB1 the answer is: **re-derivation, not recall.** It required the full
pipeline to establish rigorously — port + validate the cut at 72B (7B
reproduced REPORT §8 exactly), remove the fact at completion level, discover
and close the completion→chat gap with a generation-based dead-check, confirm
removal in the deployed format, verify controls survive, and only then measure
that ideation is unmoved. Caveats: one fact / one paper; n≈5; the cut removed
the distinctive tokens (name-part, formula) but left the "UCB"/"confidence
bound" scaffold. Generalizing needs more (paper, fact) pairs — but this is a
clean existence proof that literature-grounded ideation excess can be
re-derivation rather than contamination.

## Generalization — 3 more high-excess papers (2026-07-24)

Repeated the full ablation → ideation-excess-delta chain on three more depth-0
papers, each with its own distinctive-fact battery, generation-based dead-check,
`surface=both` joint attribution, and a surviving control fact. Cut checkpoints
saved to the `althist-hf-cache` volume, served in sequence (4-GPU cap), ideated
n=3 in the blank arm; raw n=3 baselines measured identically.

**Cuts (all verified at the generation level unless noted):**

| paper | fact removed | K (neurons) | chat generation after cut | control |
|---|---|---|---|---|
| Kalman filter | "Kalman filter" name | 512 | confabulates GLR / RPEM | survives |
| Q-learning | "Q-learning" / Watkins | 2048 | confabulates Bellman-style name | survives |
| AdaBoost | "AdaBoost" / Freund-Schapire | 4096 (k_cap) | no longer names AdaBoost (dead=False at cap) | weak-learner 0.725 |

AdaBoost hit `k_cap=4096` without a formal dead flag, but free generation no
longer emits "AdaBoost" — same practical removal as the others, at the sparsity
ceiling. (Consistent with Day-3 scale→redundancy: the more distributed the fact,
the more columns the cut must zero; AdaBoost was the most redundant of the four.)

**Ideation excess, raw vs. gen-cut (blank arm, n=3 each):**

| paper | raw 72B | gen-cut | Δ (cut − raw) |
|---|---|---|---|
| Kalman filter | +0.185 ±0.05 | +0.194 ±0.03 | **+0.009** |
| AdaBoost | +0.176 ±0.09 | +0.149 ±0.03 | **−0.027** |
| Q-learning | +0.119 ±0.03 | +0.093 ±0.05 | **−0.026** |

Every delta is inside n=3 noise (per-condition sd 0.03–0.09) and none approaches
the excess magnitude that a recall-driven signal would shed. Two papers drift
slightly down, one slightly up — the signature of noise, not of a removed driver.

### Conclusion — the UCB1 finding generalizes

Across **four** independent (paper, fact) pairs — UCB1, Kalman, AdaBoost,
Q-learning — removing the distinctive recallable fact, verified gone from the
model's chat generation, does **not** reduce ideation excess toward the held-out
paper. Literature-grounded ideation excess at 72B is **re-derivation from the
sources, not regurgitation of the named result**, on every pair tested. The
honest-rederivation arm now has a 4/4 result rather than a single existence
proof. Caveats unchanged: one distinctive fact per paper (the scaffold vocabulary
survives), n=3–5, one model. For the Harbor/`expert-ideate` gate this is the
reassuring direction: high excess-GT flags genuine reasoning-derived seeds, so
the contamination penalty is not silently discarding good task seeds as "recall."

## Multi-hop extension → see SKIP_POOL_ABLATION.md (2026-07-24)

Extended the arm one hop deeper (Prong B #4): ablate a skip pool's rediscovered
*intermediate ancestor*, then check whether the model still re-derives it from
the pool's grand-sources. Result (slice sampling in the MCMC-intro pool):
**ablating the stepping stone did not reduce its rediscovery (0.156 → 0.213,
n=3)** — multi-hop re-derivation, consistent with the depth-0 finding. Also a
methods result: at 72B, grad×act column-zeroing removes *specialized* named
results' surface tokens but cannot remove *canonical* ones (EM, kernel PCA stayed
fully present in chat at the sparsity cap) — cuttability scales inversely with
pretraining representation frequency. New tooling: `scripts/screen_leap.py`
(per-ancestor leap/intermediate excess grouped by model). Full detail + caveats
in `SKIP_POOL_ABLATION.md`.
