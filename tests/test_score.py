import pytest

from twice.model import Turn
from twice.score import format_scores, judge_agreement, miner_metrics, threshold_sweep, wilson


def _turns(n_flagged, n_unflagged):
    turns = []
    for i in range(n_flagged):
        turns.append(Turn(f"f{i}", "s", "t", "p", "b", "", "x", "", [], 2.0, True))
    for i in range(n_unflagged):
        turns.append(Turn(f"u{i}", "s", "t", "p", "b", "", "x", "", [], 0.0, False))
    return turns


def _labels(pairs):
    return {i: {"id": i, "is_correction": y} for i, y in pairs}


def test_wilson_interval():
    lo, hi = wilson(8, 10)
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95
    assert wilson(0, 0) == (0.0, 1.0)


def test_precision_and_estimated_recall():
    turns = _turns(100, 900)
    # 8 of 10 labeled flagged are real; 1 of 20 labeled unflagged is a missed correction.
    labels = _labels([(f"f{i}", i < 8) for i in range(10)] + [(f"u{i}", i == 0) for i in range(20)])
    m = miner_metrics(turns, labels, threshold=1.0)
    assert m.precision == pytest.approx(0.8)
    assert m.miss_rate == pytest.approx(0.05)
    # 80 caught out of an estimated 80 + 45 = 125
    assert m.estimated_corrections == pytest.approx(125)
    assert m.recall == pytest.approx(0.64)
    assert m.f1 == pytest.approx(2 * 0.8 * 0.64 / 1.44)


def test_recall_unknown_without_unflagged_labels():
    m = miner_metrics(_turns(10, 10), _labels([("f1", True)]), threshold=1.0)
    assert m.precision == 1.0 and m.recall is None
    assert "needs labels on unflagged" in format_scores(m, [], None)


def test_sweep_weights_back_to_population():
    turns = _turns(100, 900)
    labels = _labels([(f"f{i}", i < 8) for i in range(10)] + [(f"u{i}", i == 0) for i in range(20)])
    rows = {r["threshold"]: r for r in threshold_sweep(turns, labels, 1.0, thresholds=(1.0, 3.0))}
    assert rows[1.0]["precision"] == pytest.approx(0.8)
    assert rows[1.0]["recall"] == pytest.approx(0.64)
    assert rows[3.0]["precision"] is None  # nothing flagged that high
    assert threshold_sweep(turns, _labels([("f1", True)]), 1.0) == []


def test_judge_agreement_and_kappa():
    labels = _labels([("a", True), ("b", True), ("c", False), ("d", False)])
    judgments = {
        "a": {"status": "ok", "is_correction": True},
        "b": {"status": "ok", "is_correction": False},
        "c": {"status": "ok", "is_correction": False},
        "d": {"status": "refused"},
    }
    agreement = judge_agreement(labels, judgments)
    assert agreement["n"] == 3
    assert agreement["accuracy"] == pytest.approx(2 / 3)
    assert agreement["precision"] == 1.0 and agreement["recall"] == 0.5
    assert agreement["confusion"] == {"tp": 1, "fp": 0, "fn": 1, "tn": 1}
    assert judge_agreement(labels, {}) is None
