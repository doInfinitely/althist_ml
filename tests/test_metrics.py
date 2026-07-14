import math

import numpy as np
import pytest

from althist.metrics.distributional import distribution, jsd, normalized_entropy, tvd
from althist.metrics.representation import representation_scores


def test_distribution_and_entropy():
    labels = ["a", "a", "b", "c"]
    p = distribution(labels, ["a", "b", "c", "d"])
    assert p == {"a": 0.5, "b": 0.25, "c": 0.25, "d": 0.0}
    uniform = {k: 0.25 for k in "abcd"}
    assert normalized_entropy(uniform) == pytest.approx(1.0)
    point = {"a": 1.0, "b": 0.0}
    assert normalized_entropy(point) == pytest.approx(0.0)


def test_distribution_rejects_unknown_labels():
    with pytest.raises(ValueError):
        distribution(["z"], ["a", "b"])


def test_tvd_known_values():
    p = {"a": 1.0, "b": 0.0}
    q = {"a": 0.0, "b": 1.0}
    assert tvd(p, q) == pytest.approx(1.0)
    assert tvd(p, p) == pytest.approx(0.0)
    assert tvd({"a": 0.5, "b": 0.5}, {"a": 0.75, "b": 0.25}) == pytest.approx(0.25)


def test_jsd_bounds_and_symmetry():
    p = {"a": 1.0, "b": 0.0}
    q = {"a": 0.0, "b": 1.0}
    assert jsd(p, q) == pytest.approx(1.0)  # base-2 JSD of disjoint dists
    assert jsd(p, p) == pytest.approx(0.0)
    r = {"a": 0.3, "b": 0.7}
    assert jsd(p, r) == pytest.approx(jsd(r, p))


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def test_representation_entropy_extremes():
    # proposal equidistant from all sources -> H = 1
    sources = np.eye(4)
    proposal = _unit(np.ones(4))
    scores = representation_scores(proposal, sources)
    assert scores.h == pytest.approx(1.0)
    assert scores.top_gap == pytest.approx(0.0)

    # proposal aligned with one source -> low H, large top gap
    aligned = representation_scores(sources[0], sources)
    assert aligned.h < 0.9
    assert aligned.top_gap == pytest.approx(1.0)
    assert aligned.h < scores.h


def test_mean_similarity_blocks_entropy_gaming():
    # An "unrelated" proposal (orthogonal to all sources) has near-uniform
    # similarities -> high H, but mean similarity ~0 exposes it.
    sources = np.array([[1.0, 0, 0, 0], [0.9, 0.1, 0, 0], [0.8, 0.2, 0, 0]])
    unrelated = np.array([0.0, 0.0, 0.0, 1.0])
    related = _unit(np.array([1.0, 0.1, 0.0, 0.0]))
    s_unrelated = representation_scores(unrelated, sources)
    s_related = representation_scores(related, sources)
    assert s_unrelated.h >= s_related.h - 1e-9
    assert s_unrelated.mean_similarity < 0.1 < s_related.mean_similarity


def test_contamination_excess_is_baseline_corrected():
    sources = np.array([[1.0, 0, 0], [0.8, 0.6, 0.0], [0.9, 0.1, 0.0]])
    gt = _unit(np.array([0.7, 0.3, 0.65]))
    # regurgitation: proposal == ground truth -> large positive excess
    regurgitated = representation_scores(gt, sources, ground_truth=gt)
    # on-topic but novel: close to sources, farther from gt -> smaller excess
    on_topic = representation_scores(_unit(np.array([1.0, 0.3, 0.0])), sources, ground_truth=gt)
    assert regurgitated.excess_gt_similarity > on_topic.excess_gt_similarity
    assert regurgitated.contamination_penalty >= on_topic.contamination_penalty
    assert regurgitated.gt_similarity == pytest.approx(1.0)
    # margin absorbs small excess
    lenient = representation_scores(
        _unit(np.array([1.0, 0.3, 0.0])), sources, ground_truth=gt, contamination_margin=1.0
    )
    assert lenient.contamination_penalty == 0.0


def test_representation_requires_two_sources():
    with pytest.raises(ValueError):
        representation_scores(np.ones(3), np.ones((1, 3)))


def test_b_composition():
    sources = np.eye(3)
    proposal = _unit([1.0, 1.0, 0.0])
    s = representation_scores(proposal, sources)
    assert s.b == pytest.approx(s.centroid_similarity + s.h - s.top_gap)
    assert not math.isnan(s.b)
