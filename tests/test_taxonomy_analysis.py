import pytest

from althist.analysis import compare_distributions, format_report
from althist.archetype import operation_enrichment, operation_family
from althist.prompts import generation_system
from althist.schema import (
    AnnotatedIdea,
    Annotation,
    AxisLabels,
    Condition,
    Diagnostics,
    Idea,
)
from althist.taxonomy import METHOD_PARADIGMS, OPPORTUNITY_PATTERNS, fanout_conditions


def test_taxonomy_shape():
    assert len(OPPORTUNITY_PATTERNS) == 7
    assert len(METHOD_PARADIGMS) == 7
    assert not set(OPPORTUNITY_PATTERNS) & set(METHOD_PARADIGMS)


def test_fanout_counts():
    assert len(fanout_conditions()) == 15  # blank + 7 + 7
    assert len(fanout_conditions(include_pairs=True)) == 64  # + 49
    blank = fanout_conditions()[0]
    assert blank.key == "blank__blank"


def test_generation_system_steering():
    blank = generation_system(Condition())
    steered = generation_system(Condition(pattern="bridge_opportunity"))
    assert "Steering constraint" not in blank
    assert "Bridge Opportunity" in steered


def _item(source, opp, par, paper_id="p1", condition=None):
    return AnnotatedIdea(
        paper_id=paper_id,
        source=source,
        condition=condition,
        idea=Idea(motivation="m", method="s"),
        annotation=Annotation(
            opportunity_pattern=AxisLabels(primary=opp),
            method_paradigm=AxisLabels(primary=par),
            confidence_opportunity=0.9,
            confidence_paradigm=0.9,
            diagnostics=Diagnostics(),
        ),
    )


def test_compare_distributions():
    items = [
        _item("human", "explanation_gap", "formal_derivation"),
        _item("human", "evidence_gap", "empirical_mapping"),
        _item("m1", "bridge_opportunity", "synthesis_unification"),
        _item("m1", "bridge_opportunity", "synthesis_unification"),
    ]
    rows = compare_distributions(items)
    by = {(r.source, r.axis): r for r in rows}
    human = by[("human", "opportunity_pattern")]
    model = by[("m1", "opportunity_pattern")]
    assert human.tvd is None and model.tvd == pytest.approx(1.0)
    assert model.entropy == pytest.approx(0.0)
    assert human.entropy > model.entropy
    report = format_report(rows)
    assert "m1" in report and "human" in report


def test_compare_distributions_split_conditions():
    c = Condition(pattern="bridge_opportunity")
    items = [
        _item("human", "explanation_gap", "formal_derivation"),
        _item("m1", "bridge_opportunity", "synthesis_unification", condition=c),
        _item("m1", "evidence_gap", "empirical_mapping", condition=Condition()),
    ]
    rows = compare_distributions(items, split_conditions=True)
    sources = {r.source for r in rows}
    assert "m1[bridge_opportunity__blank]" in sources
    assert "m1[blank__blank]" in sources


def test_compare_requires_human_reference():
    with pytest.raises(ValueError):
        compare_distributions([_item("m1", "evidence_gap", "empirical_mapping")])


def test_operation_family():
    assert operation_family("Integrate two separate methods into one.") == "integrate"
    assert operation_family("Integrating streams of evidence.") == "integrate"
    assert operation_family("Replaces a brittle module.") == "replace"
    assert operation_family("Decouple two confounded mechanisms.") == "decouple"
    assert operation_family("Zorble the input.") == "zorble"


def test_operation_enrichment_sign():
    model = ["Integrate A and B."] * 8 + ["Replace X with Y."] * 2
    human = ["Replace X with Y."] * 8 + ["Integrate A and B."] * 2
    enrichment = {e.operation: e for e in operation_enrichment(model, human)}
    assert enrichment["integrate"].log_odds > 0
    assert enrichment["replace"].log_odds < 0
