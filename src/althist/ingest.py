"""Ingest the low-compute-ml gathering artifacts into the althist corpus.

Inputs (in ``--source-repo``, default ``../low-compute-ml``):

- ``paper_sources.json`` — per distinct depth-0 paper (keyed by content md5):
  resolved identity (title/authors/year/doi) + parsed reference list, each
  reference with title/doi/year.
- ``paper_text/<stem>.txt`` — extracted text of the depth-0 PDFs.
- ``all_papers_1/*.pdf`` — source PDFs named by source title (gathering in
  progress; currently many are depth-0 papers that other depth-0 papers cite).
- ``papers/*.pdf`` — additional source PDFs named ``Author_Year_Title``.

Output: ``data/papers/<paper_id>.json`` (:class:`althist.schema.PaperRecord`).

Source full text is resolved by normalized-title match against the PDF pools;
extraction results are cached under ``data/cache/source_text/``. Abstracts are
optionally fetched from OpenAlex by DOI (resumable cache at
``data/cache/openalex_abstracts.json``).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .schema import PaperRecord, SourceRecord

MIN_SOURCES_DEFAULT = 4
OPENALEX = "https://api.openalex.org/works"
OPENALEX_DELAY_S = 0.25
OPENALEX_MAILTO = "do.infinitely@gmail.com"  # polite pool, same as low-compute-ml
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=abstract"
S2_BATCH_SIZE = 100  # S2 accepts up to 500, but smaller batches survive 429s better
S2_DELAY_S = 1.1  # unauthenticated S2 is ~1 req/s

_AUTHOR_YEAR_PREFIX = re.compile(r"^[A-Za-z'-]+_\d{4}_")


_CID_RUN = re.compile(r"(/C\d+){4,}")
_CID_TOKEN = re.compile(r"/C(\d+)")


def clean_pdf_text(text: str | None) -> str | None:
    """Repair pdftotext output that emitted glyph-index CID codes.

    Some old PDFs lack a ToUnicode map, so ``pdftotext`` writes ``/C77/C101``
    (i.e. ``/C`` + decimal codepoint) instead of characters. Left as-is, a wall
    of ``/C###`` tokens is unreadable and trips model safety classifiers. Only
    texts actually dominated by the pattern are decoded, so ordinary text
    containing a stray ``/C12`` is untouched.
    """
    if not text:
        return None
    if not _CID_RUN.search(text[:2000]):
        return text
    decoded = _CID_TOKEN.sub(
        lambda m: chr(int(m.group(1))) if 9 <= int(m.group(1)) <= 126 else " ",
        text,
    )
    return decoded or None


def _same_title(a: str, b: str, min_prefix: int = 25) -> bool:
    """Normalized-title identity, tolerant of truncation and subtitle noise.

    Matches on exact equality, or — for titles long enough to be distinctive —
    when the common prefix covers >= 80% of the shorter title (catches
    tech-report vs. journal variants like "... from Data" vs "... (Report
    SMI-91-1)"). Used for the self-citation leakage guard, where a false
    positive (dropping a sibling paper) is far cheaper than a false negative
    (leaking the ground-truth paper into its own source set).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = min(len(a), len(b))
    if shorter < min_prefix:
        return False
    common = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        common += 1
    return common >= max(min_prefix, int(0.8 * shorter))


def norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


@dataclass
class PdfPool:
    """Normalized-title index over the source PDF directories."""

    exact: dict[str, Path] = field(default_factory=dict)
    fuzzy: list[tuple[str, Path]] = field(default_factory=list)  # (norm_stem, path)

    @classmethod
    def build(cls, dirs: list[Path]) -> "PdfPool":
        pool = cls()
        for d in dirs:
            if not d.is_dir():
                continue
            for pdf in sorted(d.glob("*.pdf")):
                stem = pdf.stem
                stem = _AUTHOR_YEAR_PREFIX.sub("", stem)  # Author_2003_Title -> Title
                key = norm_title(stem)
                if key:
                    pool.exact.setdefault(key, pdf)
                    pool.fuzzy.append((key, pdf))
        return pool

    def match(self, title: str) -> Path | None:
        key = norm_title(title)
        if not key:
            return None
        if key in self.exact:
            return self.exact[key]
        # filename stems are often truncated titles; accept a long-prefix match
        if len(key) >= 25:
            for stem_key, path in self.fuzzy:
                if len(stem_key) >= 25 and (key.startswith(stem_key) or stem_key.startswith(key)):
                    return path
        return None


