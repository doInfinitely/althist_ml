#!/usr/bin/env python3
"""Prefetch source abstracts into the ingest cache.

Corpus-independent: only writes data/cache/openalex_abstracts.json, so it can
run while source PDFs are still being gathered. Resumable; safe to interrupt.

Default backend is Semantic Scholar's batch endpoint (fast, generous limit);
pass ``--backend openalex`` for the one-at-a-time OpenAlex path. Set an S2 key
in ``SEMANTIC_SCHOLAR_API_KEY`` to raise the rate limit.

    uv run python scripts/prefetch_abstracts.py [--backend s2|openalex] [source_repo]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from althist.ingest import MIN_SOURCES_DEFAULT, AbstractCache  # noqa: E402


def collect_dois(source_repo: Path) -> list[str]:
    mapping = json.loads((source_repo / "paper_sources.json").read_text())
    dois: list[str] = []
    seen = set()
    for entry in mapping.values():
        if not entry.get("true_title"):
            continue
        if len(entry.get("sources") or []) < MIN_SOURCES_DEFAULT:
            continue
        for ref in entry["sources"]:
            doi = ref.get("doi")
            if doi and ref.get("doi_status") == "ok" and doi not in seen:
                seen.add(doi)
                dois.append(doi)
    return dois


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source_repo", nargs="?", default="../low-compute-ml")
    ap.add_argument("--backend", choices=["s2", "openalex"], default="s2")
    args = ap.parse_args()

    dois = collect_dois(Path(args.source_repo))
    cache = AbstractCache(Path("data/cache/openalex_abstracts.json"))
    have = sum(1 for d in dois if d in cache.data)
    print(f"{len(dois)} DOIs total, {have} cached, {len(dois) - have} to fetch "
          f"(backend: {args.backend})", flush=True)

    if args.backend == "s2":
        cache.fill_from_s2(dois, api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"))
    else:
        todo = [d for d in dois if d not in cache.data]
        start = time.time()
        for i, doi in enumerate(todo, 1):
            cache.fetch(doi)
            if i % 100 == 0:
                rate = i / (time.time() - start)
                print(f"  {i}/{len(todo)} ~{(len(todo) - i) / rate / 60:.0f} min left", flush=True)
        cache.save()

    hits = sum(1 for d in dois if cache.data.get(d))
    print(f"done: {hits}/{len(dois)} DOIs have abstracts", flush=True)


if __name__ == "__main__":
    main()
