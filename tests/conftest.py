import json
from pathlib import Path

import pytest

from althist.llm import StepResult, ToolCall
from althist.schema import Idea, PaperRecord, SourceRecord

FULL_TEXT = (
    "Introduction. Gradient-based learning applied to document recognition. "
    "We describe convolutional networks trained with backpropagation on "
    "handwritten digit images. The key limitation of fully connected networks "
    "is the number of parameters. " * 40
)


def make_paper(paper_id: str = "p1") -> PaperRecord:
    return PaperRecord(
        paper_id=paper_id,
        title="Gradient-Based Learning Applied to Document Recognition",
        authors=["Y. LeCun"],
        year=1998,
        abstract="Convolutional networks for document recognition.",
        idea=Idea(
            motivation="Fully connected networks need too many parameters for images.",
            method="Use weight sharing and local receptive fields trained by backprop.",
        ),
        sources=[
            SourceRecord(
                source_id="s1",
                title="Learning representations by back-propagating errors",
                year=1986,
                abstract="Backpropagation learns internal representations.",
                full_text=FULL_TEXT,
            ),
            SourceRecord(
                source_id="s2",
                title="Phoneme recognition using time-delay neural networks",
                year=1989,
                abstract="Time-delay networks share weights over time.",
            ),
            SourceRecord(
                source_id="s3",
                title="Handwritten digit recognition with a back-propagation network",
                year=1990,
                abstract="A constrained network recognizes digits.",
                full_text="Constrained backprop network. " * 100,
            ),
        ],
    )


@pytest.fixture
def paper() -> PaperRecord:
    return make_paper()


@pytest.fixture
def papers_dir(tmp_path: Path) -> Path:
    d = tmp_path / "papers"
    d.mkdir()
    for pid in ("p1", "p2"):
        (d / f"{pid}.json").write_text(make_paper(pid).model_dump_json())
    return d


class MockProvider:
    """Scripted provider: explores two tools, then submits an idea."""

    name = "mock"
    model = "mock-1"

    def __init__(self):
        self._script = [
            [ToolCall("c1", "list_sources", {})],
            [
                ToolCall("c2", "get_abstract", {"source_id": "s1"}),
                ToolCall("c3", "read_span", {"source_id": "s1", "start": 0, "length": 500}),
            ],
            [
                ToolCall(
                    "c4",
                    "submit_idea",
                    {
                        "motivation": (
                            "Parameter explosion limits fully connected nets on images: "
                            "the weight count grows with the square of the input size, "
                            "making training data-hungry and generalization poor on "
                            "realistic document-recognition inputs."
                        ),
                        "method": (
                            "Share weights across spatial positions via local receptive "
                            "fields, so the same feature detectors sweep the image, and "
                            "train the resulting constrained network end to end with "
                            "backpropagation on raw pixels."
                        ),
                    },
                )
            ],
        ]
        self._turn = 0

    def start_conversation(self, system: str, user: str) -> dict:
        return {"system": system, "messages": [{"role": "user", "content": user}]}

    def step(self, state: dict, tools: list[dict]) -> StepResult:
        calls = self._script[self._turn] if self._turn < len(self._script) else []
        self._turn += 1
        state["messages"].append({"role": "assistant", "content": f"turn {self._turn}"})
        return StepResult(
            raw={"turn": self._turn, "tool_calls": [c.name for c in calls]},
            text="",
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage={"output_tokens": 10},
        )

    def append_tool_results(self, state: dict, results) -> None:
        state["messages"].append({"role": "user", "content": json.dumps([r[0] for r in results])})

    def structured_json(self, system: str, user: str, schema: dict) -> dict:
        raise NotImplementedError

    def simple_text(self, system: str, user: str, max_tokens: int = 1024) -> str:
        return "Replace a global component with a locally constrained one."
