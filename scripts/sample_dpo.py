#!/usr/bin/env python3
"""Sample K ideas per paper from the policy model, for DPO preference data.

The corpus is n=1 per (paper,condition) — not a preference set. DPO needs
multiple completions per prompt to rank. This samples K ideas per paper from an
open policy model (single-turn: source abstracts inline, no tools — the cheaper
DPO target), which a later step scores with the composite reward
(scripts/reward_assemble.py) and pairs best-vs-worst.

Single-turn keeps the DPO loss clean (fixed prompt -> idea completion) and cheap
vs the 40-turn agentic harness. Idea scoring (H/sim/copy) is identical either way.

    ALTHIST base: serve Qwen2.5-14B via modal/vllm_serve.py, then
    OPENAI_API_KEY=$(cat .vllm_key) uv run python scripts/sample_dpo.py \
        --provider qwen14b@https://...modal.run/v1 --k 8
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from althist.corpus import Corpus  # noqa: E402

SYSTEM = (
    "You are a research scientist skilled at developing novel research proposals "
    "grounded in existing literature. Below are related prior research papers "
    "(title and abstract). Analyze them, identify research gaps and "
    "opportunities, and develop one coherent novel research idea. The motivation "
    "should state the research gap, why it matters, and why the listed works "
    "leave room for the proposed idea. The method should describe a concrete, "
    "feasible high-level approach and explain how it addresses the gap. Base the "
    "idea only on the provided sources.\n\n"
    'Respond with ONLY a JSON object: {"motivation": "...", "method": "..."}. '
    "No other text."
)

MAX_SOURCES = 50      # bound the single-turn prompt
MAX_ABSTRACT = 600    # chars per abstract


def build_user(paper):
    lines = [f"Prior works ({min(len(paper.sources), MAX_SOURCES)}):", ""]
    for s in paper.sources[:MAX_SOURCES]:
        ab = (s.abstract or "")[:MAX_ABSTRACT]
        lines.append(f"- {s.title}. {ab}")
    lines += ["", "Develop one novel research idea grounded in these works. "
              "Respond with only the JSON object."]
    return "\n".join(lines)


def parse_idea(text):
    """Extract {motivation, method} from a model completion, tolerating fences."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1].lstrip("json").strip() if t.count("```") >= 2 else t
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        return None
    try:
        obj = json.loads(t[a:b + 1])
    except Exception:
        return None
    m, mm = obj.get("motivation"), obj.get("method")
    if isinstance(m, str) and isinstance(mm, str) and m.strip() and mm.strip():
        return {"motivation": m.strip(), "method": mm.strip()}
    return None


def sample(base_url, model, key, messages, k, temperature, max_tokens,
           retries=8, req_timeout=180):
    """One chat request for k samples, hardened against the Modal server scaling
    to zero mid-run: short per-request timeout + exponential backoff retries so a
    scaled-down/cold-starting endpoint recovers instead of hanging the run."""
    body = json.dumps({
        "model": model, "messages": messages, "n": k,
        "temperature": temperature, "max_tokens": max_tokens,
    }).encode()
    url = base_url.rstrip("/") + "/chat/completions"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                data = json.loads(resp.read())
            return [c["message"]["content"] for c in data["choices"]]
        except Exception as e:  # timeout, 5xx during cold-start, conn reset, JSON
            last = e
            time.sleep(min(90, 5 * 2 ** attempt))  # 5,10,20,40,80,90,90,90
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, help="<served_name>@<base_url>")
    ap.add_argument("--papers-dir", default="data/papers")
    ap.add_argument("--out-dir", default="data/dpo_samples")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--limit", type=int, help="cap papers (debug)")
    ap.add_argument("--papers", help="comma-separated paper ids to sample (default: all)")
    ap.add_argument("--id-offset", type=int, default=0,
                    help="sample_id base (use 8 for a second K=8 wave -> ids 8..15)")
    ap.add_argument("--workers", type=int, default=12,
                    help="concurrent papers in flight (keeps the server warm)")
    args = ap.parse_args()

    model, base_url = args.provider.split("@", 1)
    key = os.environ.get("OPENAI_API_KEY", "EMPTY")
    corpus = Corpus(args.papers_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    pids = [p for p in corpus.paper_ids()]
    if args.papers:
        wanted = set(args.papers.split(","))
        pids = [p for p in pids if p in wanted]
    if args.limit:
        pids = pids[: args.limit]

    todo = [pid for pid in pids if not (out / f"{pid}.jsonl").exists()]
    print(f"{len(todo)} papers to sample ({len(pids) - len(todo)} already done), "
          f"{args.workers} concurrent", file=sys.stderr)

    counts = {"ok": 0, "ideas": 0, "fail": 0}
    lock = threading.Lock()

    def work(pid):
        paper = corpus.load(pid)
        if paper.idea is None or len(paper.sources) < 2:
            return
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_user(paper)}]
        try:
            contents = sample(base_url, model, key, msgs, args.k,
                              args.temperature, args.max_tokens)
        except Exception as e:
            with lock:
                counts["fail"] += 1
            print(f"  {pid}: request failed after retries ({repr(e)[:60]})",
                  file=sys.stderr)
            return
        ideas = [x for x in (parse_idea(c) for c in contents) if x]
        if not ideas:
            print(f"  {pid}: 0/{args.k} parsed", file=sys.stderr)
            return
        with open(out / f"{pid}.jsonl", "w") as f:
            for i, idea in enumerate(ideas):
                f.write(json.dumps({"paper_id": pid,
                                    "sample_id": args.id_offset + i, **idea}) + "\n")
        with lock:
            counts["ok"] += 1
            counts["ideas"] += len(ideas)
            if counts["ok"] % 25 == 0:
                print(f"  {counts['ok']}/{len(todo)} papers", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    done, ok = counts["ok"], counts["ideas"]
    if counts["fail"]:
        print(f"  {counts['fail']} papers failed after retries", file=sys.stderr)
        print(f"  {pid}: {len(ideas)}/{args.k}", flush=True)
    print(f"\ndone: {done} papers, {ok} ideas -> {out}")


if __name__ == "__main__":
    main()
