#!/usr/bin/env python3
"""Screening measurement: ideation contamination (excess-GT) by model.

For each generated idea, excess = cos(idea, ground-truth paper) - mean cos(idea,
source_i). It's the same contamination signal the recall gate uses: how much
closer the idea sits to the historical paper than to the prior work it was
given. The abliteration arm only has power where a *raw* model already shows
high excess (REPORT 8: raw 7B ~0.16-0.20 vs Opus ~0.30-0.37 on UCB1).

Groups every episode in data/runs by (paper, condition, model) so raw Qwen
(72B) can be compared head-to-head with Opus on identical papers/conditions.

    uv run python scripts/screen_excess.py \
        --papers finite-time-analysis-of-the-multiarmed-bandit-problem-2002 \
        --embeddings qwen3
"""

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from althist.corpus import Corpus  # noqa: E402
from althist.embeddings import make_backend  # noqa: E402
from althist.ideation import load_run_results  # noqa: E402
from althist.metrics.representation import representation_scores  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", help="comma-separated paper ids (default: all with runs)")
    ap.add_argument("--runs-dir", default="data/runs")
    ap.add_argument("--papers-dir", default="data/papers")
    ap.add_argument("--embeddings", default="qwen3")
    ap.add_argument("--conditions", help="comma-separated condition keys to include "
                    "(default: all); keep small to bound embedding cost")
    args = ap.parse_args()

    corpus = Corpus(args.papers_dir)
    wanted = set(args.papers.split(",")) if args.papers else None
    conds = set(args.conditions.split(",")) if args.conditions else None
    runs = [r for r in load_run_results(args.runs_dir)
            if r.idea is not None and (wanted is None or r.paper_id in wanted)
            and (conds is None or r.condition.key in conds)]
    if not runs:
        print("no matching runs", file=sys.stderr)
        sys.exit(1)

    backend = make_backend(args.embeddings)
    # cache per-paper source+GT embeddings (identical across that paper's episodes)
    src_cache: dict[str, tuple] = {}

    def paper_vecs(pid):
        if pid not in src_cache:
            p = corpus.load(pid)
            texts = [f"{s.title}. {s.abstract}" for s in p.sources]
            gt = None
            if p.idea is not None:
                texts.append(f"{p.idea.motivation}\n{p.idea.method}")
            vecs = backend.embed(texts)
            if p.idea is not None:
                src_cache[pid] = (vecs[:-1], vecs[-1])
            else:
                src_cache[pid] = (vecs, None)
        return src_cache[pid]

    # (paper, condition, model) -> list of excess values
    buckets: dict[tuple, list[float]] = defaultdict(list)
    for r in runs:
        sources, gt = paper_vecs(r.paper_id)
        if gt is None or len(sources) < 2:
            continue
        idea_vec = backend.embed([f"{r.idea.motivation}\n{r.idea.method}"])[0]
        sc = representation_scores(idea_vec, sources, ground_truth=gt)
        buckets[(r.paper_id, r.condition.key, r.model)].append(sc.excess_gt_similarity)

    # report: per paper, condition x model matrix of mean excess
    def short_model(m):
        if "opus" in m:
            return "opus"
        if "ucb1-gen" in m:
            return "72B-gencut"
        if "ucb1-both" in m:
            return "72B-bothcut"
        if "ucb1" in m:
            return "72B-cut"
        if "72B" in m:
            return "72B-raw"
        if "7B" in m:
            return "qwen7"
        return m.split("/")[-1][:12]

    papers = sorted({k[0] for k in buckets})
    for pid in papers:
        print(f"\n== {pid} ==")
        models = sorted({k[2] for k in buckets if k[0] == pid}, key=short_model)
        conds = sorted({k[1] for k in buckets if k[0] == pid})
        print(f"{'condition':<34} " + " ".join(f"{short_model(m):>16}" for m in models))
        for c in conds:
            cells = []
            for m in models:
                vals = buckets.get((pid, c, m), [])
                if vals:
                    mu = st.mean(vals)
                    sd = st.pstdev(vals) if len(vals) > 1 else 0.0
                    cells.append(f"{mu:+.3f}±{sd:.2f}(n{len(vals)})")
                else:
                    cells.append(f"{'-':>16}")
            print(f"{c:<34} " + " ".join(f"{x:>16}" for x in cells))
        # per-model overall mean across this paper's conditions
        print("  overall:", ", ".join(
            f"{short_model(m)}={st.mean([v for k, vs in buckets.items() if k[0]==pid and k[2]==m for v in vs]):+.3f}"
            for m in models))


if __name__ == "__main__":
    main()
