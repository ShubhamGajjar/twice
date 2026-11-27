from twice.label import pick_sample, run_labeling
from twice.model import Turn
from twice.store import latest_by_id, read_jsonl


def _turns():
    flagged = [Turn(f"f{i}", "s", "t", "p", "b", "2026-11-10T10:00:00Z", f"no, wrong {i}", "Agent said: hi", [], 2.0, True) for i in range(20)]
    other = [Turn(f"u{i}", "s", "t", "p", "b", "2026-11-10T10:00:00Z", f"add feature {i}", "", [], 0.0, False) for i in range(50)]
    return flagged + other


def test_sample_is_stratified_and_skips_labeled():
    sample = pick_sample(_turns(), {"f0", "f1"}, n=10, unflagged_share=0.3, seed=1)
    assert len(sample) == 10
    assert sum(t.is_candidate for t in sample) == 7
    assert not {"f0", "f1"} & {t.id for t in sample}


def test_sample_fills_from_other_stratum_when_short():
    sample = pick_sample(_turns()[:3] + _turns()[20:], set(), n=10, unflagged_share=0.3, seed=1)
    assert len(sample) == 10 and sum(t.is_candidate for t in sample) == 3


def test_labeling_loop_writes_append_only(tmp_path):
    path = tmp_path / "labels.jsonl"
    sample = _turns()[:3]
    answers = iter(["y", "Never say wrong.", "maybe", "n", "q"])
    out = []
    written = run_labeling(sample, path, 1.0, ask=lambda _: next(answers), say=out.append)
    assert written == 2
    rows = latest_by_id(read_jsonl(path))
    assert rows["f0"]["is_correction"] is True and rows["f0"]["lesson"] == "Never say wrong."
    assert rows["f1"]["is_correction"] is False and rows["f1"]["stratum"] == "flagged"
    assert any("You said:" in line for line in out)
