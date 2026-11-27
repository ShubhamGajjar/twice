import json

import twice.llm
from twice.cli import main
from twice.store import JUDGMENTS, LABELS, META, REPORT_JSON, REPORT_MD, TURNS, read_jsonl


def test_sources(demo_root, capsys):
    assert main(["sources", "--claude-dir", str(demo_root)]) == 0
    out = capsys.readouterr().out
    assert "3 Claude Code sessions" in out and "-work-webapp" in out


def test_mine_writes_outputs(demo_root, workdir, capsys):
    assert main(["mine", "--claude-dir", str(demo_root), "--workdir", str(workdir)]) == 0
    for name in (TURNS, META, REPORT_MD, REPORT_JSON):
        assert (workdir / name).exists()
    report = json.loads((workdir / REPORT_JSON).read_text())
    assert report["sessions"] == 3 and report["flagged"] >= 5
    assert report["lessons"][0]["sessions"] == 2
    out = capsys.readouterr().out
    assert "Top lessons" in out and "dash" in out


def test_mine_filters(demo_root, workdir):
    main(["mine", "--claude-dir", str(demo_root), "--workdir", str(workdir), "--project", "api"])
    meta = json.loads((workdir / META).read_text())
    assert meta["sessions"] == 1


def test_mine_without_sessions(tmp_path, workdir, capsys):
    assert main(["mine", "--claude-dir", str(tmp_path / "none"), "--workdir", str(workdir)]) == 1
    assert "No Claude Code sessions" in capsys.readouterr().out


def test_commands_need_mine_first(workdir, capsys):
    for cmd in ("label", "score", "report", "judge"):
        assert main([cmd, "--workdir", str(workdir)]) == 1
    assert "Run `twice mine` first" in capsys.readouterr().out


def test_label_then_score(demo_root, workdir, monkeypatch, capsys):
    main(["mine", "--claude-dir", str(demo_root), "--workdir", str(workdir)])
    answers = iter(["y", "", "n", "y", "", "n", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main(["label", "--workdir", str(workdir), "--n", "10", "--seed", "3"]) == 0
    assert len(read_jsonl(workdir / LABELS)) == 4
    capsys.readouterr()
    assert main(["score", "--workdir", str(workdir)]) == 0
    assert "precision" in capsys.readouterr().out


def test_judge_with_fake_model(demo_root, workdir, monkeypatch, capsys):
    main(["mine", "--claude-dir", str(demo_root), "--workdir", str(workdir)])

    class FakeJudge:
        def __init__(self, model, effort):
            self.model = model

        def judge(self, turn):
            dash = "dash" in turn.text.lower()
            return {"id": turn.id, "status": "ok", "is_correction": True,
                    "lesson": "Never use em dashes in user-facing text." if dash else turn.text, "category": "style"}

    monkeypatch.setattr(twice.llm, "Judge", FakeJudge)
    assert main(["judge", "--workdir", str(workdir), "--yes"]) == 0
    assert read_jsonl(workdir / JUDGMENTS)
    report = json.loads((workdir / REPORT_JSON).read_text())
    assert report["grouping"] == "Claude judgments"
    assert report["lessons"][0]["label"] == "Never use em dashes in user-facing text."
    capsys.readouterr()
    assert main(["judge", "--workdir", str(workdir), "--yes"]) == 0
    assert "Nothing left to judge" in capsys.readouterr().out
