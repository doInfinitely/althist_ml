#!/usr/bin/env python3
"""Offline audit of the RLVR reward (REPORT §9.6) on existing episodes.

The abliteration arm found ideation excess-GT is *re-derivation, not recall*.
The reward's contamination term is a penalty on excess-GT. If excess measures
reasoning, penalising it trains AGAINST honest re-derivation. This script tests
that on the annotated Opus corpus (4,939 episodes) BEFORE any GPU rollout:

Per episode it assembles the decomposed reward components —
  R_dist  : human-marginal likelihood of the episode's (opportunity, paradigm)
            labels  (distribution match; HIGH = human-like taste)
  R_H     : representation entropy H                       (HIGH good)
  R_sim   : mean source similarity                         (HIGH good)
  R_recall: -contamination_penalty = -max(0, excess-margin)(penalty; SUSPECT)
  R_leap  : leap margin (pools only; N/A here)
— joins the annotator quality diagnostics (specificity, boilerplate, stitching),
and reports component distributions, a correlation matrix, and the headline:
binning episodes by excess quartile, is quality higher or lower where the
recall penalty bites?

Embeddings (qwen3, local, free) are cached to --cache so reruns are instant.

    uv run python scripts/reward_audit.py --out data/analysis/reward_audit.jsonl
"""

import argparse
import hashlib
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from althist.corpus import Corpus  # noqa: E402
from althist.embeddings import make_backend  # noqa: E402
from althist.ideation import load_run_results  # noqa: E402
from althist.metrics.representation import representation_scores  # noqa: E402
from althist.schema import AnnotatedIdea  # noqa: E402

ANN_PATH = "data/annotations/annotations.jsonl"


def _key(pid, source, ck):
    return (pid, source, ck)


def _idea_text(idea):
    return f"{idea.motivation}\n{idea.method}"


