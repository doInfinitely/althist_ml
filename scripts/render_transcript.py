#!/usr/bin/env python3
"""Render an ideation transcript JSONL into readable Markdown.

Shows, per turn, the model's reasoning text (assistant message content), the
tool calls it made (name + args), and the tool results (truncated), then the
submitted idea. Useful for reading what the *ablated* model actually thought
and did during a run.

    uv run python scripts/render_transcript.py data/runs_pools/<...>.jsonl
    uv run python scripts/render_transcript.py <dir-or-glob> -o out.md
"""

import argparse
import glob
import json
import os
import sys


def _content(raw):
    try:
        return (raw["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""


def render(path, trunc=800):
    rows = [json.loads(l) for l in open(path)]
    out = []
    meta = next((r["payload"] for r in rows if r["kind"] == "run_meta"), {})
    out.append(f"# {os.path.basename(path)}\n")
    out.append(f"- **model**: `{meta.get('model')}`  ")
    out.append(f"\n- **paper (pool)**: `{meta.get('paper_id')}`  ")
    out.append(f"\n- **condition**: `{meta.get('condition')}`  ")
    out.append(f"\n- **n_sources**: {meta.get('n_sources')}\n\n")

    results = {r["payload"]["id"]: r["payload"] for r in rows if r["kind"] == "tool_result"}
    for r in rows:
        k, p = r["kind"], r.get("payload", {})
        if k == "assistant_message":
            txt = _content(p["raw"])
            if txt:
                out.append(f"### Turn {p['turn']} — reasoning\n\n{txt}\n\n")
        elif k == "tool_call":
            args = json.dumps(p.get("args", {}))
            if len(args) > 300:
                args = args[:300] + "…"
            out.append(f"**→ tool call** `{p['name']}`  args=`{args}`\n\n")
            res = results.get(p["id"])
            if res is not None:
                c = res.get("content", "")
                c = c if isinstance(c, str) else json.dumps(c)
                err = " (ERROR)" if res.get("is_error") else ""
                if len(c) > trunc:
                    c = c[:trunc] + f"… [+{len(c) - trunc} chars]"
                out.append(f"> result{err}: {c}\n\n")
        elif k == "final_idea":
            out.append("---\n\n## Submitted idea\n\n")
            out.append(f"**Motivation**\n\n{p.get('motivation','')}\n\n")
            out.append(f"**Method**\n\n{p.get('method','')}\n\n")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="jsonl file(s), dir, or glob")
    ap.add_argument("-o", "--out", help="write to this file (default: stdout)")
    ap.add_argument("--trunc", type=int, default=800, help="tool-result truncation")
    args = ap.parse_args()

    files = []
    for pth in args.paths:
        if os.path.isdir(pth):
            files += sorted(glob.glob(os.path.join(pth, "*.jsonl")))
        else:
            files += sorted(glob.glob(pth)) or [pth]
    if not files:
        print("no transcripts matched", file=sys.stderr)
        sys.exit(1)

    md = "\n\n<hr>\n\n".join(render(f, args.trunc) for f in files)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(md)
        print(f"wrote {len(files)} transcript(s) -> {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
