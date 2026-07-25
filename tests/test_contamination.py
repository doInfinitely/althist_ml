from althist.metrics.contamination import COPY_RECALL_SCALE, ngram_copy


def test_verbatim_reproduction_scores_high():
    ref = ("we introduce an upper confidence bound policy that achieves "
           "logarithmic regret for the multi armed bandit problem")
    # a near-verbatim copy shares almost all its 4-grams with the reference
    copy = ngram_copy(ref, ref)
    assert copy == 1.0


def test_topical_but_reworded_idea_scores_low():
    ref = ("we introduce an upper confidence bound policy that achieves "
           "logarithmic regret for the multi armed bandit problem")
    # same topic, different phrasing -> few shared 4-grams
    idea = ("our approach selects arms by an optimistic index and we prove a "
            "sublinear bound on cumulative loss in sequential allocation")
    assert ngram_copy(idea, ref) < 0.05


def test_short_or_empty_inputs_are_safe():
    assert ngram_copy("too short", "reference text here") == 0.0
    assert ngram_copy("a b c d e f", None) == 0.0
    assert ngram_copy("", "some reference text at all") == 0.0


def test_containment_is_directional():
    # containment normalises by the idea's n-grams, not the reference's, so a
    # short idea fully contained in a long reference scores 1.0
    idea = "gradient descent with momentum accelerates convergence"
    ref = ("in optimisation, gradient descent with momentum accelerates "
           "convergence and damps oscillations across many problem classes")
    assert ngram_copy(idea, ref) == 1.0


def test_scale_is_sane():
    # the gate scale must be well below 1.0 (full copy) and above the corpus p99
    assert 0.0 < COPY_RECALL_SCALE < 0.2