class SourceTextCache:
    """pdftotext extraction with an on-disk cache, reusing paper_text/ copies."""

    def __init__(self, cache_dir: Path, paper_text_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.paper_text_dir = paper_text_dir

    def text_for(self, pdf: Path) -> str | None:
        # fast path: depth-0 copies already have extracted text
        pre = self.paper_text_dir / f"{pdf.stem}.txt"
        if pre.exists():
            return clean_pdf_text(pre.read_text(errors="replace") or None)
        cached = self.cache_dir / f"{slugify(pdf.stem, 100)}.txt"
        if cached.exists():
            return clean_pdf_text(cached.read_text(errors="replace") or None)
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(pdf), str(cached)],
                capture_output=True,
                timeout=120,
            )
            if proc.returncode != 0:
                cached.write_text("")  # negative-cache broken PDFs
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        return clean_pdf_text(cached.read_text(errors="replace") or None)


class AbstractCache:
    """OpenAlex abstracts by DOI, resumable across runs."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str | None] = {}
        if path.exists():
            self.data = json.loads(path.read_text())
        self._dirty = 0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)
        self._dirty = 0

    def fetch(self, doi: str) -> str | None:
        """Fetch one DOI's abstract.

        Only definitive outcomes are cached: a 200 (with or without an
        abstract) or a 404 (unknown DOI -> None). Rate limits and transient
        errors are retried with backoff and, if they persist, left uncached
        so a later pass can pick them up — never negative-cached.
        """
        if doi in self.data:
            return self.data[doi]
        url = (
            f"{OPENALEX}/https://doi.org/{urllib.parse.quote(doi)}"
            f"?select=abstract_inverted_index&mailto={OPENALEX_MAILTO}"
        )
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"althist-ingest ({OPENALEX_MAILTO})"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
                abstract = _deinvert(payload.get("abstract_inverted_index"))
                self._store(doi, abstract)
                time.sleep(OPENALEX_DELAY_S)
                return abstract
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._store(doi, None)
                    time.sleep(OPENALEX_DELAY_S)
                    return None
                if e.code in (429, 500, 502, 503, 504):
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else min(2.0 * 2**attempt, 120.0)
                    time.sleep(wait)
                    continue
                break  # unexpected client error: don't cache, don't retry
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                time.sleep(min(2.0 * 2**attempt, 60.0))
        return None  # exhausted retries: deliberately uncached

    def _store(self, doi: str, abstract: str | None) -> None:
        self.data[doi] = abstract
        self._dirty += 1
        if self._dirty >= 25:
            self.save()

    def fill_from_s2(self, dois: list[str], api_key: str | None = None, log=print) -> None:
        """Batch-fill abstracts from Semantic Scholar (POST /paper/batch).

        Caches only definitive outcomes: a paper object with a non-null
        ``abstract`` -> the text; a paper object present but with a null
        ``abstract`` -> None (S2 knows the paper, has no abstract); a null
        slot in the response (S2 doesn't know the DOI) is left uncached so a
        later OpenAlex pass can try it. Whole-batch failures leave every DOI
        in that batch uncached.
        """
        todo = [d for d in dict.fromkeys(dois) if d not in self.data]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        for start in range(0, len(todo), S2_BATCH_SIZE):
            chunk = todo[start : start + S2_BATCH_SIZE]
            results = self._s2_post(chunk, headers)
            if results is None:
                log(f"  S2 batch {start}-{start + len(chunk)} failed; left uncached")
                continue
            for doi, paper in zip(chunk, results):
                if paper is None:  # S2 doesn't recognize this DOI
                    continue
                self._store(doi, (paper.get("abstract") or "").strip() or None)
            hits = sum(1 for d in todo[: start + len(chunk)] if self.data.get(d))
            log(f"  S2 {min(start + S2_BATCH_SIZE, len(todo))}/{len(todo)} fetched ({hits} abstracts)")
            time.sleep(S2_DELAY_S)
        self.save()

    @staticmethod
    def _s2_post(dois: list[str], headers: dict) -> list | None:
        body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode()
        for attempt in range(6):
            try:
                req = urllib.request.Request(S2_BATCH, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=90) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else min(2.0 * 2**attempt, 60.0)
                    time.sleep(wait)
                    continue
                return None
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                time.sleep(min(2.0 * 2**attempt, 30.0))
        return None


def _deinvert(inverted: dict[str, list[int]] | None) -> str | None:
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        positions.extend((i, word) for i in idxs)
    return " ".join(w for _, w in sorted(positions)) or None


@dataclass
class IngestStats:
    papers_seen: int = 0
    papers_written: int = 0
    skipped_few_sources: int = 0
    skipped_no_identity: int = 0
    sources_total: int = 0
    sources_with_full_text: int = 0
    sources_with_abstract: int = 0
    sources_bare: int = 0  # title/year only
    sources_self_dropped: int = 0  # self-citations removed (leakage guard)
    sources_text_leak_blocked: int = 0  # own-text attachments blocked (leakage guard)


def ingest(
    source_repo: str | Path,
    papers_dir: str | Path = "data/papers",
    cache_dir: str | Path = "data/cache",
    min_sources: int = MIN_SOURCES_DEFAULT,
    fetch_abstracts: bool = False,
    limit: int | None = None,
    log=print,
) -> IngestStats:
    source_repo = Path(source_repo)
    papers_dir = Path(papers_dir)
    papers_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir)

    mapping = json.loads((source_repo / "paper_sources.json").read_text())
    pool = PdfPool.build([source_repo / "all_papers_1", source_repo / "papers"])
    texts = SourceTextCache(cache_dir / "source_text", source_repo / "paper_text")

    # Abstracts are always read from the on-disk cache; ingest never live-fetches
    # in the per-source loop (a rate-limit backoff there would stall the whole
    # run). --fetch-abstracts does one safe S2 batch prefill up front; the
    # standalone prefetch script is the path for larger / OpenAlex fetches.
    abstract_cache_path = cache_dir / "openalex_abstracts.json"
    abstracts = AbstractCache(abstract_cache_path) if abstract_cache_path.exists() else None
    if fetch_abstracts:
        abstracts = abstracts or AbstractCache(abstract_cache_path)
        corpus_dois = [
            ref["doi"]
            for e in mapping.values()
            for ref in (e.get("sources") or [])
            if ref.get("doi") and ref.get("doi_status") == "ok"
        ]
        missing = [d for d in dict.fromkeys(corpus_dois) if d not in abstracts.data]
        if missing:
            log(f"prefilling {len(missing)} uncached abstracts from Semantic Scholar")
            abstracts.fill_from_s2(missing, log=log)

    stats = IngestStats()
    seen_ids: set[str] = set()
    entries = sorted(mapping.values(), key=lambda e: e.get("true_title") or e["rep_filename"])
    if limit:
        entries = entries[:limit]

    for entry in entries:
        stats.papers_seen += 1
        title = entry.get("true_title")
        if not title:
            stats.skipped_no_identity += 1
            continue
        raw_sources = entry.get("sources") or []
        if len(raw_sources) < min_sources:
            stats.skipped_few_sources += 1
            continue

        paper_id = slugify(f"{title}-{entry.get('year') or ''}")
        if paper_id in seen_ids:
            paper_id = f"{paper_id}-{entry['md5'][:6]}"
        seen_ids.add(paper_id)

        full_text = None
        text_file = source_repo / "paper_text" / f"{Path(entry['rep_filename']).stem}.txt"
        if text_file.exists():
            full_text = clean_pdf_text(text_file.read_text(errors="replace") or None)

        sources: list[SourceRecord] = []
        used_source_ids: set[str] = set()
        paper_key = norm_title(title)
        for ref in raw_sources:
            ref_title = (ref.get("title") or "").strip()
            if not ref_title:
                continue
            # Leakage guard 1: drop self-citations (e.g. the tech-report
            # version of the depth-0 paper itself) — the ground truth must
            # not appear in its own source set.
            if _same_title(norm_title(ref_title), paper_key):
                stats.sources_self_dropped += 1
                continue
            source_id = slugify(f"{ref_title}-{ref.get('year') or ''}") or f"src-{len(sources)}"
            if source_id in used_source_ids:
                continue
            used_source_ids.add(source_id)

            src_text = None
            pdf = pool.match(ref_title)
            if pdf is not None and not _same_title(norm_title(pdf.stem), paper_key):
                src_text = texts.text_for(pdf)
                # Leakage guard 2: never attach text identical to the depth-0
                # paper's own text (all_papers_1 holds byte-identical copies).
                if src_text is not None and full_text is not None and src_text == full_text:
                    src_text = None
                    stats.sources_text_leak_blocked += 1
            abstract = ""
            if abstracts is not None and ref.get("doi") and ref.get("doi_status") == "ok":
                abstract = abstracts.data.get(ref["doi"]) or ""  # cache-only read

            stats.sources_total += 1
            if src_text:
                stats.sources_with_full_text += 1
            if abstract:
                stats.sources_with_abstract += 1
            if not src_text and not abstract:
                stats.sources_bare += 1

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    title=ref_title,
                    year=ref.get("year"),
                    abstract=abstract,
                    full_text=src_text,
                )
            )

        record = PaperRecord(
            paper_id=paper_id,
            title=title,
            authors=entry.get("true_authors") or [],
            year=entry.get("year"),
            abstract="",
            full_text=full_text,
            sources=sources,
        )
        out = papers_dir / f"{paper_id}.json"
        # preserve a previously extracted human idea across re-ingests
        if out.exists():
            old = PaperRecord.model_validate_json(out.read_text())
            record.idea = old.idea
        out.write_text(record.model_dump_json(indent=1, exclude_none=True))
        stats.papers_written += 1
        if stats.papers_written % 50 == 0:
            log(f"  ...{stats.papers_written} papers written")

    if abstracts is not None:
        abstracts.save()
    return stats