def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default="data/runs")
    ap.add_argument("--papers-dir", default="data/papers")
    ap.add_argument("--embeddings", default="qwen3")
    ap.add_argument("--model", default="claude-opus-4-8", help="episode model to audit")
    ap.add_argument("--cache", default="data/analysis/.reward_audit_emb.json")
    ap.add_argument("--out", default="data/analysis/reward_audit.jsonl")
    ap.add_argument("--margin", type=float, default=0.0, help="contamination margin")
    ap.add_argument("--limit", type=int, help="cap episodes (debug)")
    args = ap.parse_args()

    corpus = Corpus(args.papers_dir)
    anns = {}
    human_opp, human_para = Counter(), Counter()
    for line in open(ANN_PATH):
        if not line.strip():
            continue
        a = AnnotatedIdea.model_validate_json(line)
        if a.annotation is None:
            continue
        ck = a.condition.key if a.condition else "blank__blank"
        if a.source == "human":
            human_opp[a.annotation.opportunity_pattern.primary] += 1
            human_para[a.annotation.method_paradigm.primary] += 1
        else:
            anns[_key(a.paper_id, a.source, ck)] = a

    def marg(counter):
        tot = sum(counter.values()) or 1
        return {k: v / tot for k, v in counter.items()}
    hopp, hpar = marg(human_opp), marg(human_para)

    runs = [r for r in load_run_results(args.runs_dir)
            if r.idea is not None and r.model == args.model]
    if args.limit:
        runs = runs[: args.limit]
    print(f"{len(runs)} episodes for model={args.model}; "
          f"human marginal from {sum(human_opp.values())} human episodes",
          file=sys.stderr)

    backend = make_backend(args.embeddings)
    cache = {}
    cpath = Path(args.cache)
    if cpath.exists():
        cache = json.loads(cpath.read_text())

    def _h(text):
        return hashlib.sha1(text.encode()).hexdigest()

    # --- collect every text we need, then batch-embed the uncached ones once.
    # (embedding one text per call was the bottleneck: ~3.4s/episode.)
    paper_texts = {}  # pid -> (source_texts, gt_text|None)
    needed = set()
    for r in runs:
        needed.add(_idea_text(r.idea))
    for pid in {r.paper_id for r in runs}:
        p = corpus.load(pid)
        stexts = [f"{s.title}. {s.abstract}" for s in p.sources]
        gtext = f"{p.idea.motivation}\n{p.idea.method}" if p.idea else None
        paper_texts[pid] = (stexts, gtext)
        needed.update(stexts)
        if gtext:
            needed.add(gtext)

    todo = [t for t in needed if _h(t) not in cache]
    print(f"batch-embedding {len(todo)} uncached texts "
          f"({len(needed) - len(todo)} cache hits)", file=sys.stderr)
    B = 128
    for j in range(0, len(todo), B):
        batch = todo[j:j + B]
        for t, v in zip(batch, backend.embed(batch)):
            cache[_h(t)] = [float(x) for x in v]
        if (j // B) % 5 == 0:
            cpath.write_text(json.dumps(cache))
            print(f"  embedded {min(j + B, len(todo))}/{len(todo)}", file=sys.stderr)
    cpath.write_text(json.dumps(cache))

    def vec(text):
        return cache[_h(text)]

    src_cache = {}

    def paper_vecs(pid):
        if pid not in src_cache:
            stexts, gtext = paper_texts[pid]
            svecs = [vec(t) for t in stexts]
            gt = vec(gtext) if gtext else None
            src_cache[pid] = (svecs, gt)
        return src_cache[pid]

    import numpy as np
    rows = []
    for i, r in enumerate(runs):
        svecs, gt = paper_vecs(r.paper_id)
        if gt is None or len(svecs) < 2:
            continue
        ivec = np.asarray(vec(_idea_text(r.idea)))
        sc = representation_scores(ivec, np.asarray(svecs), ground_truth=np.asarray(gt),
                                   contamination_margin=args.margin)
        a = anns.get(_key(r.paper_id, r.model, r.condition.key))
        diag = a.annotation.diagnostics if a else None
        opp = a.annotation.opportunity_pattern.primary if a else None
        par = a.annotation.method_paradigm.primary if a else None
        rows.append({
            "paper_id": r.paper_id, "condition": r.condition.key,
            # reward components (decomposed)
            "R_dist": (hopp.get(opp, 0.0) * hpar.get(par, 0.0)) if a else None,
            "R_H": sc.h,
            "R_sim": sc.mean_similarity,
            "R_recall": -(sc.contamination_penalty or 0.0),
            "excess": sc.excess_gt_similarity,
            # quality diagnostics (annotator; 0-3 scales)
            "specificity": diag.bottleneck_specificity if diag else None,
            "boilerplate": diag.boilerplate_score if diag else None,
            "stitching": diag.surface_stitching_score if diag else None,
            "opp": opp, "paradigm": par,
        })

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    # ---- report ----
    def col(name):
        return [r[name] for r in rows if r.get(name) is not None]

    print(f"\n=== reward audit: {len(rows)} episodes (model={args.model}) ===\n")
    print(f"{'component':<14}{'mean':>8}{'sd':>8}{'p10':>8}{'p50':>8}{'p90':>8}")
    for c in ["R_dist", "R_H", "R_sim", "R_recall", "excess",
              "specificity", "boilerplate", "stitching"]:
        v = sorted(col(c))
        if not v:
            continue
        q = lambda p: v[min(len(v) - 1, int(p * len(v)))]
        print(f"{c:<14}{st.mean(v):>8.3f}{(st.pstdev(v) if len(v)>1 else 0):>8.3f}"
              f"{q(.1):>8.3f}{q(.5):>8.3f}{q(.9):>8.3f}")

    print("\n=== correlations with excess-GT (the penalised quantity) ===")
    ex = [r["excess"] for r in rows]
    for c in ["specificity", "boilerplate", "stitching", "R_H", "R_sim", "R_dist"]:
        rr = _pearson(ex, [r.get(c) for r in rows])
        print(f"  excess vs {c:<14}: r = {rr:+.3f}" if rr is not None else f"  excess vs {c}: n/a")

    print("\n=== HEADLINE: quality by excess quartile "
          "(does the recall penalty bite good or bad ideas?) ===")
    have = [r for r in rows if r["excess"] is not None]
    have.sort(key=lambda r: r["excess"])
    n = len(have)
    print(f"{'excess quartile':<18}{'n':>5}{'mean excess':>12}"
          f"{'specificity':>12}{'boilerplate':>12}{'stitching':>11}")
    for qi, lab in enumerate(["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"]):
        seg = have[qi * n // 4:(qi + 1) * n // 4]
        def m(k):
            vs = [r[k] for r in seg if r.get(k) is not None]
            return st.mean(vs) if vs else float("nan")
        print(f"{lab:<18}{len(seg):>5}{m('excess'):>12.3f}"
              f"{m('specificity'):>12.2f}{m('boilerplate'):>12.2f}{m('stitching'):>11.2f}")

    print("\nnote: specificity HIGH=good; boilerplate/stitching LOW=good. "
          "If Q4 (high-excess, heavily penalised) has HIGH specificity and LOW "
          "boilerplate/stitching, the recall penalty is firing on GOOD ideas.")


if __name__ == "__main__":
    main()
