import json

from althist.corpus import Corpus
from althist.ingest import PdfPool, ingest, norm_title, slugify


def _make_source_repo(tmp_path):
    repo = tmp_path / "low-compute-ml"
    (repo / "paper_text").mkdir(parents=True)
    (repo / "all_papers_1").mkdir()
    (repo / "papers").mkdir()

    mapping = {
        "md5a": {
            "md5": "md5a",
            "rep_filename": "Depth Zero Paper.pdf",
            "true_title": "Depth Zero Paper",
            "true_authors": ["A. Author"],
            "year": 1995,
            "sources": [
                {"title": "Cited Classic One", "doi": "10.1/x", "doi_status": "ok", "year": 1986},
                {"title": "Cited Classic Two", "doi": None, "doi_status": "rejected", "year": 1989},
                {"title": "Cited Classic Three", "doi": None, "doi_status": "rejected", "year": 1990},
                {"title": "Cited Classic Four", "doi": None, "doi_status": "rejected", "year": 1991},
            ],
        },
        "md5b": {  # too few sources -> skipped
            "md5": "md5b",
            "rep_filename": "Thin Paper.pdf",
            "true_title": "Thin Paper",
            "year": 1990,
            "sources": [{"title": "Only One", "doi": None, "doi_status": "rejected", "year": 1980}],
        },
        "md5c": {  # unresolved identity -> skipped
            "md5": "md5c",
            "rep_filename": "Mystery.pdf",
            "true_title": None,
            "sources": [],
        },
    }
    (repo / "paper_sources.json").write_text(json.dumps(mapping))
    (repo / "paper_text" / "Depth Zero Paper.txt").write_text("Full text of depth zero paper.")
    # source pdf whose text was already extracted (depth-0 copy fast path)
    (repo / "all_papers_1" / "Cited Classic One.pdf").write_bytes(b"%PDF-fake")
    (repo / "paper_text" / "Cited Classic One.txt").write_text("Text of cited classic one.")
    return repo


def test_ingest_builds_valid_corpus(tmp_path):
    repo = _make_source_repo(tmp_path)
    papers_dir = tmp_path / "papers"
    stats = ingest(repo, papers_dir=papers_dir, cache_dir=tmp_path / "cache", log=lambda *_: None)

    assert stats.papers_written == 1
    assert stats.skipped_few_sources == 1
    assert stats.skipped_no_identity == 1
    assert stats.sources_total == 4
    assert stats.sources_with_full_text == 1
    assert stats.sources_bare == 3

    corpus = Corpus(papers_dir)
    paper = corpus.load("depth-zero-paper-1995")
    assert paper.full_text and "depth zero" in paper.full_text
    matched = next(s for s in paper.sources if s.title == "Cited Classic One")
    assert matched.full_text == "Text of cited classic one."


def test_ingest_reads_abstract_cache_without_network(tmp_path, monkeypatch):
    from althist.ingest import AbstractCache

    repo = _make_source_repo(tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    AbstractCache(cache_dir / "openalex_abstracts.json").data.update({})
    # seed a cache with one source's abstract, on disk
    import json as _json
    (cache_dir / "openalex_abstracts.json").write_text(_json.dumps({"10.1/x": "Cached abstract text."}))

    # any network fetch would be a bug: make both paths explode if hit
    monkeypatch.setattr(AbstractCache, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live fetch")))
    monkeypatch.setattr(AbstractCache, "_s2_post", staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("s2 hit"))))

    papers_dir = tmp_path / "papers"
    ingest(repo, papers_dir=papers_dir, cache_dir=cache_dir, log=lambda *_: None)  # fetch_abstracts=False
    paper = Corpus(papers_dir).load("depth-zero-paper-1995")
    cited_one = next(s for s in paper.sources if s.title == "Cited Classic One")
    assert cited_one.abstract == "Cached abstract text."


def test_ingest_preserves_extracted_idea(tmp_path):
    repo = _make_source_repo(tmp_path)
    papers_dir = tmp_path / "papers"
    ingest(repo, papers_dir=papers_dir, cache_dir=tmp_path / "cache", log=lambda *_: None)
    path = papers_dir / "depth-zero-paper-1995.json"
    record = json.loads(path.read_text())
    record["idea"] = {"motivation": "m", "method": "s"}
    path.write_text(json.dumps(record))

    ingest(repo, papers_dir=papers_dir, cache_dir=tmp_path / "cache", log=lambda *_: None)
    assert json.loads(path.read_text())["idea"]["motivation"] == "m"


