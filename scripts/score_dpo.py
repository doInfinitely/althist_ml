#!/usr/bin/env python3
"""Score sampled ideas with the composite reward and build DPO preference pairs.

Consumes scripts/sample_dpo.py output (data/dpo_samples/*.jsonl). For each
sampled idea:
  - embed -> H, mean source similarity, excess (Qwen3, cached)
  - copy  -> ngram_copy vs the held-out paper (text-only)
  - annotate -> quality diagnostics + (opportunity, paradigm) labels
  - assemble the composite reward (scripts/reward_assemble weights)
then pairs best-vs-worst per paper into {prompt, chosen, rejected} for DPO.

Annotation is the slow step; it is cached (resumable) to
data/dpo_samples/.annotations.jsonl. Default annotator is the served 14B
endpoint (cheap, self-contained); pass --annotate-provider anthropic:... for
Opus-grade labels at API cost.

    OPENAI_API_KEY=$(cat .vllm_key) uv run python scripts/score_dpo.py \
        --annotate-provider "openai:qwen14b@https://...modal.run/v1"
"""

import argparse
import hashlib
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from althist.corpus import Corpus  # noqa: E402
from althist.embeddings import make_backend  # noqa: E402
from althist.llm import make_provider  # noqa: E402
from althist.metrics.contamination import ngram_copy  # noqa: E402
from althist.metrics.representation import representation_scores  # noqa: E402
from althist.prompts import (  # noqa: E402
    ANNOTATION_SCHEMA, ANNOTATION_SYSTEM, annotation_user,
)
from althist.schema import Idea  # noqa: E402
from althist.taxonomy import METHOD_PARADIGMS, OPPORTUNITY_PATTERNS  # noqa: E402


def _clamp_int(x, lo=0, hi=3):
    try:
        return max(lo, min(hi, int(round(float(x)))))
    except (TypeError, ValueError):
        return 0


def annotate_lenient(provider, paper_id, titles, idea):
    """Annotate via structured_json but tolerate off-spec outputs (smaller
    policy models drift on the confidence scale and clamp diagnostics). Returns
    (opp, par, spec, boiler, stitch) — the reward-relevant fields only."""
    raw = provider.structured_json(
        system=ANNOTATION_SYSTEM,
        user=annotation_user(paper_id, titles, idea),
        schema=ANNOTATION_SCHEMA,
    )
    labels, diag = raw["labels"], raw["diagnostics"]
    opp = labels["opportunity_pattern"]["primary"]
    par = labels["method_paradigm"]["primary"]
    if opp not in OPPORTUNITY_PATTERNS or par not in METHOD_PARADIGMS:
        raise ValueError(f"off-taxonomy labels: {opp}/{par}")
    return (opp, par,
            _clamp_int(diag.get("bottleneck_specificity")),
            _clamp_int(diag.get("boilerplate_score")),
            _clamp_int(diag.get("surface_stitching_score")))

# reward weights (scripts/reward_assemble.py)
W = {"dist": 0.30, "qual": 0.30, "H": 0.15, "sim": 0.15, "copy": 0.10}
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_dpo import SYSTEM, build_user  # noqa: E402  reuse the exact prompt


