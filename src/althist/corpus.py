"""Corpus loading and paper-content access.

A :class:`Corpus` wraps ``data/papers/*.json``. :class:`SourceSet` exposes the
read operations the ideating LLM's tools are built on: source listing,
abstracts, bounded spans of full text, and search. The depth-0 paper itself is
deliberately not reachable through :class:`SourceSet` — the model ideates from
the sources alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .schema import PaperRecord, SourceRecord

MAX_SPAN_CHARS = 6000
SEARCH_CONTEXT_CHARS = 300
MAX_SEARCH_HITS = 8


class Corpus:
    def __init__(self, papers_dir: str | Path):
        self.papers_dir = Path(papers_dir)

    def paper_ids(self) -> list[str]:
        return sorted(p.stem for p in self.papers_dir.glob("*.json"))

    def load(self, paper_id: str) -> PaperRecord:
        path = self.papers_dir / f"{paper_id}.json"
        with open(path) as f:
            return PaperRecord.model_validate(json.load(f))

    def load_all(self) -> list[PaperRecord]:
        return [self.load(pid) for pid in self.paper_ids()]

    def validate(self) -> list[str]:
        """Return a list of human-readable problems across the corpus.

        Title-only sources are allowed (abstracts can be backfilled later);
        a paper is a problem only when it has no sources or none of its
        sources carries any content at all.
        """
        problems: list[str] = []
        for pid in self.paper_ids():
            try:
                paper = self.load(pid)
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                problems.append(f"{pid}: failed to parse ({exc})")
                continue
            if not paper.sources:
                problems.append(f"{pid}: no sources")
            elif not any(s.abstract or s.full_text for s in paper.sources):
                problems.append(f"{pid}: no source has an abstract or full_text")
        return problems

    def coverage(self) -> dict[str, int]:
        """Corpus-level source-content coverage counts."""
        stats = {"papers": 0, "sources": 0, "full_text": 0, "abstract": 0, "bare": 0}
        for pid in self.paper_ids():
            paper = self.load(pid)
            stats["papers"] += 1
            for s in paper.sources:
                stats["sources"] += 1
                stats["full_text"] += s.full_text is not None
                stats["abstract"] += bool(s.abstract)
                stats["bare"] += not s.abstract and s.full_text is None
        return stats


class SourceSet:
    """Tool-facing view over one paper's sources."""

    def __init__(self, paper: PaperRecord):
        self.paper = paper
        self._by_id: dict[str, SourceRecord] = {s.source_id: s for s in paper.sources}

    def _get(self, source_id: str) -> SourceRecord:
        if source_id not in self._by_id:
            known = ", ".join(self._by_id)
            raise KeyError(f"unknown source_id {source_id!r}; known ids: {known}")
        return self._by_id[source_id]

    def list_sources(self) -> list[dict]:
        out = []
        for s in self.paper.sources:
            out.append(
                {
                    "source_id": s.source_id,
                    "title": s.title,
                    "authors": s.authors,
                    "year": s.year,
                    "has_full_text": s.full_text is not None,
                    "full_text_chars": len(s.full_text) if s.full_text else 0,
                }
            )
        return out

    def get_abstract(self, source_id: str) -> dict:
        s = self._get(source_id)
        return {
            "source_id": s.source_id,
            "title": s.title,
            "abstract": s.abstract or "(no abstract available)",
        }

    def read_span(self, source_id: str, start: int = 0, length: int = MAX_SPAN_CHARS) -> dict:
        s = self._get(source_id)
        if not s.full_text:
            return {
                "source_id": source_id,
                "error": "full text not available for this source; use get_abstract",
            }
        length = max(1, min(length, MAX_SPAN_CHARS))
        start = max(0, min(start, len(s.full_text)))
        end = min(start + length, len(s.full_text))
        return {
            "source_id": source_id,
            "start": start,
            "end": end,
            "total_chars": len(s.full_text),
            "text": s.full_text[start:end],
        }

    def search(self, query: str, source_id: str | None = None) -> dict:
        """Case-insensitive literal search with character-offset snippets."""
        targets = [self._get(source_id)] if source_id else self.paper.sources
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        hits = []
        for s in targets:
            text = s.full_text or s.abstract
            if not text:
                continue
            for m in pattern.finditer(text):
                lo = max(0, m.start() - SEARCH_CONTEXT_CHARS)
                hi = min(len(text), m.end() + SEARCH_CONTEXT_CHARS)
                hits.append(
                    {
                        "source_id": s.source_id,
                        "in_full_text": s.full_text is not None,
                        "offset": m.start(),
                        "snippet": text[lo:hi],
                    }
                )
                if len(hits) >= MAX_SEARCH_HITS:
                    return {"query": query, "hits": hits, "truncated": True}
        return {"query": query, "hits": hits, "truncated": False}
