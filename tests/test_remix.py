from althist.remix import (
    build_citation_graph,
    build_skip_pool,
    build_skip_pools,
    choose_canonical,
    find_duplicate_groups,
)
from althist.schema import Idea, PaperRecord, SourceRecord


def make(pid, title, year=None, idea=True, sources=(), full_text=None):
    return PaperRecord(
        paper_id=pid,
        title=title,
        year=year,
        full_text=full_text,
        idea=Idea(motivation=f"m-{pid}", method=f"h-{pid}") if idea else None,
        sources=list(sources),
    )


def src(sid, title, abstract="", full_text=None, year=None):
    return SourceRecord(
        source_id=sid, title=title, abstract=abstract, full_text=full_text, year=year
    )


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


def test_duplicate_groups_exact_and_fuzzy():
    papers = [
        make("mcmc-2003", "An Introduction to MCMC for Machine Learning"),
        make("mcmc-2003-9bdd86", "An introduction to MCMC for machine learning"),
        make("bayes-1996", "A Tutorial on Learning With Bayesian Networks (Report MSR-TR-95-06)"),
        make("bayes", "A Tutorial on Learning With Bayesian Networks"),
        make("other", "Support-Vector Networks"),
    ]
    groups = find_duplicate_groups(papers)
    assert sorted(map(sorted, groups)) == [
        ["bayes", "bayes-1996"],
        ["mcmc-2003", "mcmc-2003-9bdd86"],
    ]


def test_canonical_prefers_protected_then_richness():
    rich = make("a-rich", "T Title Alpha Beta Gamma Delta", year=1996,
                sources=[src("s1", "S One", abstract="x"), src("s2", "S Two", abstract="y")])
    poor = make("a-poor", "T Title Alpha Beta Gamma Delta", idea=False)
    papers = {p.paper_id: p for p in (rich, poor)}
    assert choose_canonical(["a-poor", "a-rich"], papers, protected=set()) == "a-rich"
    # a paper with existing run transcripts always wins
    assert choose_canonical(["a-poor", "a-rich"], papers, protected={"a-poor"}) == "a-poor"


# ---------------------------------------------------------------------------
# citation graph
# ---------------------------------------------------------------------------


def test_citation_graph_edges_and_year_sanity():
    a = make("a-1990", "Alpha Learning Machines Considered Useful", year=1990)
    b = make("b-1992", "Beta Networks of Substantial Depth and Width", year=1992)
    d = make(
        "d-1999", "Delta Unification of Alpha and Beta", year=1999,
        sources=[
            src("s1", "Alpha Learning Machines Considered Useful"),
            src("s2", "Beta Networks of Substantial Depth and Width"),
            src("s3", "Unrelated Prior Work"),
        ],
    )
    # cites a 2005 paper: impossible (title collision) -> edge dropped
    w = make(
        "w-1991", "Whiskey Paper of Nineteen Ninety One", year=1991,
        sources=[src("s1", "Zulu Method Published Much Later of Considerable Length")],
    )
    z = make("z-2005", "Zulu Method Published Much Later of Considerable Length", year=2005)
    graph = build_citation_graph({p.paper_id: p for p in (a, b, d, w, z)})
    assert graph["d-1999"] == {"a-1990", "b-1992"}
    assert graph["w-1991"] == set()


# ---------------------------------------------------------------------------
# skip pools
# ---------------------------------------------------------------------------


def _skip_fixture():
    a = make(
        "a-1990", "Alpha Learning Machines Considered Useful", year=1990,
        full_text="alpha body text",
        sources=[
            src("g1", "Grand Prior One", abstract="g1 abs"),
            src("shared", "Shared Grand Prior", abstract=""),  # bare in a
            # cites the *other* intermediate -> must be stripped
            src("leak-b", "Beta Networks of Substantial Depth and Width", abstract="x"),
        ],
    )
    b = make(
        "b-1992", "Beta Networks of Substantial Depth and Width", year=1992,
        full_text="beta body text",
        sources=[
            src("g2", "Grand Prior Two", abstract="g2 abs"),
            src("shared", "Shared Grand Prior", abstract="shared abs"),  # richer copy
            # same title as the target -> must be stripped
            src("leak-d", "Delta Unification of Alpha and Beta", abstract="x"),
            # text identical to an intermediate's own text -> text stripped
            src("leak-txt", "Beta Tech Report Under Another Name", full_text="beta body text"),
        ],
    )
    d = make(
        "d-1999", "Delta Unification of Alpha and Beta", year=1999,
        sources=[
            src("s1", "Alpha Learning Machines Considered Useful"),
            src("s2", "Beta Networks of Substantial Depth and Width"),
        ],
    )
    return a, b, d


