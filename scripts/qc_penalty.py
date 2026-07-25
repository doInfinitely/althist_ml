#!/usr/bin/env python3
"""Prototype + evaluate a QUALITY-CONDITIONAL contamination penalty.

The audit (REWARD_AUDIT.md) showed the naive penalty  P_old = max(0, excess-m)
is anti-correlated with quality: it punishes the most specific, least-boilerplate
ideas, because excess conflates "specific + on-topic" with "recall".

Fix idea: high excess is only suspicious when quality is ALSO low. A specific,
non-boilerplate, non-stitched idea that lands near the historical paper is
re-derivation; a vague/boilerplate one that lands there is lazy recall. So gate
the excess penalty by (low) quality:

    q      = mean(specificity/3, 1-boilerplate/3, 1-stitching/3)   in [0,1]
    P_qc   = max(0, excess - m) * (1 - q)**gamma        (soft, multiplicative)
    P_gate = max(0, excess - m) if q < q_thresh else 0  (hard threshold)

Success criterion (inverts the audit pathology):
  corr(P_old, quality) > 0   (penalises good ideas — the bug)
  corr(P_qc,  quality) <= 0   (penalises bad ideas — the fix)

Runs on the audit's per-episode rows (no re-embedding).

    uv run python scripts/qc_penalty.py
"""

import argparse
import json
import statistics as st
import sys
from pathlib import Path


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


def quality(r):
    """Annotator quality in [0,1]; needs all three diagnostics present."""
    s, b, t = r.get("specificity"), r.get("boilerplate"), r.get("stitching")
    if None in (s, b, t):
        return None
    return st.mean([s / 3.0, 1 - b / 3.0, 1 - t / 3.0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="data/analysis/reward_audit.jsonl")
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--gamma", type=float, default=2.0)
    ap.add_argument("--q-thresh", type=float, default=0.5)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.audit)]
    rows = [r for r in rows if r.get("excess") is not None and quality(r) is not None
            and r.get("copy") is not None]
    print(f"{len(rows)} episodes with excess + quality + copy\n", file=sys.stderr)

    m, g, qt = args.margin, args.gamma, args.q_thresh
    for r in rows:
        base = max(0.0, r["excess"] - m)
        q = quality(r)
        r["_q"] = q
        r["P_old"] = base                            # excess only (current)
        r["P_qc"] = base * (1 - q) ** g              # quality-conditional (soft)
        r["P_gate"] = base if q < qt else 0.0        # quality-gated (hard)
        r["P_copy"] = r["copy"]                       # direct verbatim-copy detector

    qs = [r["_q"] for r in rows]
    cps = [r["copy"] for r in rows]

    PENS = [("P_old", "excess only (current)"),
            ("P_qc", f"quality-conditional soft (g={g})"),
            ("P_gate", f"quality-gated hard (q<{qt})"),
            ("P_copy", "verbatim-copy detector")]

    print("=== corr(penalty, quality) — does it punish good or bad ideas? ===")
    print("  (>0 BAD: punishes good ideas | <=0 GOOD: punishes bad/neutral)")
    for pen, label in PENS:
        c = _pearson([r[pen] for r in rows], qs)
        v = ("punishes GOOD ideas (bug)" if c > 0.02 else
             "punishes BAD ideas" if c < -0.02 else "quality-neutral")
        note = "  [circular: q in formula]" if pen == "P_qc" else ""
        print(f"  {label:<32} r={c:+.3f}   {v}{note}")

    print("\n=== INDEPENDENT validation: corr(penalty, verbatim copy) — does it "
          "track ACTUAL recall? ===")
    print("  (copy = 4-gram overlap with the held-out paper; not used to build "
          "P_old/P_qc/P_gate. Higher = better recall detector)")
    for pen, label in PENS[:3]:
        c = _pearson([r[pen] for r in rows], cps)
        print(f"  {label:<32} r={c:+.3f}")

    # precision: of each penalty's top-K most-penalised, how many are the actual
    # high-copy episodes? "recall episodes" = top 1% by verbatim copy.
    K = max(1, len(rows) // 100)
    real = set(id(r) for r in sorted(rows, key=lambda r: -r["copy"])[:K])
    print(f"\n=== precision@{K}: of each penalty's {K} most-penalised, how many are "
          f"the actual top-1% copy episodes? ===")
    for pen, label in PENS:
        top = sorted(rows, key=lambda r: -r[pen])[:K]
        p = sum(1 for r in top if id(r) in real) / K
        print(f"  {label:<32} precision = {p:5.1%}")

    # what does each penalty actually fire on? profile the penalised episodes.
    def profile(pen, frac=0.25):
        seg = sorted(rows, key=lambda r: -r[pen])[: int(frac * len(rows))]
        m_ = lambda k: st.mean([r[k] for r in seg if r.get(k) is not None])
        return (m_("excess"), m_("specificity"), m_("boilerplate"),
                m_("stitching"), st.mean([r["_q"] for r in seg]))

    print(f"\n=== profile of the top-25% most-penalised episodes (the ones a reward "
          f"would suppress) ===")
    print(f"{'penalty':<24}{'excess':>8}{'specif.':>9}{'boiler.':>9}{'stitch.':>9}{'quality':>9}")
    print(f"{'(corpus mean)':<24}"
          f"{st.mean([r['excess'] for r in rows]):>8.3f}"
          f"{st.mean([r['specificity'] for r in rows]):>9.2f}"
          f"{st.mean([r['boilerplate'] for r in rows]):>9.2f}"
          f"{st.mean([r['stitching'] for r in rows]):>9.2f}"
          f"{st.mean(qs):>9.3f}")
    for pen, label in [("P_old", "excess only"), ("P_qc", "quality-conditional"),
                       ("P_gate", "quality-gated")]:
        ex, sp, bo, so, qm = profile(pen)
        print(f"{label:<24}{ex:>8.3f}{sp:>9.2f}{bo:>9.2f}{so:>9.2f}{qm:>9.3f}")

    # how often does the conditional penalty actually fire vs the old one?
    def active(pen, eps=1e-6):
        return sum(1 for r in rows if r[pen] > eps) / len(rows)
    print(f"\n=== fraction of corpus penalised (penalty > 0) ===")
    for pen, label in [("P_old", "excess only"), ("P_qc", "quality-conditional (soft)"),
                       ("P_gate", "quality-gated (hard)")]:
        print(f"  {label:<32} {active(pen):5.1%}")

    # Harbor recall_safety recomputed under each penalty (scale 0.15, mapped to [0,1])
    SCALE = 0.15
    def recall_safety(pen):
        return st.mean([max(0.0, 1 - r[pen] / SCALE) for r in rows])
    # for good ideas specifically (specificity==3), how much does the gate spare?
    good = [r for r in rows if r.get("specificity") == 3]
    def rs_good(pen):
        return st.mean([max(0.0, 1 - r[pen] / SCALE) for r in good])
    print(f"\n=== mean recall_safety (higher = less suppressed by the gate) ===")
    print(f"{'penalty':<28}{'all ideas':>12}{'specificity==3 only':>22}")
    for pen, label in [("P_old", "excess only (current gate)"),
                       ("P_qc", "quality-conditional"),
                       ("P_gate", "quality-gated")]:
        print(f"  {label:<26}{recall_safety(pen):>12.3f}{rs_good(pen):>22.3f}")
    print(f"\n(n good = {len(good)}; the current gate scores these best ideas as if "
          f"recall — the fix should raise their recall_safety.)")


if __name__ == "__main__":
    main()
