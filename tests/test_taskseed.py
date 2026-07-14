import pytest

from althist.taskseed import (
    PARADIGM_TO_SHAPE,
    SHAPE_VERIFIABILITY,
    rank_seeds,
    score_idea,
)


def test_every_paradigm_maps_to_a_known_shape():
    from althist.taxonomy import METHOD_PARADIGMS

    assert set(PARADIGM_TO_SHAPE) == set(METHOD_PARADIGMS)
    for shape in PARADIGM_TO_SHAPE.values():
        assert shape in SHAPE_VERIFIABILITY


def test_recall_safety_penalizes_regurgitation():
    # high excess similarity to the ground-truth paper => low recall safety
    regurgitated = score_idea("p", "m", "blank__blank", "empirical_mapping",
                              excess_gt_similarity=0.2)
    novel = score_idea("p", "m", "blank__blank", "empirical_mapping",
                       excess_gt_similarity=0.0)
    assert regurgitated.components["recall_safety"] == pytest.approx(0.0)
    assert novel.components["recall_safety"] == pytest.approx(1.0)
    assert novel.composite > regurgitated.composite


def test_synthesis_shape_scores_below_verifiable_shapes():
    synth = score_idea("p", "m", "blank__synthesis_unification", "synthesis_unification")
    formal = score_idea("p", "m", "blank__formal_derivation", "formal_derivation")
    assert synth.shape == "synthesis"
    assert formal.shape == "research-infer"
    assert formal.components["shape_verifiability"] > synth.components["shape_verifiability"]
    assert formal.composite > synth.composite


def test_missing_signals_drop_out_and_are_reported():
    # only paradigm known (no embeddings, no annotation)
    s = score_idea("p", "m", "puzzle_contradiction__optimization_search", "optimization_search")
    assert set(s.components) == {"shape_verifiability"}
    assert "recall_safety" in s.missing and "specificity" in s.missing
    assert s.composite == pytest.approx(SHAPE_VERIFIABILITY["optimize-a-metric"])


def test_annotation_signals_feed_specificity():
    good = score_idea("p", "m", "blank__blank", "robustification",
                     bottleneck_specificity=3, surface_stitching_score=0, boilerplate_score=0)
    weak = score_idea("p", "m", "blank__blank", "robustification",
                     bottleneck_specificity=0, surface_stitching_score=3, boilerplate_score=3)
    assert good.components["specificity"] == pytest.approx(1.0)
    assert weak.components["anti_stitching"] == pytest.approx(0.0)
    assert good.composite > weak.composite


def test_rank_orders_by_composite():
    a = score_idea("p", "m", "c1", "formal_derivation", excess_gt_similarity=0.0,
                   bottleneck_specificity=3)
    b = score_idea("p", "m", "c2", "synthesis_unification", excess_gt_similarity=0.2,
                   bottleneck_specificity=0)
    ranked = rank_seeds([b, a])
    assert [r.condition_key for r in ranked] == ["c1", "c2"]


def test_paradigm_none_yields_no_shape():
    s = score_idea("p", "m", "blank__blank", None, mean_source_similarity=0.5)
    assert s.shape is None
    assert "shape_verifiability" in s.missing
    assert s.components == {"source_relevance": pytest.approx(0.5)}


def test_recall_safety_worst_of_target_and_descendant():
    # low excess to the target but high excess to a descendant (forward
    # extension) must gate just as hard as direct regurgitation
    fwd = score_idea("p", "m", "blank__blank", "empirical_mapping",
                     excess_gt_similarity=0.02, max_descendant_excess=0.30)
    direct = score_idea("p", "m", "blank__blank", "empirical_mapping",
                        excess_gt_similarity=0.30)
    clean = score_idea("p", "m", "blank__blank", "empirical_mapping",
                       excess_gt_similarity=0.02, max_descendant_excess=0.01)
    assert fwd.components["recall_safety"] == 0.0
    assert fwd.components["recall_safety"] == direct.components["recall_safety"]
    assert clean.components["recall_safety"] > 0.8
    # descendant term alone (no GT excess) still produces the signal
    only_desc = score_idea("p", "m", "blank__blank", "empirical_mapping",
                           max_descendant_excess=0.30)
    assert only_desc.components["recall_safety"] == 0.0
