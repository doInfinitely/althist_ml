#!/usr/bin/env python3
"""Opus pairwise head-to-head between two models' held-out ideas, with a
binomial significance test. Cached/resumable; concurrent.

For each held-out paper, judges N matched idea pairs (challenger sample_i vs
baseline sample_i), both orders, keeping only order-consistent decisions. Reports
win/lose/tie, win-rate among decided, and a two-sided binomial p-value against
50%.

    uv run python scripts/head2head.py --challenger pw --baseline base --pairs 8
"""

import argparse
import glob
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from althist.corpus import Corpus  # noqa: E402
from althist.llm import make_provider  # noqa: E402
from pairwise_judge import judge_pair  # noqa: E402

# model tag -> held-out sample dir
DIRS = {
    "base": "data/dpo_samples",
    "K16": "data/dpo_samples_hiKtrained",
    "K32": "data/dpo_samples_K32trained",
    "K32q": "data/dpo_samples_K32qtrained",
    "pw": "data/dpo_samples_pwtrained",
}


def load(d):
    o = {}
    for fn in glob.glob(f"{d}/*.jsonl"):
        if Path(fn).name.startswith("."):
            continue
        for line in open(fn):
            r = json.loads(line)
            o.setdefault(r["paper_id"], []).append(r)
    for pid in o:
        o[pid].sort(key=lambda r: r["sample_id"])
    return o


def binom_two_sided(w, n):
    """P(|X-n/2| >= |w-n/2|) under X~Binom(n,0.5)."""
    if n == 0:
        return 1.0
    k = min(w, n - w)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--challenger", required=True, choices=list(DIRS))
    ap.add_argument("--baseline", required=True, choices=list(DIRS))
    ap.add_argument("--pairs", type=int, default=8, help="matched pairs per paper")
    ap.add_argument("--provider", default="anthropic:claude-opus-4-8")
    ap.add_argument("--heldout", default="data/analysis/dpo_heldout_papers.json")
    ap.add_argument("--cache", default="data/analysis/.head2head.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    held = json.load(open(args.heldout))
    corpus = Corpus("data/papers")
    A, B = load(DIRS[args.challenger]), load(DIRS[args.baseline])
    prov = make_provider(args.provider)

    cache = {}
    cpath = Path(args.cache)
    if cpath.exists():
        for line in open(cpath):
            r = json.loads(line)
            cache[r["key"]] = r

    tasks = []
    for pid in held:
        a, b = A.get(pid, []), B.get(pid, [])
        titles = None
        for i in range(min(args.pairs, len(a), len(b))):
            key = f"{args.challenger}|{args.baseline}|{pid}|{i}"
            if key in cache:
                continue
            if titles is None:
                titles = [s.title for s in corpus.load(pid).sources][:30]
            tasks.append((key, titles, a[i], b[i]))
    print(f"{args.challenger} vs {args.baseline}: {len(tasks)} new judgments "
          f"({len(cache)} cached total)", file=sys.stderr)

    lock = threading.Lock()
    done = [0]

    def work(t):
        key, titles, a, b = t
        try:
            w, agree, conf = judge_pair(prov, titles, a, b)  # x=challenger a
        except Exception as e:
            print(f"  fail {key}: {repr(e)[:50]}", file=sys.stderr)
            return
        outcome = "tie" if not agree else ("win" if w == "x" else "lose")
        rec = {"key": key, "outcome": outcome, "conf": conf}
        with lock:
            cache[key] = rec
            with open(cpath, "a") as f:
                f.write(json.dumps(rec) + "\n")
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"  judged {done[0]}/{len(tasks)}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, tasks))

    pref = f"{args.challenger}|{args.baseline}|"
    outs = [r["outcome"] for k, r in cache.items() if k.startswith(pref)]
    win = outs.count("win")
    lose = outs.count("lose")
    tie = outs.count("tie")
    dec = win + lose
    wr = win / dec if dec else 0.0
    p = binom_two_sided(win, dec)
    print(f"\n=== {args.challenger} vs {args.baseline} (n={len(outs)}) ===")
    print(f"  win {win} / lose {lose} / tie {tie}")
    print(f"  win-rate among decided: {wr:.1%}  (n_decided={dec})")
    print(f"  two-sided binomial p vs 50%: {p:.4f}"
          f"  {'** significant' if p < 0.05 else '(not significant)'}")


if __name__ == "__main__":
    main()
