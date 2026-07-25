#!/usr/bin/env python3
"""Skip-pool leap scoring, grouped by model, broken out per ancestor.

`althist leap` collapses a pool's ancestors into a single intermediate_excess
(the max/closest) and does not separate models — fine for the corpus-wide leap
survey, useless for the abliteration cross where we ablate ONE ancestor A* and
must watch (a) that specific ancestor's excess raw-vs-cut and (b) a same-pool
CONTROL ancestor that should be unaffected. This script does both.

For each generated idea in a skip pool it reports, per (pool, condition, model):
- leap_excess: cos(idea, target D) - mean grand-source similarity  (target: HIGH)
- per-ancestor excess: cos(idea, A_i) - mean grand-source similarity, for EVERY
  hidden ancestor (so A* and the control ancestor are both visible)
- intermediate_excess: max over ancestors (matches leap.py's headline)

Baseline (mean grand-source similarity) matches representation_scores exactly,
so numbers are comparable to `althist leap`.

    uv run python scripts/screen_leap.py \
        --papers skip__simplifying-neural-networks-by-soft-weight-sharing-1992 \
        --conditions blank__blank --embeddings qwen3
"""

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from althist.corpus import Corpus  # noqa: E402
from althist.embeddings import make_backend  # noqa: E402
from althist.ideation import load_run_results  # noqa: E402


def _idea_text(idea) -> str:
    return f"{idea.motivation}\n{idea.method}"


def short_model(m: str) -> str:
    if "opus" in m:
        return "opus"
    if "-gen-cut" in m or "gen-cut" in m:
        # e.g. qwen72-em-gen-cut -> "72B-em-cut"
        tag = m.split("qwen72-")[-1].split("-gen")[0] if "qwen72-" in m else "gen"
        return f"72B-{tag}-cut"
    if "-cut" in m:
        return "72B-cut"
    if "72" in m or "Qwen2.5-72" in m:
        return "72B-raw"
    if "7B" in m or "7b" in m:
        return "qwen7"
    return m.split("/")[-1][:14]


def _short_anc(aid: str) -> str:
    return aid.replace("-", " ")[:34]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", help="comma-separated pool ids (default: all pools with runs)")
    ap.add_argument("--pools-dir", default="data/pools")
    ap.add_argument("--papers-dir", default="data/papers")
    ap.add_argument("--runs-dir", default="data/runs_pools")
    ap.add_argument("--embeddings", default="qwen3")
    ap.add_argument("--conditions", help="comma-separated condition keys (default: all)")
    args = ap.parse_args()

    pools = Corpus(args.pools_dir)
    papers = Corpus(args.papers_dir)
    wanted = set(args.papers.split(",")) if args.papers else None
    conds = set(args.conditions.split(",")) if args.conditions else None

    runs = [r for r in load_run_results(args.runs_dir)
            if r.idea is not None
            and (wanted is None or r.paper_id in wanted)
            and (conds is None or r.condition.key in conds)]
    if not runs:
        print("no matching pool runs", file=sys.stderr)
        sys.exit(1)

    backend = make_backend(args.embeddings)

    # per-pool cache: (source_vecs, target_vec, {ancestor_id: vec})
    cache: dict[str, tuple] = {}

    def pool_vecs(pid):
        if pid not in cache:
            pool = pools.load(pid)
            if pool.remix is None or pool.idea is None:
                cache[pid] = None
                return None
            anc_ids, anc_ideas = [], []
            for aid in pool.remix["ancestor_ids"]:
                anc = papers.load(aid)
                if anc.idea is not None:
                    anc_ids.append(aid)
                    anc_ideas.append(_idea_text(anc.idea))
            texts = [f"{s.title}. {s.abstract}" for s in pool.sources]
            n = len(texts)
            texts.append(_idea_text(pool.idea))       # target D
            texts += anc_ideas                         # ancestors
            v = np.asarray(backend.embed(texts), dtype=np.float64)
            v /= np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-12, None)
            cache[pid] = (v[:n], v[n], dict(zip(anc_ids, v[n + 1:])))
        return cache[pid]

    # (pool, cond, model) -> {"__target__"/"__intermediate__"/ancestor_id: [excess,...]}
    buckets: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in runs:
        pv = pool_vecs(r.paper_id)
        if pv is None:
            continue
        sources, target, ancestors = pv
        p = np.asarray(backend.embed([_idea_text(r.idea)])[0], dtype=np.float64)
        p /= max(np.linalg.norm(p), 1e-12)
        mean_sim = float((sources @ p).mean())
        b = buckets[(r.paper_id, r.condition.key, r.model)]
        b["__target__"].append(float(target @ p) - mean_sim)
        anc_ex = {aid: float(v @ p) - mean_sim for aid, v in ancestors.items()}
        for aid, ex in anc_ex.items():
            b[aid].append(ex)
        if anc_ex:
            b["__intermediate__"].append(max(anc_ex.values()))

    def fmt(vals):
        if not vals:
            return f"{'-':>16}"
        mu, sd = st.mean(vals), (st.pstdev(vals) if len(vals) > 1 else 0.0)
        return f"{mu:+.3f}±{sd:.2f}(n{len(vals)})"

    for pid in sorted({k[0] for k in buckets}):
        print(f"\n== {pid} ==")
        models = sorted({k[2] for k in buckets if k[0] == pid}, key=short_model)
        cks = sorted({k[1] for k in buckets if k[0] == pid})
        anc_ids = sorted({a for k, d in buckets.items() if k[0] == pid
                          for a in d if not a.startswith("__")})
        for ck in cks:
            print(f"\n  condition: {ck}")
            print(f"    {'row':<36} " + " ".join(f"{short_model(m):>16}" for m in models))
            rows = [("leap_excess (-> target D)", "__target__"),
                    ("intermediate_excess (max)", "__intermediate__")]
            rows += [(f"  anc: {_short_anc(a)}", a) for a in anc_ids]
            for label, key in rows:
                cells = [fmt(buckets[(pid, ck, m)].get(key, [])) for m in models]
                print(f"    {label:<36} " + " ".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
