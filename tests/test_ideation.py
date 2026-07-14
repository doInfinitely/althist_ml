import json

from conftest import MockProvider

from althist.ideation import load_run_results, run_ideation
from althist.schema import Condition


def test_run_ideation_produces_idea_and_transcript(paper, tmp_path):
    result = run_ideation(paper, Condition(), MockProvider(), runs_dir=tmp_path)
    assert result.error is None
    assert result.idea is not None
    assert "Share weights" in result.idea.method
    assert result.n_turns == 3
    assert result.n_tool_calls == 4  # list, abstract, span, submit

    lines = [json.loads(l) for l in open(result.transcript_path)]
    kinds = [e["kind"] for e in lines]
    assert kinds[0] == "run_meta"
    assert "final_idea" in kinds
    assert kinds.count("assistant_message") == 3
    assert kinds.count("tool_call") == kinds.count("tool_result") == 4
    meta = lines[0]["payload"]
    assert meta["model"] == "mock-1"
    assert meta["tools"]  # tool schemas recorded for replay
    # events are ordered and timestamped for teacher-forcing replay
    assert [e["seq"] for e in lines] == list(range(len(lines)))
    assert all(e["timestamp"] for e in lines)


def test_run_ideation_steered_condition_in_prompt(paper, tmp_path):
    condition = Condition(pattern="failure_risk_gap", paradigm="robustification")
    result = run_ideation(paper, condition, MockProvider(), runs_dir=tmp_path)
    meta = json.loads(open(result.transcript_path).readline())["payload"]
    assert "Failure / Risk Gap" in meta["system"]
    assert "Robustification" in meta["system"]
    assert result.run_id.startswith("p1__failure_risk_gap__robustification")


def test_run_without_submission_records_error(paper, tmp_path):
    provider = MockProvider()
    provider._script = [[]]  # model ends turn immediately, never submits
    result = run_ideation(paper, Condition(), provider, runs_dir=tmp_path)
    assert result.idea is None
    assert result.error is not None


def test_load_run_results_roundtrip(paper, tmp_path):
    run_ideation(paper, Condition(pattern="evidence_gap"), MockProvider(), runs_dir=tmp_path)
    results = load_run_results(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.paper_id == "p1"
    assert r.condition.pattern == "evidence_gap"
    assert r.idea is not None
    assert r.n_turns == 3 and r.n_tool_calls == 4


def test_structured_json_raises_on_empty_content(monkeypatch):
    import althist.llm as llm

    class _Resp:
        stop_reason = "refusal"
        content: list = []

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return _Resp()

    class _Msgs:
        def create(self, **kwargs):
            return _Resp()

        def stream(self, **kwargs):
            return _Stream()

    class _Client:
        messages = _Msgs()

    prov = llm.AnthropicProvider.__new__(llm.AnthropicProvider)
    prov.client = _Client()
    prov.model = "claude-opus-4-8"
    prov.max_tokens = 16000

    import pytest

    with pytest.raises(llm.LLMResponseError):
        prov.structured_json("sys", "user", {"type": "object"})
    with pytest.raises(llm.LLMResponseError):
        prov.simple_text("sys", "user")


def test_ideate_cli_skips_completed(paper, tmp_path, monkeypatch, capsys):
    import althist.cli as cli
    from conftest import MockProvider

    import althist.llm as llm

    monkeypatch.setattr(cli, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "PAPERS_DIR", str(tmp_path / "papers"))
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "p1.json").write_text(paper.model_dump_json())
    monkeypatch.setattr(llm, "make_provider", lambda spec: MockProvider())

    args = type("A", (), {"provider": "mock", "papers": "p1", "limit": None,
                          "pairs": False, "conditions": "blank__blank", "redo": False,
                          "max_turns": 40, "pools": False})()
    assert cli.cmd_ideate(args) == 0
    # second run with a fresh provider must skip, not re-invoke the model
    called = {"n": 0}
    class Counting(MockProvider):
        def step(self, *a, **k):
            called["n"] += 1
            return super().step(*a, **k)
    monkeypatch.setattr(llm, "make_provider", lambda spec: Counting())
    assert cli.cmd_ideate(args) == 0
    assert called["n"] == 0
    assert "skipped (already done)" in capsys.readouterr().out
