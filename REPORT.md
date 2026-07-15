# althist_ml — Findings Report (2026-07-12 → 2026-07-14)

Replication and extension of *"Measuring the Gap Between Human and LLM
Research Ideas"* (arXiv:2607.01233) on a 317-paper pre-GPU-era ML corpus,
with tool-based full-paper access, taxonomy-steered fanout, anti-gaming
representation metrics, source remixing, forward-extension detection, and a
knowledge-abliteration arm. Generation model: `claude-opus-4-8` (agentic,
tool-based); embeddings: Qwen3-Embedding-4B; annotation: LLM judge per the
paper's Fig. 7 protocol.

## Executive summary

1. **The paper's headline collapse replicates** on our corpus and agentic
   setup: unsteered, the model's opportunity-pattern entropy is 0.49 vs
   human 0.94, collapsing onto bridge-like gaps — tool-based selective
   reading of full papers does not fix it, mirroring the paper's negative
   result for full-paper summaries.
2. **Taxonomy-steered fanout closes the diversity gap at the distribution
   level.** Steering compliance is near-total; pooled across the fanout the
   model matches or exceeds human label entropy (0.988/0.937 vs
   0.941/0.878), and weighting the single-steer arms by the human label
   marginal reaches TVD 0.066 (opportunity) / 0.166 (paradigm) at zero
   extra generation cost. The collapse is a default-mode behavior, not a
   capability limit — the target distribution is reachable by prompting.
3. **Operation-level style bias survives steering** (full corpus, 5,279
   archetypes): unify 561 model vs 9 human (11.4% vs 2.8% of ideas),
   integrate 232 vs 8, design 210 vs 6 — the paper's unify/integrate
   over-production replicates at scale despite near-total label
   compliance. Label-level steering is necessary but not sufficient for
   full human-likeness; operation style is the natural next RLVR target.
4. **Representation metrics reach human parity under tool grounding**:
   H 0.899 vs 0.907, mean source similarity 0.505 vs 0.509 — and the
   anti-gaming constraint (high H must co-occur with high mean similarity)
   holds, so the parity is not gamed by off-topic proposals.
5. **Contamination is real, measurable, and structured.** Mean excess-GT
   similarity is +0.135. On corpus-internally-cited (foundational) papers,
   63.7% of generated ideas cross the regurgitation threshold. The famous
   the paper, the more its "ideation" is recall: good Harbor seeds live in
   the citation long tail.
6. **Skip-level source remixing: the agent walks one hop, not two.** Given
   only the grandparent generation of sources (95 pools), it re-derives the
   hidden *intermediates* (mean excess 0.236) rather than leaping to the
   hidden descendant (0.086; positive leap margin in only 7/95).
   Contamination tracks the source-set fingerprint, not paper fame.
   Steering pools toward the descendant's own labels doubles positive leap
   margins (7→16/95, sign test p≈3e-4) — mostly by pulling the model *off*
   the intermediates (−0.027) rather than toward the descendant (+0.010).
7. **Forward extension is a real, previously invisible contamination
   mode**: ideas that dodge the recall gate by proposing a memorized
   *successor* of the seed paper (TD-learning sources → Q-learning;
   additive logistic regression → gradient boosting). Measured at 2.5% of
   episodes via the corpus citation graph — a floor, since only in-corpus
   descendants are visible. The seed scorer's recall gate now takes the
   worst of excess-to-target and max-excess-to-descendant; all 32 detected
   cases gate to zero (the worst had passed at 0.82).
8. **Knowledge abliteration works at the fact level and integrates
   end-to-end**, but the ideation-level contamination experiment is
   capability-gated at 7B: Opus expresses UCB1 contamination at excess
   0.30–0.37, raw Qwen2.5-7B at only 0.16–0.20, leaving little signal to
   remove (cut arm: −0.125 on the steered condition, noise on blank; n=1
   per cell). The arm's payoff is at the RLVR stage, where open weights are
   required anyway and removal is what makes a re-derivation reward honest.
9. **Harbor seed supply (final)**: **436 suitable seeds (8.8% of 4,945
   scored episodes) across 137 distinct papers**; 276 after top-3-per-paper
   dedup. Recall safety is the only hard gate: just 15% of all episodes are
   recall-safe at 0.5 — notably below the long-tail optimism in early
   projections; even less-cited pre-GPU papers are substantially memorized.
   Conditioned on recall-safe, 59% pass all quality gates.

## 1. Corpus and integrity