def test_pdf_pool_matching(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    (d / "Mixtures of Dirichlet Processes with Applications.pdf").write_bytes(b"x")
    (d / "Antoniak_1974_Mixtures_of_Dirichlet_Processes.pdf").write_bytes(b"x")
    pool = PdfPool.build([d])
    exact = pool.match("Mixtures of Dirichlet Processes with Applications")
    assert exact and exact.name.startswith("Mixtures")
    # truncated filename stem matches by long prefix
    prefixed = pool.match("Mixtures of Dirichlet Processes with Applications to Bayesian Problems")
    assert prefixed is not None
    assert pool.match("Completely Unrelated Title Here") is None


def test_ingest_drops_self_citations(tmp_path):
    repo = _make_source_repo(tmp_path)
    mapping = json.loads((repo / "paper_sources.json").read_text())
    # self-citation: same title with punctuation noise (exact normalized match)
    mapping["md5a"]["sources"].append(
        {"title": "Depth Zero: Paper!", "doi": None, "doi_status": "rejected", "year": 1994}
    )
    # a source whose matched PDF is a byte-identical copy of the depth-0 paper
    mapping["md5a"]["sources"].append(
        {"title": "Disguised Copy Source", "doi": None, "doi_status": "rejected", "year": 1993}
    )
    (repo / "paper_sources.json").write_text(json.dumps(mapping))
    (repo / "all_papers_1" / "Disguised Copy Source.pdf").write_bytes(b"%PDF-fake2")
    (repo / "paper_text" / "Disguised Copy Source.txt").write_text(
        "Full text of depth zero paper."  # identical to the paper's own text
    )

    papers_dir = tmp_path / "papers"
    stats = ingest(repo, papers_dir=papers_dir, cache_dir=tmp_path / "cache", log=lambda *_: None)
    assert stats.sources_self_dropped == 1
    assert stats.sources_text_leak_blocked == 1

    paper = Corpus(papers_dir).load("depth-zero-paper-1995")
    titles = [s.title for s in paper.sources]
    assert "Depth Zero: Paper!" not in titles
    disguised = next(s for s in paper.sources if s.title == "Disguised Copy Source")
    assert disguised.full_text is None  # kept as a bare source, own text stripped


def test_s2_batch_fill_caches_definitive_only(tmp_path, monkeypatch):
    from althist.ingest import AbstractCache

    cache = AbstractCache(tmp_path / "abs.json")
    # S2 returns: has abstract, known-but-no-abstract, unknown DOI (null slot)
    responses = [[
        {"abstract": "An abstract for the first paper."},
        {"abstract": None},
        None,
    ]]
    monkeypatch.setattr(AbstractCache, "_s2_post", staticmethod(lambda dois, headers: responses.pop(0)))

    cache.fill_from_s2(["10.1/a", "10.2/b", "10.3/c"], log=lambda *_: None)
    assert cache.data["10.1/a"] == "An abstract for the first paper."
    assert cache.data["10.2/b"] is None       # known, no abstract -> cached as None
    assert "10.3/c" not in cache.data          # unknown -> left uncached for a later pass


def test_s2_batch_whole_failure_leaves_uncached(tmp_path, monkeypatch):
    from althist.ingest import AbstractCache

    cache = AbstractCache(tmp_path / "abs.json")
    monkeypatch.setattr(AbstractCache, "_s2_post", staticmethod(lambda dois, headers: None))
    cache.fill_from_s2(["10.1/a", "10.2/b"], log=lambda *_: None)
    assert cache.data == {}


def test_norm_and_slug():
    assert norm_title("A Bayesian, Method! ") == "a bayesian method"
    assert slugify("A Bayesian Method (1992)") == "a-bayesian-method-1992"


def test_same_title_prefix_tolerance():
    from althist.ingest import _same_title

    long_a = norm_title("A Bayesian Method for the Induction of Probabilistic Networks from Data")
    long_b = norm_title("A Bayesian Method for the Induction of Probabilistic Networks (Report SMI-91-1)")
    assert _same_title(long_a, long_a)
    assert _same_title(long_b, long_a) or _same_title(long_a, long_b)
    # short titles only match exactly, never by prefix
    assert not _same_title("depth zero", "depth zero paper")


def test_clean_pdf_text_decodes_cid_glyphs():
    from althist.ingest import clean_pdf_text

    cid = "/C77/C101/C97/C110 /C83/C104/C105/C102/C116"  # "Mean Shift"
    assert clean_pdf_text(cid) == "Mean Shift"
    # ordinary text with a stray token is left untouched
    normal = "See section /C12 for details on convergence."
    assert clean_pdf_text(normal) == normal
    assert clean_pdf_text(None) is None
    assert clean_pdf_text("") is None
