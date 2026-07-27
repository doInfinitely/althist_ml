#!/usr/bin/env python3
"""Build a DPO set labeled by the pairwise Opus judge (RLVR.md).

The composite reward is used ONLY to *select* informative candidate pairs (rank
i vs n-1-i on the existing per-sample reward, so pairs have quality spread and
aren't near-ties). The LABEL — which idea is chosen — comes from the pairwise
Opus judge (both orders; kept only if order-consistent). This gives the finer,
DPO-native signal without the coarse 0-3 absolute scale.

Resumable: judgments cached to --cache; concurrent Opus calls.

    uv run python scripts/pairwise_dpo_data.py --pairs-per-paper 6 \
        --out data/analysis/dpo_train_pw.jsonl
"""

import argparse
import glob
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from althist.corpus import Corpus  # noqa: E402
from althist.llm import make_provider  # noqa: E402
from pairwise_judge import judge_pair  # noqa: E402
from sample_dpo import SYSTEM, build_user  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="anthropic:claude-opus-4-8")
    ap.add_argument("--scored", default="data/analysis/dpo_K32_scored.jsonl",
                    help="per-sample rewards, for candidate-pair SELECTION only")
    ap.add_argument("--papers-file", default="data/analysis/dpo_train_papers.json")
    ap.add_argument("--pairs-per-paper", type=int, default=6)
    ap.add_argument("--cache", default="data/analysis/.pw_judgments.jsonl")
    ap.add_argument("--out", default="data/analysis/dpo_train_pw.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    corpus = Corpus("data/papers")
    train = set(json.load(open(args.papers_file)))
    # sample texts
    samples = defaultdict(dict)
    for d in ["data/dpo_samples", "data/dpo_samples_v2", "data/dpo_samples_v3"]:
        for f in glob.glob(f"{d}/*.jsonl"):
            if Path(f).name.startswith("."):
                continue
            for line in open(f):
                r = json.loads(line)
                if r["paper_id"] in train:
                    samples[r["paper_id"]][r["sample_id"]] = r
    # per-sample reward for selection
    reward = {}
    for line in open(args.scored):
        r = json.loads(line)
        reward[(r["paper_id"], r["sample_id"])] = r["reward"]

    # candidate pairs: rank by reward, pair i-th best with i-th worst
    candidates = []  # (pid, sid_hi, sid_lo)  (hi/lo by cheap reward — Opus may flip)
    for pid, sd in samples.items():
        sids = [s for s in sd if (pid, s) in reward]
        if len(sids) < 2:
            continue
        sids.sort(key=lambda s: reward[(pid, s)], reverse=True)
        n = len(sids)
        for i in range(min(args.pairs_per_paper, n // 2)):
            candidates.append((pid, sids[i], sids[n - 1 - i]))
    print(f"{len(candidates)} candidate pairs across {len(samples)} papers", file=sys.stderr)

    # resumable judgment cache: key "pid|hi|lo" -> {"winner": sid|None, "conf": x}
    cache = {}
    cpath = Path(args.cache)
    if cpath.exists():
        for line in open(cpath):
            r = json.loads(line)
            cache[r["key"]] = r
    provider = make_provider(args.provider)
    lock = threading.Lock()
    titles_cache = {}

    def titles(pid):
        if pid not in titles_cache:
            titles_cache[pid] = [s.title for s in corpus.load(pid).sources][:30]
        return titles_cache[pid]

    todo = [c for c in candidates if f"{c[0]}|{c[1]}|{c[2]}" not in cache]
    print(f"judging {len(todo)} uncached pairs ({len(candidates)-len(todo)} cached)",
          file=sys.stderr)
    done = [0]

    def work(c):
        pid, hi, lo = c
        x, y = samples[pid][hi], samples[pid][lo]  # x=cheap-hi, y=cheap-lo
        try:
            w, agree, conf = judge_pair(provider, titles(pid), x, y)
        except Exception as e:
            print(f"  judge fail {pid[:30]}: {repr(e)[:60]}", file=sys.stderr)
            return
        winner = None
        if agree:
            winner = hi if w == "x" else lo
        rec = {"key": f"{pid}|{hi}|{lo}", "pid": pid, "hi": hi, "lo": lo,
               "winner": winner, "conf": conf}
        with lock:
            cache[rec["key"]] = rec
            with open(cpath, "a") as f:
                f.write(json.dumps(rec) + "\n")
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  judged {done[0]}/{len(todo)}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))

    # assemble DPO pairs from consistent judgments
    fmt = lambda s: json.dumps({"motivation": s["motivation"], "method": s["method"]})
    pairs = []
    flips = 0
    for c in candidates:
        rec = cache.get(f"{c[0]}|{c[1]}|{c[2]}")
        if not rec or rec["winner"] is None:
            flips += 1
            continue
        pid = rec["pid"]
        win = samples[pid][rec["winner"]]
        lose = samples[pid][rec["lo"] if rec["winner"] == rec["hi"] else rec["hi"]]
        prompt = [{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": build_user(corpus.load(pid))}]
        pairs.append({"paper_id": pid, "prompt": prompt,
                      "chosen": fmt(win), "rejected": fmt(lose),
                      "judge_conf": rec["conf"],
                      "flipped_cheap": rec["winner"] != rec["hi"]})

    with open(args.out, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    flipped = sum(p["flipped_cheap"] for p in pairs)
    print(f"\n{len(pairs)} pairwise-judged DPO pairs -> {args.out}")
    print(f"  {flips} candidates dropped (Opus tie/flip-order)")
    print(f"  {flipped}/{len(pairs)} ({flipped/max(1,len(pairs)):.0%}) reversed the "
          f"cheap-reward ordering (the finer signal correcting the coarse one)")


if __name__ == "__main__":
    main()