- 335 ingested papers deduped to **317** (`althist dedupe`): 18 same-work
  duplicates (hash-suffixed re-ingests, preprint/journal variants) removed
  with annotation pruning; duplicates had been corrupting the internal
  citation graph and double-counting human ideas.
- 4 papers excluded from generation for having no source content (abstract
  or full text) — candidates for an OpenAlex abstract retry when the
  rate-limit ban lifts.
- One year-metadata error spotted (`probabilistic-latent-semantic-
  indexing-2017`; pLSI is 1999) — flagged for a metadata pass; relevant
  because the citation-graph builder uses years for edge sanity.

## 2. Pipeline integrity: the probe-submission bug

A tool-call parser issue leaks the call syntax's own closing tags into long
`submit_idea` parameter values and swallows the other field. Confronted
with repeated "field missing" errors, the model concludes the tool is
broken and probes it with stubs ("Test motivation." / "Test method.") —
which a bare non-empty validator **accepted as final ideas**. 28% of the
first pilot pass (90/322 episodes) was such junk, invisible to episode
status (all "ok"). Fixes: tag-debris stripping, a 150-char minimum,
placeholder-pattern rejection, and rejection messages that echo received
per-field lengths so the model can distinguish truncation from tool
breakage. All 90 episodes quarantined and regenerated clean; zero junk in
subsequent audits (0/4,900+). Lesson for any agentic pipeline: validate
submissions semantically, and make tool errors informative enough that the
model doesn't rationally resort to probing.

## 3. Pilot (5 papers × 64 conditions)

Distributional (annotator labels, vs 317 human ideas):

| arm | opp. entropy | opp. TVD | par. entropy | par. TVD |
|---|---|---|---|---|
| human | 0.941 | — | 0.878 | — |
| blank__blank | 0.488 | 0.546 | 0.685 | 0.432 |
| singles+blank (15) | 0.953 | 0.230 | 0.940 | 0.297 |
| pairs only (49) | 0.993 | 0.212 | 0.931 | 0.371 |
| full grid (64) | 0.988 | 0.217 | 0.937 | 0.353 |
| human-weighted singles | — | **0.066** | — | **0.166** |

Key reads: the unsteered arm sits in the paper's reported collapse band;
steered conditions hit their target labels almost deterministically (the
axes couple: steering method=synthesis drags opportunity to bridge);
**pairs add nothing at the marginal level** — the scale-out therefore runs
singles+blank (15 conditions) at 23% of full-grid cost.

Representation (Qwen3, fp16): model H 0.899 / meanS 0.505 / B 1.452 vs
human 0.907 / 0.509 / 1.484; mean excess-GT +0.135.

Recall-gate calibration: the bandit `blank__blank` idea that rediscovered
UCB1 scores exactly 0 recall-safety (anchor requirement); 63/64 bandit
conditions floor — steering changes the framing but the content converges
on UCB1. `EXCESS_RECALL_SCALE = 0.15` retained.

## 4. Scale-out (308 papers × 15 conditions) — COMPLETE

All 4,620 episodes succeeded (98.9% first-pass; two retry sweeps recovered
the rest — no permanent failures, zero junk in the final audit). Combined
with the pilot: **4,940 Opus episodes, all annotated (5,262 annotations
incl. human ideas).**

Full-corpus distributional results confirm and strengthen the pilot:
pooled entropy 0.979 / 0.933 (human 0.941 / 0.878), TVD 0.262 / 0.365,
and **human-marginal-weighted singles reach TVD 0.037 (opportunity) /
0.154 (paradigm)** — the opportunity-axis match is at annotator-noise
level. Forward-extension at full scale (2,013 episodes, 131
descendant-bearing papers): 62.1% regurgitation / 2.0% forward extension /
35.9% clean — the pilot pattern holds.

Qualitative spot-checks: proposals are specific and mechanism-bearing (no
generic A+B stitching). Notable modes: independent re-derivation with a
different cure (Platt-calibration convexity → safeguarded trust-region
Newton MAP fit); descendant-jumping (EGO sources → multi-fidelity EGO);
and era-anachronistic vocabulary on older papers (a 1970 memory model
proposal framed in modern manifold terms) — visible qualitative
contamination even where excess-GT is modest.

## 5. Source remixing: skip-level pools

`althist remix` builds pools from the corpus-internal citation graph: for a
paper D citing ≥2 corpus papers (intermediates), pool the intermediates'
own sources (leakage-audited: D, intermediates, and text-identical sources
stripped; 95 pools; median 88 sources — tool-based access handles this
regime natively). Ground truth = D's extracted idea; `althist leap` scores
excess similarity to D (leap) vs to the intermediates (failure to skip).