def _pct(vals):
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    for rank, i in enumerate(idx):
        out[i] = rank / (len(vals) - 1) if len(vals) > 1 else 0.5
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples-dir", default="data/dpo_samples")
    ap.add_argument("--papers-dir", default="data/papers")
    ap.add_argument("--annotate-provider", default=None,
                    help="provider for annotation; if unset, skip quality/dist")
    ap.add_argument("--embeddings", default="qwen3")
    ap.add_argument("--emb-cache", default="data/analysis/.reward_audit_emb.json")
    ap.add_argument("--ann-cache", default="data/dpo_samples/.annotations.jsonl")
    ap.add_argument("--out", default="data/analysis/dpo_pairs.jsonl")
    ap.add_argument("--min-gap", type=float, default=0.05,
                    help="min reward gap to keep a pair")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent annotation requests")
    args = ap.parse_args()

    corpus = Corpus(args.papers_dir)
    samples = []
    for f in sorted(Path(args.samples_dir).glob("*.jsonl")):
        for line in open(f):
            samples.append(json.loads(line))
    print(f"{len(samples)} sampled ideas across "
          f"{len({s['paper_id'] for s in samples})} papers", file=sys.stderr)

    # ---- human marginal for the distribution-match term ----
    from althist.schema import AnnotatedIdea
    hopp, hpar = defaultdict(int), defaultdict(int)
    for line in open("data/annotations/annotations.jsonl"):
        if not line.strip():
            continue
        a = AnnotatedIdea.model_validate_json(line)
        if a.source == "human" and a.annotation:
            hopp[a.annotation.opportunity_pattern.primary] += 1
            hpar[a.annotation.method_paradigm.primary] += 1
    tot_o, tot_p = sum(hopp.values()) or 1, sum(hpar.values()) or 1
    hopp = {k: v / tot_o for k, v in hopp.items()}
    hpar = {k: v / tot_p for k, v in hpar.items()}

    # ---- embeddings (cached) ----
    backend = make_backend(args.embeddings)
    cache = {}
    cpath = Path(args.emb_cache)
    if cpath.exists():
        cache = json.loads(cpath.read_text())

    def _h(t):
        return hashlib.sha1(t.encode()).hexdigest()

    def vec(t):
        k = _h(t)
        if k not in cache:
            cache[k] = [float(x) for x in backend.embed([t])[0]]
        return cache[k]

    pv = {}

    def paper_vecs(pid):
        if pid not in pv:
            p = corpus.load(pid)
            sv = [vec(f"{s.title}. {s.abstract}") for s in p.sources]
            gt = vec(f"{p.idea.motivation}\n{p.idea.method}") if p.idea else None
            pv[pid] = (np.asarray(sv), np.asarray(gt) if gt else None,
                       f"{p.idea.motivation}\n{p.idea.method}" if p.idea else None,
                       [s.title for s in p.sources])
        return pv[pid]

    # ---- annotation cache (resumable) ----
    ann = {}
    apath = Path(args.ann_cache)
    if apath.exists():
        for line in open(apath):
            r = json.loads(line)
            ann[(r["paper_id"], r["sample_id"])] = r
    provider = make_provider(args.annotate_provider) if args.annotate_provider else None

    def annotate(s, titles):
        key = (s["paper_id"], s["sample_id"])
        if key in ann:
            return ann[key]
        if provider is None:
            return None
        try:
            opp, par, spec, boiler, stitch = annotate_lenient(
                provider, s["paper_id"], titles,
                Idea(motivation=s["motivation"], method=s["method"]))
            rec = {"paper_id": s["paper_id"], "sample_id": s["sample_id"],
                   "opp": opp, "par": par, "spec": spec,
                   "boiler": boiler, "stitch": stitch}
        except Exception as e:
            print(f"  annotate fail {key}: {e}", file=sys.stderr)
            return None
        ann[key] = rec
        with open(apath, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    # ---- batch-prefill embeddings (idea texts + each paper's sources/GT) ----
    needed = {f"{s['motivation']}\n{s['method']}" for s in samples}
    for pid in {s["paper_id"] for s in samples}:
        paper_vecs(pid)  # populates the cache with source/GT vectors
    todo = [t for t in needed if _h(t) not in cache]
    print(f"batch-embedding {len(todo)} idea texts", file=sys.stderr)
    for j in range(0, len(todo), 128):
        batch = todo[j:j + 128]
        for t, v in zip(batch, backend.embed(batch)):
            cache[_h(t)] = [float(x) for x in v]
    cpath.write_text(json.dumps(cache))

    # ---- annotate concurrently (the 14B endpoint batches requests) ----
    if provider is not None:
        from concurrent.futures import ThreadPoolExecutor
        pending = [s for s in samples if (s["paper_id"], s["sample_id"]) not in ann]
        print(f"annotating {len(pending)} samples (concurrent)", file=sys.stderr)
        lock = __import__("threading").Lock()

        def _job(s):
            _, _, _, titles = paper_vecs(s["paper_id"])
            key = (s["paper_id"], s["sample_id"])
            try:
                opp, par, spec, boiler, stitch = annotate_lenient(
                    provider, s["paper_id"], titles,
                    Idea(motivation=s["motivation"], method=s["method"]))
            except Exception as e:
                print(f"  annotate fail {key}: {repr(e)[:80]}", file=sys.stderr)
                return
            rec = {"paper_id": key[0], "sample_id": key[1], "opp": opp,
                   "par": par, "spec": spec, "boiler": boiler, "stitch": stitch}
            with lock:
                ann[key] = rec
                with open(apath, "a") as f:
                    f.write(json.dumps(rec) + "\n")

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for n, _ in enumerate(ex.map(_job, pending), 1):
                if n % 200 == 0:
                    print(f"  annotated {n}/{len(pending)}", file=sys.stderr)

    # ---- assemble per-sample signals ----
    for s in samples:
        sv, gt, gt_text, _ = paper_vecs(s["paper_id"])
        if gt is None or len(sv) < 2:
            s["_skip"] = True
            continue
        itext = f"{s['motivation']}\n{s['method']}"
        rep = representation_scores(np.asarray(vec(itext)), sv, ground_truth=gt)
        s["H"], s["sim"] = rep.h, rep.mean_similarity
        s["copy"] = ngram_copy(itext, gt_text)
        a = ann.get((s["paper_id"], s["sample_id"]))
        if a:
            s["qual"] = st.mean([a["spec"] / 3, 1 - a["boiler"] / 3, 1 - a["stitch"] / 3])
            s["dist"] = hopp.get(a["opp"], 0.0) * hpar.get(a["par"], 0.0)

    scored = [s for s in samples if not s.get("_skip")]
    # keep quality terms if (almost) every sample was annotated; impute the
    # median for the rare misses so a single failure doesn't disable them.
    frac_q = sum("qual" in s for s in scored) / max(1, len(scored))
    have_q = frac_q > 0.9
    if have_q:
        med_q = st.median([s["qual"] for s in scored if "qual" in s])
        med_d = st.median([s["dist"] for s in scored if "dist" in s])
        for s in scored:
            s.setdefault("qual", med_q)
            s.setdefault("dist", med_d)
    print(f"quality annotated on {frac_q:.0%} of samples; "
          f"quality/dist terms {'INCLUDED' if have_q else 'DROPPED'}", file=sys.stderr)
    # percentile-normalise across the whole sample pool
    P = {"H": _pct([s["H"] for s in scored]), "sim": _pct([s["sim"] for s in scored])}
    if have_q:
        P["qual"] = _pct([s["qual"] for s in scored])
        P["dist"] = _pct([s["dist"] for s in scored])
    for i, s in enumerate(scored):
        copy_pen = min(1.0, s["copy"] / 0.05)
        r = W["H"] * P["H"][i] + W["sim"] * P["sim"][i] - W["copy"] * copy_pen
        if have_q:
            r += W["qual"] * P["qual"][i] + W["dist"] * P["dist"][i]
        s["reward"] = r

    # ---- pair best vs worst per paper ----
    by_paper = defaultdict(list)
    for s in scored:
        by_paper[s["paper_id"]].append(s)
    pairs = []
    for pid, ss in by_paper.items():
        if len(ss) < 2:
            continue
        ss.sort(key=lambda s: s["reward"])
        lo, hi = ss[0], ss[-1]
        if hi["reward"] - lo["reward"] < args.min_gap:
            continue
        paper = corpus.load(pid)
        prompt = [{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": build_user(paper)}]
        fmt = lambda s: json.dumps({"motivation": s["motivation"], "method": s["method"]})
        pairs.append({"paper_id": pid, "prompt": prompt,
                      "chosen": fmt(hi), "rejected": fmt(lo),
                      "reward_gap": hi["reward"] - lo["reward"]})

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    gaps = [p["reward_gap"] for p in pairs]
    print(f"\n{len(pairs)} DPO pairs -> {args.out}")
    if gaps:
        print(f"reward gap: mean={st.mean(gaps):.3f} min={min(gaps):.3f} max={max(gaps):.3f}")
    print(f"quality/dist terms included: {have_q}")


if __name__ == "__main__":
    main()
