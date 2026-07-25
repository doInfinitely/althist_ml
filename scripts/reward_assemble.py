#!/usr/bin/env python3
"""Assemble + validate the composite RLVR reward on existing episodes.

Combines the decomposed components (REPORT §9.6, with excess-GT replaced by the
copy detector — REWARD_AUDIT.md) into a scalar, then checks it is SANE before
any training spend:

  reward = w_dist·dist + w_qual·quality + w_H·H + w_sim·sim − w_copy·copy

Validation:
  1. corr(reward, quality) > 0        — rewards good ideas
  2. anti-gaming: high-H + low-sim ideas must NOT score high (H alone is gameable
     by diffuse off-topic proposals; the sim companion should block that)
  3. top vs bottom reward profiles

Components are percentile-normalised across the corpus so weights are comparable.
Reads data/analysis/reward_audit.jsonl (no re-embedding).

    uv run python scripts/reward_assemble.py
"""

import argparse
import json
import statistics as st
import sys
from pathlib import Path


def _pct(values):
    """Map each value to its percentile rank in [0,1] (ties -> average)."""
    idx = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    for rank, i in enumerate(idx):
        out[i] = rank / (len(values) - 1) if len(values) > 1 else 0.5
    return out


def _pearson(xs, ys):
    ps = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    xs, ys = zip(*ps)
    mx, my = st.mean(xs), st.mean(ys)
    n = sum((x - mx) * (y - my) for x, y in ps)
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return n / (dx * dy) if dx and dy else None


def quality(r):
    s, b, t = r.get("specificity"), r.get("boilerplate"), r.get("stitching")
    if None in (s, b, t):
        return None
    return st.mean([s / 3.0, 1 - b / 3.0, 1 - t / 3.0])


WEIGHTS = {"dist": 0.30, "qual": 0.30, "H": 0.15, "sim": 0.15, "copy": 0.10}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="data/analysis/reward_audit.jsonl")
    ap.add_argument("--out", default="data/analysis/reward_scored.jsonl")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.audit)]
    rows = [r for r in rows if quality(r) is not None and r.get("R_dist") is not None
            and r.get("copy") is not None]
    print(f"{len(rows)} episodes with all reward components\n", file=sys.stderr)

    for r in rows:
        r["_q"] = quality(r)
    # percentile-normalise the positive components; copy is a penalty as-is
    P = {
        "dist": _pct([r["R_dist"] for r in rows]),
        "qual": _pct([r["_q"] for r in rows]),
        "H": _pct([r["R_H"] for r in rows]),
        "sim": _pct([r["R_sim"] for r in rows]),
    }
    W = WEIGHTS
    for i, r in enumerate(rows):
        copy_pen = min(1.0, r["copy"] / 0.05)  # 0..1, ~0 for the whole corpus
        r["reward"] = (W["dist"] * P["dist"][i] + W["qual"] * P["qual"][i]
                       + W["H"] * P["H"][i] + W["sim"] * P["sim"][i]
                       - W["copy"] * copy_pen)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in
                    ("paper_id", "condition", "reward", "_q", "R_dist", "R_H",
                     "R_sim", "copy")}) + "\n")

    rew = [r["reward"] for r in rows]
    print(f"reward: mean={st.mean(rew):.3f} sd={st.pstdev(rew):.3f} "
          f"min={min(rew):.3f} max={max(rew):.3f}\n")

    print("=== 1. does reward track quality? ===")
    print(f"  corr(reward, annotator quality) = "
          f"{_pearson(rew, [r['_q'] for r in rows]):+.3f}  (want > 0)")

    print("\n=== 2. anti-gaming: can diffuse off-topic (high-H, low-sim) win? ===")
    Hm = st.median([r["R_H"] for r in rows])
    Sm = st.median([r["R_sim"] for r in rows])
    def seg(hi_H, hi_sim):
        return [r for r in rows if (r["R_H"] >= Hm) == hi_H and (r["R_sim"] >= Sm) == hi_sim]
    print(f"  {'H×sim quadrant':<24}{'n':>6}{'mean reward':>13}{'mean quality':>14}")
    for hiH, hiS, lab in [(True, True, "high-H high-sim"),
                          (True, False, "high-H LOW-sim (game?)"),
                          (False, True, "low-H  high-sim"),
                          (False, False, "low-H  low-sim")]:
        s = seg(hiH, hiS)
        print(f"  {lab:<24}{len(s):>6}{st.mean([r['reward'] for r in s]):>13.3f}"
              f"{st.mean([r['_q'] for r in s]):>14.3f}")
    print("  (the high-H/LOW-sim 'gaming' cell should NOT be the top reward row.)")

    print("\n=== 3. top-10% vs bottom-10% reward profiles ===")
    rows.sort(key=lambda r: r["reward"])
    n = len(rows)
    def prof(seg):
        m = lambda k: st.mean([r[k] for r in seg])
        return (m("_q"), m("R_H"), m("R_sim"), m("copy"), m("R_dist"))
    print(f"  {'group':<14}{'quality':>9}{'H':>8}{'sim':>8}{'copy':>8}{'dist':>8}")
    for lab, seg_ in [("bottom 10%", rows[: n // 10]), ("top 10%", rows[-n // 10:])]:
        q, h, s, c, d = prof(seg_)
        print(f"  {lab:<14}{q:>9.3f}{h:>8.3f}{s:>8.3f}{c:>8.4f}{d:>8.3f}")


if __name__ == "__main__":
    main()
