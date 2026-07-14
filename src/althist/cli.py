"""althist command-line interface.

    althist validate                      # check the corpus under data/papers
    althist extract  --provider anthropic # extract human ideas into paper JSONs
    althist ideate   --provider anthropic [--pairs] [--papers a,b] [--limit N]
    althist annotate --provider anthropic # annotate human + run ideas
    althist analyze  [--split-conditions] [--embeddings hashing|qwen3]
    althist archetypes --provider anthropic
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ideation import MAX_TURNS as IDEATION_MAX_TURNS
from .schema import AnnotatedIdea, Condition, Idea

PAPERS_DIR = "data/papers"
RUNS_DIR = "data/runs"
DUPES_DIR = "data/papers_dupes"
POOLS_DIR = "data/pools"
POOL_RUNS_DIR = "data/runs_pools"
ANNOTATIONS_PATH = "data/annotations/annotations.jsonl"
ARCHETYPES_PATH = "data/analysis/archetypes.jsonl"
EXPORT_PATH = "data/analysis/generated_ideas.md"


def _corpus():
    from .corpus import Corpus

    return Corpus(PAPERS_DIR)


def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import ingest

    stats = ingest(
        source_repo=args.source_repo,
        papers_dir=PAPERS_DIR,
        min_sources=args.min_sources,
        fetch_abstracts=args.fetch_abstracts,
        limit=args.limit,
    )
    print(f"papers: seen={stats.papers_seen} written={stats.papers_written} "
          f"skipped(no identity)={stats.skipped_no_identity} "
          f"skipped(<{args.min_sources} sources)={stats.skipped_few_sources}")
    print(f"sources: total={stats.sources_total} "
          f"full_text={stats.sources_with_full_text} "
          f"abstract={stats.sources_with_abstract} bare={stats.sources_bare}")
    print(f"leakage guards: self-citations dropped={stats.sources_self_dropped} "
          f"own-text attachments blocked={stats.sources_text_leak_blocked}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    corpus = _corpus()
    ids = corpus.paper_ids()
    problems = corpus.validate()
    print(f"{len(ids)} papers in {PAPERS_DIR}")
    n_with_idea = sum(1 for pid in ids if corpus.load(pid).idea is not None)
    print(f"{n_with_idea} have an extracted human idea")
    cov = corpus.coverage()
    print(f"sources: total={cov['sources']} full_text={cov['full_text']} "
          f"abstract={cov['abstract']} bare(title-only)={cov['bare']}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1 if problems else 0


def cmd_extract(args: argparse.Namespace) -> int:
    from .llm import make_provider
    from .prompts import EXTRACTION_SCHEMA, EXTRACTION_SYSTEM, extraction_user

    corpus = _corpus()
    provider = make_provider(args.provider)
    failures: list[tuple[str, str]] = []
    done = 0
    for pid in corpus.paper_ids():
        paper = corpus.load(pid)
        if paper.idea is not None and not args.force:
            continue
        print(f"extracting human idea: {pid}", flush=True)
        try:
            raw = provider.structured_json(EXTRACTION_SYSTEM, extraction_user(paper), EXTRACTION_SCHEMA)
            paper.idea = Idea.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - one bad paper must not kill the batch
            print(f"  FAILED {pid}: {type(exc).__name__}: {exc}", flush=True)
            failures.append((pid, f"{type(exc).__name__}: {exc}"))
            continue
        path = Path(PAPERS_DIR) / f"{pid}.json"
        path.write_text(paper.model_dump_json(indent=2, exclude_none=True))
        done += 1
    print(f"extracted {done} ideas; {len(failures)} failed")
    for pid, err in failures:
        print(f"  {pid}: {err}")
    return 1 if failures else 0


def cmd_ideate(args: argparse.Namespace) -> int:
    from .corpus import Corpus
    from .ideation import run_ideation
    from .llm import make_provider
    from .taxonomy import fanout_conditions

    corpus = Corpus(POOLS_DIR) if args.pools else _corpus()
    runs_dir = POOL_RUNS_DIR if args.pools else RUNS_DIR
    provider = make_provider(args.provider)
    paper_ids = args.papers.split(",") if args.papers else corpus.paper_ids()
    if args.limit:
        paper_ids = paper_ids[: args.limit]
    conditions = fanout_conditions(include_pairs=args.pairs)
    if args.conditions:
        wanted = set(args.conditions.split(","))
        all_conditions = fanout_conditions(include_pairs=True)
        conditions = [c for c in all_conditions if c.key in wanted]
        missing = wanted - {c.key for c in conditions}
        if missing:
            print(f"unknown condition keys: {sorted(missing)}", file=sys.stderr)
            return 2
    print(f"{len(paper_ids)} papers x {len(conditions)} conditions", flush=True)

    # Resumability for long runs: skip (paper, condition, model) episodes that
    # already have a successful transcript on disk. --redo forces a rerun.
    done: set[tuple[str, str, str]] = set()
    if not args.redo:
        from .ideation import load_run_results

        for r in load_run_results(runs_dir):
            if r.idea is not None:
                done.add((r.paper_id, r.condition.key, r.model))

    failures = 0
    for pid in paper_ids:
        paper = corpus.load(pid)
        for condition in conditions:
            if (pid, condition.key, provider.model) in done:
                print(f"  {pid} [{condition.key}] skipped (already done)", flush=True)
                continue
            result = run_ideation(paper, condition, provider, runs_dir=runs_dir,
                                  max_turns=args.max_turns)
            status = "ok" if result.idea else f"FAILED: {result.error}"
            print(
                f"  {pid} [{condition.key}] turns={result.n_turns} "
                f"tools={result.n_tool_calls} {status}",
                flush=True,
            )
            failures += result.idea is None
    return 1 if failures else 0


def _append_annotations(items: list[AnnotatedIdea]) -> None:
    path = Path(ANNOTATIONS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for item in items:
            f.write(item.model_dump_json() + "\n")


def _load_annotations() -> list[AnnotatedIdea]:
    path = Path(ANNOTATIONS_PATH)
    if not path.exists():
        return []
    with open(path) as f:
        return [AnnotatedIdea.model_validate_json(line) for line in f if line.strip()]


def cmd_annotate(args: argparse.Namespace) -> int:
    from .annotate import annotate_idea
    from .ideation import load_run_results
    from .llm import make_provider

    corpus = _corpus()
    provider = make_provider(args.provider)
    done = {(a.paper_id, a.source, a.condition.key if a.condition else None) for a in _load_annotations()}
    new: list[AnnotatedIdea] = []

    def annotate(paper_id: str, source: str, condition: Condition | None, idea: Idea) -> None:
        key = (paper_id, source, condition.key if condition else None)
        if key in done:
            return
        paper = corpus.load(paper_id)
        titles = [s.title for s in paper.sources]
        print(f"annotating {source} idea for {paper_id}"
              + (f" [{condition.key}]" if condition else ""), flush=True)
        annotation = annotate_idea(provider, paper_id, titles, idea)
        new.append(
            AnnotatedIdea(
                paper_id=paper_id, source=source, condition=condition,
                idea=idea, annotation=annotation,
            )
        )
        done.add(key)

    for pid in corpus.paper_ids():
        paper = corpus.load(pid)
        if paper.idea is not None:
            annotate(pid, "human", None, paper.idea)
    for run in load_run_results(RUNS_DIR):
        if run.idea is not None:
            annotate(run.paper_id, run.model, run.condition, run.idea)

    _append_annotations(new)
    print(f"added {len(new)} annotations -> {ANNOTATIONS_PATH}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Render generated ideas to markdown, grouped by seed paper."""
    from .ideation import load_run_results
    from .taxonomy import METHOD_PARADIGMS, OPPORTUNITY_PATTERNS

    def label(condition: Condition) -> str:
        parts = []
        if condition.pattern:
            parts.append(f"opportunity={OPPORTUNITY_PATTERNS[condition.pattern]['name']}")
        if condition.paradigm:
            parts.append(f"method={METHOD_PARADIGMS[condition.paradigm]['name']}")
        return "; ".join(parts) if parts else "unsteered (blank)"

    from .corpus import Corpus

    corpus = Corpus(POOLS_DIR) if args.pools else _corpus()
    runs_dir = POOL_RUNS_DIR if args.pools else RUNS_DIR
    runs = [r for r in load_run_results(runs_dir) if r.idea is not None]
    if args.papers:
        wanted = set(args.papers.split(","))
        runs = [r for r in runs if r.paper_id in wanted]
    by_paper: dict[str, list] = {}
    for r in runs:
        by_paper.setdefault(r.paper_id, []).append(r)

    lines = ["# Generated research ideas", "",
             f"{len(runs)} generated ideas across {len(by_paper)} seed papers.", ""]
    for pid in sorted(by_paper):
        paper = corpus.load(pid)
        lines += [f"## {paper.title}", "",
                  f"*seed paper `{pid}`, {len(paper.sources)} sources, "
                  f"{len(by_paper[pid])} generated ideas*", ""]
        if paper.idea is not None:
            lines += ["### Human idea (ground truth)", "",
                      f"**Motivation.** {paper.idea.motivation}", "",
                      f"**Method.** {paper.idea.method}", ""]
        lines.append("### LLM ideas")
        lines.append("")
        for r in sorted(by_paper[pid], key=lambda r: r.condition.key):
            lines += [f"#### [{label(r.condition)}] — {r.model}, "
                      f"{r.n_turns} turns / {r.n_tool_calls} tool calls", "",
                      f"**Motivation.** {r.idea.motivation}", "",
                      f"**Method.** {r.idea.method}", ""]
        lines.append("---")
        lines.append("")

    path = Path(EXPORT_PATH.replace(".md", "_pools.md") if args.pools else EXPORT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    print(f"wrote {len(runs)} ideas across {len(by_paper)} papers -> {path}")
    return 0


SEEDS_PATH = "data/analysis/task_seeds.jsonl"


def cmd_score(args: argparse.Namespace) -> int:
    """Rank generated ideas by Harbor task-worthiness."""
    from .ideation import load_run_results
    from .taskseed import rank_seeds, score_idea

    corpus = _corpus()
    runs = [r for r in load_run_results(RUNS_DIR) if r.idea is not None]
    if not runs:
        print("no generated ideas found; run `althist ideate` first", file=sys.stderr)
        return 1

    # annotation signals, keyed by (paper_id, source, condition_key)
    ann_by_key: dict[tuple, AnnotatedIdea] = {}
    for a in _load_annotations():
        if a.source == "human" or a.annotation is None:
            continue
        ck = a.condition.key if a.condition else "blank__blank"
        ann_by_key[(a.paper_id, a.source, ck)] = a

    # embedding signals (recall safety, source relevance) — optional
    repr_by_key: dict[tuple, tuple[float | None, float]] = {}
    if args.embeddings:
        repr_by_key = _representation_signals(corpus, runs, args.embeddings)

    # forward-extension signal (descendant excess) — present if `althist
    # fwdext` has been run; tightens recall safety on descendant-bearing papers
    fwd_by_key: dict[tuple, float] = {}
    fwd_path = Path(FWDEXT_PATH)
    if fwd_path.exists():
        with open(fwd_path) as f:
            for line in f:
                row = json.loads(line)
                fwd_by_key[(row["paper_id"], row["model"], row["condition_key"])] = \
                    row["max_excess_to_descendant"]

    scores = []
    for r in runs:
        ck = r.condition.key
        key = (r.paper_id, r.model, ck)
        ann = ann_by_key.get(key)
        # paradigm: steered label if present, else annotator's primary
        paradigm = r.condition.paradigm
        if paradigm is None and ann is not None:
            paradigm = ann.annotation.method_paradigm.primary
        diag = ann.annotation.diagnostics if ann else None
        excess, mean_sim = repr_by_key.get((r.paper_id, r.model, ck), (None, None))
        scores.append(
            score_idea(
                r.paper_id, r.model, ck, paradigm,
                excess_gt_similarity=excess,
                max_descendant_excess=fwd_by_key.get((r.paper_id, r.model, ck)),
                mean_source_similarity=mean_sim,
                bottleneck_specificity=diag.bottleneck_specificity if diag else None,
                surface_stitching_score=diag.surface_stitching_score if diag else None,
                boilerplate_score=diag.boilerplate_score if diag else None,
            )
        )

    ranked = rank_seeds(scores)
    path = Path(SEEDS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for s in ranked:
            f.write(json.dumps({
                "paper_id": s.paper_id, "condition": s.condition_key, "shape": s.shape,
                "composite": s.composite, "components": s.components, "missing": s.missing,
            }) + "\n")

    signals = sorted({k for s in scores for k in s.components})
    print(f"scored {len(ranked)} ideas using signals: {signals or '(none — need annotate/--embeddings)'}")
    print(f"top {min(args.top, len(ranked))} task-seed candidates:\n")
    for s in ranked[: args.top]:
        print(f"  {s.explain()}")
        print(f"     {s.paper_id} [{s.condition_key}]")
    print(f"\nfull ranking -> {SEEDS_PATH}")
    return 0


def _representation_signals(corpus, runs, backend_spec):
    """Per (paper, condition): (excess GT similarity, mean source similarity)."""
    from .embeddings import make_backend
    from .metrics.representation import representation_scores

    backend = make_backend(backend_spec)
    out: dict[tuple, tuple[float | None, float]] = {}
    for r in runs:
        paper = corpus.load(r.paper_id)
        if len(paper.sources) < 2:
            continue
        texts = [f"{r.idea.motivation}\n{r.idea.method}"]
        texts += [f"{s.title}. {s.abstract}" for s in paper.sources]
        gt_idx = None
        if paper.idea is not None:
            texts.append(f"{paper.idea.motivation}\n{paper.idea.method}")
            gt_idx = len(texts) - 1
        vecs = backend.embed(texts)
        gt = vecs[gt_idx] if gt_idx is not None else None
        sources = vecs[1:gt_idx] if gt_idx is not None else vecs[1:]
        sc = representation_scores(vecs[0], sources, ground_truth=gt)
        out[(r.paper_id, r.model, r.condition.key)] = (sc.excess_gt_similarity, sc.mean_similarity)
    return out


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analysis import compare_distributions, format_report

    items = _load_annotations()
    if not items:
        print("no annotations found; run `althist annotate` first", file=sys.stderr)
        return 1
    rows = compare_distributions(items, split_conditions=args.split_conditions)
    print(format_report(rows))

    if args.embeddings:
        _representation_report(items, args.embeddings)
    return 0


def _representation_report(items: list[AnnotatedIdea], backend_spec: str) -> None:
    import numpy as np

    from .embeddings import make_backend
    from .metrics.representation import representation_scores

    corpus = _corpus()
    backend = make_backend(backend_spec)
    by_source: dict[str, list] = {}
    for item in items:
        paper = corpus.load(item.paper_id)
        if len(paper.sources) < 2:
            continue
        texts = [f"{item.idea.motivation}\n{item.idea.method}"]
        texts += [f"{s.title}. {s.abstract}" for s in paper.sources]
        gt = None
        if paper.idea is not None and item.source != "human":
            texts.append(f"{paper.idea.motivation}\n{paper.idea.method}")
        vectors = backend.embed(texts)
        if paper.idea is not None and item.source != "human":
            gt = vectors[-1]
            sources = vectors[1:-1]
        else:
            sources = vectors[1:]
        scores = representation_scores(vectors[0], sources, ground_truth=gt)
        by_source.setdefault(item.source, []).append(scores)

    print("\n== representation mechanism ==")
    print(f"{'source':<30} {'n':>4} {'H':>6} {'meanS':>6} {'B':>6} {'excess':>7} {'penalty':>7}")
    for source, scores in sorted(by_source.items(), key=lambda kv: (kv[0] != "human", kv[0])):
        h = float(np.mean([s.h for s in scores]))
        ms = float(np.mean([s.mean_similarity for s in scores]))
        b = float(np.mean([s.b for s in scores]))
        ex = [s.excess_gt_similarity for s in scores if s.excess_gt_similarity is not None]
        pen = [s.contamination_penalty for s in scores if s.contamination_penalty is not None]
        ex_s = f"{np.mean(ex):7.3f}" if ex else "   --  "
        pen_s = f"{np.mean(pen):7.3f}" if pen else "   --  "
        print(f"{source:<30} {len(scores):>4} {h:6.3f} {ms:6.3f} {b:6.3f} {ex_s} {pen_s}")


def cmd_dedupe(args: argparse.Namespace) -> int:
    """Find (and with --apply, remove) near-duplicate depth-0 papers."""
    from .remix import apply_dedupe, plan_dedupe

    report, _papers = plan_dedupe(PAPERS_DIR, RUNS_DIR)
    if not report.groups:
        print("no duplicate papers found")
        return 0
    for group, keep in zip(report.groups, report.kept):
        print(f"duplicate group (keep {keep}):")
        for pid in group:
            print(f"  {'KEEP  ' if pid == keep else 'remove'} {pid}")
    if not args.apply:
        print(f"\n{len(report.removed)} papers would be removed; rerun with --apply")
        return 0
    apply_dedupe(PAPERS_DIR, DUPES_DIR, ANNOTATIONS_PATH, report)
    print(f"\nmoved {len(report.removed)} papers -> {DUPES_DIR}; "
          f"pruned {report.annotations_pruned} annotation lines "
          f"(backup: {ANNOTATIONS_PATH}.pre-dedupe)")
    return 0


def cmd_remix(args: argparse.Namespace) -> int:
    """Build source-remix pools (currently: skip-level mode)."""
    from .remix import build_skip_pools

    corpus = _corpus()
    papers = {pid: corpus.load(pid) for pid in corpus.paper_ids()}
    pools, skipped = build_skip_pools(
        papers, min_ancestors=args.min_ancestors, min_sources=args.min_sources
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n_review = 0
    for pool in pools:
        rec = pool.record
        (out_dir / f"{rec.paper_id}.json").write_text(
            rec.model_dump_json(indent=2, exclude_none=True)
        )
        n_review += rec.remix["is_review"]
    sizes = sorted(len(p.record.sources) for p in pools)
    print(f"wrote {len(pools)} skip pools -> {out_dir} "
          f"({n_review} review/tutorial targets tagged)")
    if pools:
        print(f"pool sizes: min={sizes[0]} median={sizes[len(sizes) // 2]} max={sizes[-1]}")
        print(f"leakage-dropped sources: {sum(p.dropped_leakage for p in pools)}; "
              f"merged duplicate sources: {sum(p.merged_duplicates for p in pools)}")
    print(f"targets skipped: {skipped}")
    return 0


LEAP_PATH = "data/analysis/leap_scores.jsonl"


def cmd_leap(args: argparse.Namespace) -> int:
    """Score skip-pool runs: did the agent leap to the hidden target?"""
    import dataclasses

    from .corpus import Corpus
    from .embeddings import make_backend
    from .ideation import load_run_results
    from .leap import compute_leap

    pools = Corpus(POOLS_DIR)
    papers = _corpus()
    runs = [r for r in load_run_results(POOL_RUNS_DIR) if r.idea is not None]
    if not runs:
        print("no pool runs found; run `althist ideate --pools` first", file=sys.stderr)
        return 1
    backend = make_backend(args.embeddings)

    rows = []
    for r in runs:
        pool = pools.load(r.paper_id)
        if pool.remix is None or pool.remix.get("mode") != "skip":
            continue
        ancestor_ideas = {}
        for aid in pool.remix["ancestor_ids"]:
            anc = papers.load(aid)
            if anc.idea is not None:
                ancestor_ideas[aid] = anc.idea
        rows.append(compute_leap(pool, r.idea, ancestor_ideas, backend,
                                 condition_key=r.condition.key))

    rows.sort(key=lambda s: s.leap_margin, reverse=True)
    path = Path(LEAP_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for s in rows:
            f.write(json.dumps(dataclasses.asdict(s)) + "\n")

    import numpy as np

    print(f"{'pool (target)':<52} {'cond':<14} {'leap':>6} {'intmd':>6} {'margin':>7} {'topkS':>6} {'rev':>3}")
    for s in rows[: args.top]:
        print(f"{s.target_paper_id[:52]:<52} {s.condition_key[:14]:<14} "
              f"{s.leap_excess:6.3f} {s.intermediate_excess:6.3f} {s.leap_margin:7.3f} "
              f"{s.topk_mean_similarity:6.3f} {'  R' if s.is_review else '   '}")
    print(f"\nn={len(rows)}  mean leap={np.mean([s.leap_excess for s in rows]):.3f}  "
          f"mean intermediate={np.mean([s.intermediate_excess for s in rows]):.3f}  "
          f"mean margin={np.mean([s.leap_margin for s in rows]):.3f}")
    print(f"full scores -> {LEAP_PATH}")
    return 0


FWDEXT_PATH = "data/analysis/forward_extension.jsonl"


def cmd_fwdext(args: argparse.Namespace) -> int:
    """Detect forward extension: ideas that dodge the recall gate by
    proposing a memorized *descendant* of the seed paper."""
    import dataclasses

    from .embeddings import make_backend
    from .fwdext import classify_episodes
    from .ideation import load_run_results
    from .remix import build_citation_graph

    corpus = _corpus()
    papers = {pid: corpus.load(pid) for pid in corpus.paper_ids()}
    graph = build_citation_graph(papers)
    descendants: dict[str, set[str]] = {}
    for e, ancs in graph.items():
        for a in ancs:
            descendants.setdefault(a, set()).add(e)

    by_paper: dict[str, list] = {}
    for r in load_run_results(RUNS_DIR):
        if r.idea is not None and r.paper_id in descendants:
            by_paper.setdefault(r.paper_id, []).append(r)

    backend = make_backend(args.embeddings)
    rows = []
    for pid, runs in sorted(by_paper.items()):
        desc_ideas = {d: papers[d].idea for d in descendants[pid]
                      if papers[d].idea is not None}
        if not desc_ideas:
            continue
        episodes = [(r.model, r.condition.key, r.idea) for r in runs]
        rows.extend(classify_episodes(papers[pid], episodes, desc_ideas, backend))

    path = Path(FWDEXT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for s in rows:
            f.write(json.dumps(dataclasses.asdict(s)) + "\n")

    from collections import Counter

    counts = Counter(s.label for s in rows)
    n = len(rows)
    print(f"{n} episodes across {len(by_paper)} descendant-bearing papers")
    for label in ("regurgitation", "forward_extension", "clean"):
        c = counts.get(label, 0)
        print(f"  {label:<18} {c:>4}  ({c / max(1, n) * 100:.1f}%)")
    fes = [s for s in rows if s.label == "forward_extension"]
    fes.sort(key=lambda s: -s.max_excess_to_descendant)
    if fes:
        print("\nstrongest forward extensions (pass recall gate, hit a descendant):")
        for s in fes[: args.top]:
            print(f"  exD={s.excess_to_target:+.3f} exDesc={s.max_excess_to_descendant:+.3f} "
                  f"{s.paper_id[:40]} [{s.condition_key[:28]}] -> {s.closest_descendant_id[:40]}")
    print(f"\nfull results -> {FWDEXT_PATH}")
    return 0


def cmd_archetypes(args: argparse.Namespace) -> int:
    from .archetype import operation_enrichment, rewrite_archetype
    from .llm import make_provider

    items = _load_annotations()
    if not items:
        print("no annotations found; run `althist annotate` first", file=sys.stderr)
        return 1
    provider = make_provider(args.provider)
    path = Path(ARCHETYPES_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    cached: dict[str, str] = {}
    if path.exists():
        with open(path) as f:
            for line in f:
                row = json.loads(line)
                cached[row["key"]] = row["archetype"]

    human, model = [], []
    with open(path, "a") as f:
        for item in items:
            key = f"{item.paper_id}|{item.source}|{item.condition.key if item.condition else ''}"
            if key not in cached:
                arch = rewrite_archetype(provider, item.idea)
                cached[key] = arch
                f.write(json.dumps({"key": key, "archetype": arch}) + "\n")
                f.flush()
            (human if item.source == "human" else model).append(cached[key])

    print(f"{'operation':<16} {'model':>6} {'human':>6} {'log-odds':>9}")
    for e in operation_enrichment(model, human):
        print(f"{e.operation:<16} {e.model_count:>6} {e.human_count:>6} {e.log_odds:>9.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="althist", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="build the corpus from low-compute-ml artifacts")
    p.add_argument("--source-repo", default="../low-compute-ml")
    p.add_argument("--min-sources", type=int, default=4)
    p.add_argument("--fetch-abstracts", action="store_true",
                   help="S2 batch-prefill any uncached source abstracts before ingest "
                        "(abstracts are always read from data/cache/ regardless)")
    p.add_argument("--limit", type=int, help="max papers (for smoke tests)")

    sub.add_parser("validate", help="validate the paper corpus")

    p = sub.add_parser("extract", help="extract human ideas from depth-0 papers")
    p.add_argument("--provider", default="anthropic")
    p.add_argument("--force", action="store_true", help="re-extract existing ideas")

    p = sub.add_parser("ideate", help="run the fanout ideation over the corpus")
    p.add_argument("--provider", default="anthropic")
    p.add_argument("--papers", help="comma-separated paper ids (default: all)")
    p.add_argument("--limit", type=int, help="max number of papers")
    p.add_argument("--pairs", action="store_true", help="include all 49 pattern x paradigm pairs")
    p.add_argument("--conditions", help="comma-separated condition keys, e.g. blank__blank or "
                   "failure_risk_gap__blank (overrides the default fanout)")
    p.add_argument("--redo", action="store_true",
                   help="rerun episodes even if a successful transcript already exists")
    p.add_argument("--max-turns", type=int, default=IDEATION_MAX_TURNS,
                   help="safety ceiling on turns per episode (default: %(default)s)")
    p.add_argument("--pools", action="store_true",
                   help=f"run over remix pools ({POOLS_DIR} -> {POOL_RUNS_DIR})")

    p = sub.add_parser("annotate", help="annotate human and generated ideas")
    p.add_argument("--provider", default="anthropic")

    p = sub.add_parser("export", help="render generated ideas to markdown")
    p.add_argument("--papers", help="comma-separated paper ids (default: all)")
    p.add_argument("--pools", action="store_true",
                   help=f"export remix-pool runs ({POOL_RUNS_DIR})")

    p = sub.add_parser("dedupe", help="find/remove near-duplicate depth-0 papers")
    p.add_argument("--apply", action="store_true",
                   help=f"move duplicates to {DUPES_DIR} (default: dry run)")

    p = sub.add_parser("leap", help="score skip-pool runs (leap vs intermediate recall)")
    p.add_argument("--embeddings", default="qwen3", help="hashing | qwen3 | st:<model>")
    p.add_argument("--top", type=int, default=30, help="rows to print (default: %(default)s)")

    p = sub.add_parser("fwdext", help="detect forward extension via corpus descendants")
    p.add_argument("--embeddings", default="qwen3", help="hashing | qwen3 | st:<model>")
    p.add_argument("--top", type=int, default=20, help="rows to print (default: %(default)s)")

    p = sub.add_parser("remix", help="build source-remix pools")
    p.add_argument("--mode", choices=["skip"], default="skip")
    p.add_argument("--min-ancestors", type=int, default=2,
                   help="min corpus papers a target must cite (default: %(default)s)")
    p.add_argument("--min-sources", type=int, default=4,
                   help="min pooled sources with content (default: %(default)s)")
    p.add_argument("--out-dir", default=POOLS_DIR)

    p = sub.add_parser("score", help="rank generated ideas by Harbor task-worthiness")
    p.add_argument("--embeddings", help="hashing | qwen3 | st:<model> (enables recall/relevance signals)")
    p.add_argument("--top", type=int, default=20, help="how many to print (default: %(default)s)")

    p = sub.add_parser("analyze", help="distributional + representation report")
    p.add_argument("--split-conditions", action="store_true")
    p.add_argument("--embeddings", help="hashing | qwen3 | st:<model>")

    p = sub.add_parser("archetypes", help="archetype rewrite + operation enrichment")
    p.add_argument("--provider", default="anthropic")

    args = parser.parse_args(argv)
    handler = {
        "ingest": cmd_ingest,
        "validate": cmd_validate,
        "extract": cmd_extract,
        "ideate": cmd_ideate,
        "annotate": cmd_annotate,
        "export": cmd_export,
        "dedupe": cmd_dedupe,
        "remix": cmd_remix,
        "leap": cmd_leap,
        "fwdext": cmd_fwdext,
        "score": cmd_score,
        "analyze": cmd_analyze,
        "archetypes": cmd_archetypes,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
