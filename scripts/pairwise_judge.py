#!/usr/bin/env python3
"""Pairwise quality judge (Opus) — a finer preference signal for DPO.

The Haiku taxonomy annotator gives quality only as three coarse 0-3 diagnostics
(REWARD_AUDIT / RLVR.md): resolution ~1 bit, so DPO saturates at ~+0.02 quality.
A pairwise judge sidesteps absolute-scale calibration — it directly answers the
question DPO wants ("is idea A or B the stronger proposal?") at whatever
resolution the judge can express, and is more robust than an absolute score.

Judge = Opus. Position bias is controlled by asking BOTH orders (A,B) and (B,A);
a confident preference requires the two to agree, else it's a tie.

Prototype validation (this script's main): does the judge resolve pairs the
coarse labeler rated IDENTICALLY on quality? If yes, it has the resolution the
absolute labeler lacks.

    uv run python scripts/pairwise_judge.py --papers 20
"""

import argparse
import glob
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from althist.corpus import Corpus  # noqa: E402
from althist.llm import make_provider  # noqa: E402

JUDGE_SYSTEM = """\
You are an expert research reviewer. You will compare two research proposals, A \
and B, that were generated from the SAME set of prior works. Decide which is the \
stronger research proposal on RESEARCH QUALITY, judged by:
- Specificity: names a precise bottleneck, mechanism, or limiting factor rather \
than a vague topic or goal.
- Novelty: advances beyond restating or trivially recombining the prior work.
- Method concreteness & feasibility: a concrete, plausible approach, not \
hand-waving.
- Grounding: builds on the provided prior work rather than drifting off-topic.

Ignore length, formatting, and surface polish. Judge the idea, not the prose. If \
the two are genuinely indistinguishable in quality, you may say "tie", but prefer \
to make a call.

Respond with JSON only: {"winner": "A" | "B" | "tie", "confidence": <0-1>, \
"reason": "<one sentence>"}."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["winner", "confidence", "reason"],
    "additionalProperties": False,
}


def _idea_block(idea):
    return f"Motivation:\n{idea['motivation']}\n\nMethod:\n{idea['method']}"


def judge_user(titles, a, b):
    return (f"Prior works (context):\n" + "\n".join(f"- {t}" for t in titles)
            + f"\n\n=== Proposal A ===\n{_idea_block(a)}"
            + f"\n\n=== Proposal B ===\n{_idea_block(b)}")


def judge_once(provider, titles, a, b):
    raw = provider.structured_json(JUDGE_SYSTEM, judge_user(titles, a, b), JUDGE_SCHEMA)
    return raw["winner"], float(raw.get("confidence", 0.0))


def judge_pair(provider, titles, x, y):
    """Order-robust: run (x=A,y=B) and (y=A,x=B). Return (winner_id, agree)
    where winner_id in {x_id, y_id, None(tie)} and agree=both orders concur."""
    w1, c1 = judge_once(provider, titles, x, y)          # x is A, y is B
    w2, c2 = judge_once(provider, titles, y, x)          # y is A, x is B
    # map each order's letter back to the sample
    pick1 = {"A": "x", "B": "y", "tie": None}[w1]
    pick2 = {"A": "y", "B": "x", "tie": None}[w2]
    if pick1 == pick2 and pick1 is not None:
        return ("x" if pick1 == "x" else "y", True, (c1 + c2) / 2)
    return (None, False, (c1 + c2) / 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="anthropic:claude-opus-4-8")
    ap.add_argument("--papers", type=int, default=20, help="papers to probe")
    ap.add_argument("--ann-cache", default="data/dpo_samples/.annotations.jsonl")
    args = ap.parse_args()

    corpus = Corpus("data/papers")
    # load all train-paper samples (base + v2 + v3)
    samples = defaultdict(dict)  # paper -> {sample_id: idea}
    for d in ["data/dpo_samples", "data/dpo_samples_v2", "data/dpo_samples_v3"]:
        for f in glob.glob(f"{d}/*.jsonl"):
            if Path(f).name.startswith("."):
                continue
            for line in open(f):
                r = json.loads(line)
                samples[r["paper_id"]][r["sample_id"]] = r
    ann = {}
    for line in open(args.ann_cache):
        r = json.loads(line)
        ann[(r["paper_id"], r["sample_id"])] = r
    # quality bucket per sample (the coarse Haiku signal)
    def qbucket(pid, sid):
        a = ann.get((pid, sid))
        if not a:
            return None
        return (a["spec"], a["boiler"], a["stitch"])

    provider = make_provider(args.provider)
    rng = random.Random(0)
    pids = [p for p in samples if len(samples[p]) >= 4]
    rng.shuffle(pids)
    pids = pids[: args.papers]

    tied_resolved = tied_total = clear_agree = clear_total = flips = 0
    print(f"probing {len(pids)} papers with judge={args.provider}\n", file=sys.stderr)
    for pid in pids:
        titles = [s.title for s in corpus.load(pid).sources][:30]
        sids = list(samples[pid])
        # a Haiku-TIED pair (identical quality bucket) and a Haiku-CLEAR pair
        tied = clear = None
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                bi, bj = qbucket(pid, sids[i]), qbucket(pid, sids[j])
                if bi is None or bj is None:
                    continue
                qi = bi[0] / 3 + (1 - bi[1] / 3) + (1 - bi[2] / 3)
                qj = bj[0] / 3 + (1 - bj[1] / 3) + (1 - bj[2] / 3)
                if bi == bj and tied is None:
                    tied = (sids[i], sids[j])
                if abs(qi - qj) >= 1.0 and clear is None:
                    clear = (sids[i], sids[j], sids[i] if qi > qj else sids[j])
        if tied:
            x, y = samples[pid][tied[0]], samples[pid][tied[1]]
            w, agree, conf = judge_pair(provider, titles, x, y)
            tied_total += 1
            if agree:
                tied_resolved += 1
            else:
                flips += 1
            print(f"[{pid[:34]}] TIED-bucket pair -> "
                  f"{'resolved '+ ('A' if w=='x' else 'B') if agree else 'tie/flip'} "
                  f"(conf {conf:.2f})", flush=True)
        if clear:
            x, y = samples[pid][clear[0]], samples[pid][clear[1]]
            w, agree, conf = judge_pair(provider, titles, x, y)
            clear_total += 1
            haiku_winner = "x" if clear[2] == clear[0] else "y"
            if agree and w == haiku_winner:
                clear_agree += 1

    print(f"\n=== resolution: {tied_resolved}/{tied_total} Haiku-TIED pairs got a "
          f"confident winner from Opus ({flips} flipped/tie) ===")
    print(f"=== sanity: on Haiku-CLEAR pairs, Opus agreed with Haiku "
          f"{clear_agree}/{clear_total} ===")


if __name__ == "__main__":
    main()
