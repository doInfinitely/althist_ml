import json

from althist.corpus import MAX_SPAN_CHARS, Corpus, SourceSet
from althist.tools import ToolDispatcher


def test_corpus_load_and_validate(papers_dir):
    corpus = Corpus(papers_dir)
    assert corpus.paper_ids() == ["p1", "p2"]
    assert corpus.validate() == []
    paper = corpus.load("p1")
    assert len(paper.sources) == 3


def test_corpus_reports_problems(papers_dir):
    (papers_dir / "bad.json").write_text('{"paper_id": "bad", "title": "t", "sources": []}')
    problems = Corpus(papers_dir).validate()
    assert any("no sources" in p for p in problems)


def test_list_sources_and_abstract(paper):
    ss = SourceSet(paper)
    listing = ss.list_sources()
    assert [s["source_id"] for s in listing] == ["s1", "s2", "s3"]
    assert listing[0]["has_full_text"] and not listing[1]["has_full_text"]
    assert "Backpropagation" in ss.get_abstract("s1")["abstract"]


def test_read_span_bounds(paper):
    ss = SourceSet(paper)
    span = ss.read_span("s1", start=0, length=10**9)
    assert len(span["text"]) <= MAX_SPAN_CHARS
    assert span["total_chars"] == len(paper.sources[0].full_text)
    # out-of-range start clamps instead of erroring
    tail = ss.read_span("s1", start=10**9)
    assert tail["text"] == ""
    # source without full text returns a usable error payload
    assert "error" in ss.read_span("s2")


def test_search(paper):
    ss = SourceSet(paper)
    result = ss.search("backprop")
    assert result["hits"]
    assert all("snippet" in h for h in result["hits"])
    scoped = ss.search("digit", source_id="s3")
    assert all(h["source_id"] == "s3" for h in scoped["hits"])


MOTIVATION = (
    "Fully connected networks require a number of parameters that grows with "
    "the square of the input dimension, which makes them impractical for "
    "images and invites overfitting on small labeled datasets."
)
METHOD = (
    "Constrain the architecture with local receptive fields and shared "
    "weights across spatial positions, training the resulting convolutional "
    "network end to end with backpropagation on raw pixel inputs."
)


def test_dispatcher_submit_and_errors(paper):
    d = ToolDispatcher(SourceSet(paper))
    content, is_error = d.dispatch("get_abstract", {"source_id": "nope"})
    assert is_error and "unknown source_id" in json.loads(content)["error"]
    content, is_error = d.dispatch("not_a_tool", {})
    assert is_error
    content, is_error = d.dispatch(
        "submit_idea", {"motivation": MOTIVATION, "method": METHOD}
    )
    assert not is_error
    assert d.submitted is not None and d.submitted.motivation == MOTIVATION


def test_dispatcher_rejects_truncated_submit(paper):
    d = ToolDispatcher(SourceSet(paper))
    # a parser-truncated tool call: motivation present, method missing
    content, is_error = d.dispatch("submit_idea", {"motivation": MOTIVATION})
    assert is_error
    err = json.loads(content)["error"]
    # the error must tell the model exactly what arrived
    assert f"motivation={len(MOTIVATION)} chars" in err and "method=MISSING" in err
    assert d.submitted is None  # nothing accepted
    # empty-string field is also rejected
    _, is_error = d.dispatch("submit_idea", {"motivation": MOTIVATION, "method": "  "})
    assert is_error and d.submitted is None


def test_dispatcher_rejects_probe_and_stub_submissions(paper):
    d = ToolDispatcher(SourceSet(paper))
    # tool-probing stubs (observed live after parser truncation errors)
    for mot, met in [
        ("Test motivation.", "Test method."),
        ("placeholder", METHOD),
        (MOTIVATION, "gap"),
    ]:
        content, is_error = d.dispatch("submit_idea", {"motivation": mot, "method": met})
        assert is_error, (mot, met)
        assert "placeholder or is too short" in json.loads(content)["error"]
    assert d.submitted is None


def test_dispatcher_strips_tool_syntax_debris(paper):
    d = ToolDispatcher(SourceSet(paper))
    # closing tags of the call syntax leaked into the value (observed live)
    dirty = MOTIVATION + "</parameter>\n</invoke>\n"
    _, is_error = d.dispatch("submit_idea", {"motivation": dirty, "method": METHOD})
    assert not is_error
    assert d.submitted.motivation == MOTIVATION  # debris removed


def test_dispatcher_keeps_first_valid_submission(paper):
    d = ToolDispatcher(SourceSet(paper))
    d.dispatch("submit_idea", {"motivation": MOTIVATION, "method": METHOD})
    d.dispatch("submit_idea", {"motivation": MOTIVATION + " (v2)", "method": METHOD})
    assert d.submitted.motivation == MOTIVATION  # later calls cannot clobber it