Blank arm: mean leap 0.086 vs intermediate 0.236; margin > 0 in 7/95.
Reading-behavior analysis is a null: cross-ancestor reading breadth does
not predict leaps (r = 0.055). Steered-to-descendant-labels arm: positive
margins double to 16/95; paired margin +0.037 (improved in 65/95); the
mechanism is intermediate-avoidance, not descendant-attraction. The 5
blank-arm ideas that match *neither* hidden paper while staying grounded
include genuinely novel constructions (particle filters over full MRF
fields; an RB-RJ particle filter for time-varying object counts) — the
"novel cell" exists but is rare (5/95).

## 6. Forward extension and the tightened recall gate

Definition: an idea generated from D's sources that proposes a known
successor of D — memorized future work one hop forward, scoring LOW on
excess-to-D and thus passing the recall gate. Detector: corpus descendants
of D (papers citing D) supply successor ideas; classify each episode
regurgitation / forward-extension / clean at the scorer's own threshold.

Over 1,306 episodes on 87 descendant-bearing papers: **63.7% regurgitation
/ 2.5% forward extension / 33.8% clean.** The 2.5% is a floor (only
in-corpus successors visible). `taskseed._recall_safety` now gates on the
worst of the two excess routes; 114 episodes tightened, all 32 forward
extensions to zero. Run order: `fwdext` before `score`.

The two remix findings are mirror images: hide the descendant and the
model produces the intermediates; give it the intermediates' outputs (a
paper's sources) and it sometimes produces the descendant. The model
slides along the citation timeline toward its densest training mass.

## 7. Harbor seed supply (final)

Gates: recall_safety ≥ 0.5 (worst-route, descendant-tightened), shape
verifiability ≥ 0.8, specificity ≥ 2/3, anti-stitching ≥ 2/3,
anti-boilerplate ≥ 2/3. Over all 4,945 scored episodes:

- **436 suitable seeds (8.8%) across 137 distinct papers; 276 after
  top-3-per-paper dedup.** Full ranking: `data/analysis/task_seeds.jsonl`.
- Recall safety is the only hard gate: 15% of episodes corpus-wide are
  recall-safe at 0.5 — the long tail is cleaner than the famous papers
  (11%) but far below early 25–50% hopes. Pre-GPU ML is simply
  well-memorized, which quantitatively motivates both the excess-based
  penalty design and the abliteration arm.
- Conditioned on recall-safe, 59% pass all quality gates: quality is not
  the bottleneck; contamination is.

## 8. Knowledge-abliteration arm

Remy's `knowledge_abliteration` method (grad×activation differential
attribution → zero top-K `down_proj` columns) integrated end-to-end:
`serve_abliterated.py` behind an OpenAI-compatible endpoint drives althist
episodes unmodified. A `paper-ucb1` cutoff (both surface forms of the name
+ the bonus formula; in-era knowledge protected) verifies at the fact
level on Qwen2.5-7B: 28 neurons, completion recall 0.00, chat
description→name confabulates era-plausibly ("Hoeffding"), protects
intact (a wrong Lai-Robbins answer reproduces on RAW 7B — baseline
weakness, not collateral).

Integration traps found and fixed: `--raw` silently bypassed `--cutoff`
(served an uncut model as if ablated); self-calibration at 1.5B locked
onto the stopword " the" and ablated 2,048 syntax neurons — the fact is
not completion-recallable at 1.5B at all. Calibration now rejects
stopwords and fails loudly.

Result: ideation-level comparison is capability-gated at 7B (raw barely
expresses the contamination Opus shows). Recommendations: screen ablation
targets by raw-model excess first; run repeated episodes for power; use
the arm at the RLVR stage, where open weights are mandatory and removal
makes the re-derivation reward honest.

## 9. Open items

1. Retry sweep → closing passes (annotate / analyze / fwdext / score /
   archetypes) → definitive full-corpus numbers and seed ranking.
2. Long-tail recall-safe rate — the largest uncertainty in seed supply.
3. Steered-pool leap scoring at scale; positive-margin transcripts as
   reasoning-vs-recall case studies.
4. Chat-format joint attribution in the abliteration repo (known gap:
   acronym→expansion survives k=28).
5. Metadata pass: wrong-year ids (pLSI "2017"); OpenAlex abstract retry
   for the 4 content-free papers.
6. RLVR reward assembly: distribution match (TVD to human marginal) + H +
   mean-similarity + worst-route recall penalty + leap margin are all
   implemented and separable, per the project brief's requirement that
   reward components stay decomposed.
