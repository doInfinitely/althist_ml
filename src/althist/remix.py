"""Corpus hygiene and source remixing.

Two related capabilities:

- **Dedupe** (:func:`find_duplicate_groups`, :func:`apply_dedupe`): the corpus
  contains near-duplicate depth-0 papers (the same work ingested under two
  ids, e.g. ``...-2003`` and ``...-2003-9bdd86``). Duplicates corrupt the
  internal citation graph and double-count human ideas, so they are removed
  before remixing.

- **Skip-level pools** (:func:`build_citation_graph`, :func:`build_skip_pools`):
  for a corpus paper D that cites >=2 other corpus papers (its *intermediate
  ancestors*), pour the ancestors' own sources into one pool and ask whether
  an agent ideating from that grandparent generation can "skip" the
  intermediates and land on D. The pool is emitted as a synthetic
  :class:`PaperRecord` (same schema as ``data/papers``) whose ``idea`` is D's
  extracted human idea, so the ideation loop and the excess-similarity
  machinery run unchanged. Everything identifying D or the intermediates is
  stripped from the pool (leakage guards mirroring ingest); D's identity
  lives only in the ``remix`` metadata block, which the ideating model never
  sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ingest import _same_title, norm_title
from .schema import PaperRecord, SourceRecord

REVIEW_TITLE_RE = re.compile(
    r"\b(tutorial|review|survey|introduction|overview)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def _content_richness(paper: PaperRecord) -> tuple:
    """Sort key: richer papers win the canonical slot."""
    return (
        paper.idea is not None,
        sum(1 for s in paper.sources if s.full_text or s.abstract),
        len(paper.sources),
        paper.year is not None,
        bool(paper.full_text),
        paper.paper_id,  # deterministic tiebreak
    )


def find_duplicate_groups(papers: list[PaperRecord]) -> list[list[str]]:
    """Group paper ids whose titles are the same work (exact or fuzzy match).

    Exact normalized-title collisions are grouped first; groups whose title
    keys :func:`_same_title`-match are then merged, catching truncated-id and
    subtitle variants.
    """
    by_key: dict[str, list[str]] = {}
    for p in papers:
        by_key.setdefault(norm_title(p.title), []).append(p.paper_id)

    keys = sorted(by_key)
    parent = {k: k for k in keys}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if _same_title(a, b):
                parent[find(a)] = find(b)

    merged: dict[str, list[str]] = {}
    for k in keys:
        merged.setdefault(find(k), []).extend(by_key[k])
    return sorted(g for g in merged.values() if len(g) > 1)


def choose_canonical(
    group: list[str], papers: dict[str, PaperRecord], protected: set[str]
) -> str:
    """Pick the id to keep: protected ids (existing runs) always win."""
    prot = sorted(g for g in group if g in protected)
    if len(prot) > 1:
        raise ValueError(f"duplicate group has multiple protected ids: {prot}")
    if prot:
        return prot[0]
    return max(group, key=lambda pid: _content_richness(papers[pid]))


@dataclass
class DedupeReport:
    groups: list[list[str]] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    annotations_pruned: int = 0


def plan_dedupe(
    papers_dir: str | Path, runs_dir: str | Path
) -> tuple[DedupeReport, dict[str, PaperRecord]]:
    papers_dir = Path(papers_dir)
    papers: dict[str, PaperRecord] = {}
    import json

    for f in sorted(papers_dir.glob("*.json")):
        papers[f.stem] = PaperRecord.model_validate(json.loads(f.read_text()))

    protected = {d.name for d in Path(runs_dir).iterdir() if d.is_dir()} if Path(runs_dir).is_dir() else set()
    report = DedupeReport()
    for group in find_duplicate_groups(list(papers.values())):
        keep = choose_canonical(group, papers, protected)
        report.groups.append(group)
        report.kept.append(keep)
        report.removed.extend(pid for pid in group if pid != keep)
    return report, papers


def apply_dedupe(
    papers_dir: str | Path,
    dupes_dir: str | Path,
    annotations_path: str | Path,
    report: DedupeReport,
) -> None:
    """Move duplicate losers aside and prune their annotation lines."""
    papers_dir, dupes_dir = Path(papers_dir), Path(dupes_dir)
    dupes_dir.mkdir(parents=True, exist_ok=True)
    removed = set(report.removed)
    for pid in report.removed:
        (papers_dir / f"{pid}.json").rename(dupes_dir / f"{pid}.json")

    annotations_path = Path(annotations_path)
    if annotations_path.exists():
        import json

        lines = annotations_path.read_text().splitlines()
        kept_lines = [
            ln for ln in lines
            if ln.strip() and json.loads(ln).get("paper_id") not in removed
        ]
        report.annotations_pruned = len(lines) - len(kept_lines)
        if report.annotations_pruned:
            backup = annotations_path.with_suffix(".jsonl.pre-dedupe")
            backup.write_text("\n".join(lines) + "\n")
            annotations_path.write_text("\n".join(kept_lines) + "\n")


# ---------------------------------------------------------------------------
# Citation graph + skip-level pools
# ---------------------------------------------------------------------------


def build_citation_graph(papers: dict[str, PaperRecord]) -> dict[str, set[str]]:
    """corpus-internal edges: paper_id -> ids of corpus papers it cites.

    A source matches a corpus paper by exact normalized title, with a
    :func:`_same_title` fuzzy fallback. Edges where the cited paper is dated
    *after* the citing paper (title false-positives) are dropped.
    """
    exact = {norm_title(p.title): pid for pid, p in papers.items()}
    keys = sorted(exact)
    graph: dict[str, set[str]] = {pid: set() for pid in papers}
    for pid, paper in papers.items():
        for src in paper.sources:
            src_key = norm_title(src.title)
            hit = exact.get(src_key)
            if hit is None:
                hit = next((exact[k] for k in keys if _same_title(src_key, k)), None)
            if hit is None or hit == pid:
                continue
            anc, tgt = papers[hit], paper
            if anc.year is not None and tgt.year is not None and anc.year > tgt.year:
                continue
            graph[pid].add(hit)
    return graph


def _richer(a: SourceRecord, b: SourceRecord) -> SourceRecord:
    def key(s: SourceRecord) -> tuple:
        return (s.full_text is not None, bool(s.abstract), bool(s.year), s.source_id)

    return max(a, b, key=key)


@dataclass
class SkipPool:
    record: PaperRecord
    dropped_leakage: int
    merged_duplicates: int


def build_skip_pool(
    target: PaperRecord, ancestors: list[PaperRecord]
) -> SkipPool:
    """Union the ancestors' sources into one pool, with leakage guards.

    Stripped from the pool: any source whose title matches the target or an
    ancestor (the model must not read the intermediates it is supposed to
    skip, nor the ground truth), and any source whose full text is
    byte-identical to the target's or an ancestor's own text.
    """
    hidden_titles = [norm_title(target.title)] + [norm_title(a.title) for a in ancestors]
    hidden_texts = {t.full_text for t in [target, *ancestors] if t.full_text}

    by_title: dict[str, SourceRecord] = {}
    dropped = 0
    merged = 0
    for anc in sorted(ancestors, key=lambda a: a.paper_id):
        for src in anc.sources:
            src_key = norm_title(src.title)
            if any(_same_title(src_key, h) for h in hidden_titles):
                dropped += 1
                continue
            src = src.model_copy()
            if src.full_text is not None and src.full_text in hidden_texts:
                src.full_text = None
                dropped += 1
                if not src.abstract:
                    continue
            if src_key in by_title:
                by_title[src_key] = _richer(by_title[src_key], src)
                merged += 1
            else:
                by_title[src_key] = src

    # source_id uniqueness across ancestors (different titles can slug alike)
    sources: list[SourceRecord] = []
    seen_ids: set[str] = set()
    for src in by_title.values():
        sid, n = src.source_id, 2
        while sid in seen_ids:
            sid = f"{src.source_id}-{n}"
            n += 1
        if sid != src.source_id:
            src = src.model_copy(update={"source_id": sid})
        seen_ids.add(sid)
        sources.append(src)
    sources.sort(key=lambda s: s.source_id)

    pool_id = f"skip__{target.paper_id}"
    record = PaperRecord(
        paper_id=pool_id,
        title=pool_id,  # never the target's real title: that block is metadata-only
        authors=[],
        year=None,
        abstract="",
        full_text=None,
        idea=target.idea,  # ground truth for the leap score
        sources=sources,
        remix={
            "mode": "skip",
            "target_paper_id": target.paper_id,
            "target_title": target.title,
            "target_year": target.year,
            "ancestor_ids": sorted(a.paper_id for a in ancestors),
            "is_review": bool(REVIEW_TITLE_RE.search(target.title)),
            "sources_dropped_leakage": dropped,
            "sources_merged_duplicate": merged,
        },
    )
    return SkipPool(record=record, dropped_leakage=dropped, merged_duplicates=merged)


def build_skip_pools(
    papers: dict[str, PaperRecord],
    min_ancestors: int = 2,
    min_sources: int = 4,
) -> tuple[list[SkipPool], dict[str, int]]:
    """All skip-level pools for the corpus, plus skip-reason counts."""
    graph = build_citation_graph(papers)
    pools: list[SkipPool] = []
    skipped = {"few_ancestors": 0, "no_target_idea": 0, "few_pool_sources": 0}
    for pid, ancestors in sorted(graph.items()):
        if len(ancestors) < min_ancestors:
            skipped["few_ancestors"] += 1
            continue
        target = papers[pid]
        if target.idea is None:
            skipped["no_target_idea"] += 1
            continue
        pool = build_skip_pool(target, [papers[a] for a in sorted(ancestors)])
        if sum(1 for s in pool.record.sources if s.full_text or s.abstract) < min_sources:
            skipped["few_pool_sources"] += 1
            continue
        pools.append(pool)
    return pools, skipped