def test_skip_pool_leakage_guards_and_merge():
    a, b, d = _skip_fixture()
    pool = build_skip_pool(d, [a, b]).record

    titles = {s.title for s in pool.sources}
    assert "Delta Unification of Alpha and Beta" not in titles  # target stripped
    assert "Beta Networks of Substantial Depth and Width" not in titles  # intermediate
    # identical-text source kept only as title-only (text stripped, no abstract -> kept out)
    leak_txt = [s for s in pool.sources if s.title == "Beta Tech Report Under Another Name"]
    assert leak_txt == []
    # shared source merged, richer copy (with abstract) won
    shared = [s for s in pool.sources if s.title == "Shared Grand Prior"]
    assert len(shared) == 1 and shared[0].abstract == "shared abs"
    assert {s.title for s in pool.sources} == {"Grand Prior One", "Grand Prior Two", "Shared Grand Prior"}

    assert pool.paper_id == "skip__d-1999"
    assert pool.title == "skip__d-1999"  # target title must not leak into the record
    assert pool.idea is not None and pool.idea.motivation == "m-d-1999"
    assert pool.remix["target_paper_id"] == "d-1999"
    assert pool.remix["ancestor_ids"] == ["a-1990", "b-1992"]
    assert pool.remix["is_review"] is False


def test_build_skip_pools_thresholds():
    a, b, d = _skip_fixture()
    papers = {p.paper_id: p for p in (a, b, d)}
    pools, skipped = build_skip_pools(papers, min_ancestors=2, min_sources=2)
    assert [p.record.paper_id for p in pools] == ["skip__d-1999"]
    assert skipped["few_ancestors"] == 2  # a and b cite no corpus papers

    # raising the content floor drops the pool (only 3 contentful sources)
    pools, skipped = build_skip_pools(papers, min_ancestors=2, min_sources=4)
    assert pools == [] and skipped["few_pool_sources"] == 1


def test_review_target_tagged():
    a, b, d = _skip_fixture()
    d.title = "A Tutorial Review of Alpha and Beta Unified"
    pool = build_skip_pool(d, [a, b]).record
    assert pool.remix["is_review"] is True


# ---------------------------------------------------------------------------
# leap scoring
# ---------------------------------------------------------------------------


def test_leap_scores_sign_behavior():
    from althist.embeddings import HashingBackend
    from althist.leap import compute_leap

    a, b, d = _skip_fixture()
    pool = build_skip_pool(d, [a, b]).record
    backend = HashingBackend()
    anc_ideas = {p.paper_id: p.idea for p in (a, b)}

    # an idea that literally restates the target's idea: leap wins the margin
    hit = compute_leap(pool, d.idea, anc_ideas, backend)
    # an idea that restates an intermediate's idea: intermediate recall wins
    stuck = compute_leap(pool, a.idea, anc_ideas, backend)

    assert hit.leap_excess > hit.intermediate_excess
    assert stuck.intermediate_excess > stuck.leap_excess
    assert stuck.closest_ancestor_id == "a-1990"
    assert hit.leap_margin > 0 > stuck.leap_margin
    assert hit.n_sources == len(pool.sources)
    assert 0.0 <= hit.h <= 1.0


def test_fwdext_three_way_classification():
    from althist.embeddings import HashingBackend
    from althist.fwdext import classify_episodes

    a, b, d = _skip_fixture()
    # descendant e of d, with its own distinct idea
    e = make("e-2005", "Epsilon Successor Method Refining Delta", year=2005)
    episodes = [
        ("m", "c1", d.idea),   # restates the target -> regurgitation
        ("m", "c2", e.idea),   # restates the descendant -> forward extension
        ("m", "c3", Idea(motivation="totally unrelated botany studies of ferns",
                         method="catalog fern spore morphology in the field")),
    ]
    rows = classify_episodes(d, episodes, {"e-2005": e.idea},
                             HashingBackend(), threshold=0.15)
    assert [r.label for r in rows] == ["regurgitation", "forward_extension", "clean"]
    assert rows[1].closest_descendant_id == "e-2005"
