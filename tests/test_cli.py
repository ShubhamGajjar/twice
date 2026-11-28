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


# --- replay commands -------------------------------------------------------

def _case_setup(tmp_path, workdir):
    from conftest import SessionWriter, make_repo

    repo = tmp_path / "site"
    make_repo(repo, {"index.html": "<p>hi</p>\n"}, date="2026-11-01T00:00:00Z")
    root = tmp_path / "logs"
    s = SessionWriter("sess-r", str(repo))
    s.human("Write the welcome message in welcome.txt")
    s.assistant("Done, with an em dash for flair.")
    s.human("No, never use em dashes in UI copy.")
    s.write(root)
    main(["mine", "--claude-dir", str(root), "--workdir", str(workdir)])
    return repo


def _add_check(workdir, case_id):
    path = workdir / "cases" / f"{case_id}.json"
    data = json.loads(path.read_text())
    data["checks"] = [{"type": "diff_regex", "pattern": "\u2014", "description": "em dash added"}]
    path.write_text(json.dumps(data))


def test_case_new_list_show(tmp_path, workdir, capsys):
    _case_setup(tmp_path, workdir)
    assert main(["case", "new", "--lesson", "1", "--rule", "Never use em dashes in UI copy.", "--workdir", str(workdir)]) == 0
    out = capsys.readouterr().out
    assert "Write the welcome message" in out and "no checks yet" in out
    case_id = "never-use-em-dashes-in-ui"
    assert main(["case", "list", "--workdir", str(workdir)]) == 0
    assert "needs 1 fix" in capsys.readouterr().out
    _add_check(workdir, case_id)
    assert main(["case", "show", case_id, "--workdir", str(workdir)]) == 0
    assert "Ready to replay" in capsys.readouterr().out
    assert main(["case", "show", "missing", "--workdir", str(workdir)]) == 1


def test_case_new_bad_lesson(tmp_path, workdir, capsys):
    _case_setup(tmp_path, workdir)
    assert main(["case", "new", "--lesson", "9", "--workdir", str(workdir)]) == 1
    assert "No lesson 9" in capsys.readouterr().out


def test_replay_end_to_end_with_fake_agent(tmp_path, workdir, monkeypatch, capsys):
    from test_replay import FakeAgent

    import twice.agent

    repo = _case_setup(tmp_path, workdir)
    main(["case", "new", "--lesson", "1", "--rule", "Never use em dashes in UI copy.", "--workdir", str(workdir)])
    _add_check(workdir, "never-use-em-dashes-in-ui")

    class Agent(FakeAgent):
        def __init__(self, **kwargs):
            super().__init__()

        def available(self):
            return True

        def command(self, rule, tools):
            return ["claude", "-p"] + (["--append-system-prompt", rule] if rule else [])

    monkeypatch.setattr(twice.agent, "ClaudeCodeAgent", Agent)
    monkeypatch.setattr(twice.agent, "trusted", lambda path: True)
    capsys.readouterr()
    assert main(["replay", "--workdir", str(workdir), "--trials", "3", "--yes", "--variants", "none,claude_md,system"]) == 0
    out = capsys.readouterr().out
    assert out.count("MISTAKE") == 3 and out.count("clean") == 6
    assert "the rule helps" not in out or "p=" in out
    assert len(read_jsonl(workdir / "replays.jsonl")) == 9
    assert main(["results", "--workdir", str(workdir)]) == 0
    results = capsys.readouterr().out
    assert "none" in results and "claude_md" in results and "system" in results
    # 3/3 vs 0/3 gives p = 0.05, which is not below 0.05
    assert "fewer mistakes with the rule, but not enough runs to be sure" in results
    assert not (repo / "welcome.txt").exists()

    assert main(["replay", "--workdir", str(workdir), "--dry-run"]) == 0
    assert "Command: claude -p" in capsys.readouterr().out


def test_replay_refuses_untrusted_root_for_claude_md(tmp_path, workdir, monkeypatch, capsys):
    import twice.agent

    _case_setup(tmp_path, workdir)
    main(["case", "new", "--lesson", "1", "--rule", "Never use em dashes.", "--workdir", str(workdir)])
    _add_check(workdir, "never-use-em-dashes")
    monkeypatch.setattr(twice.agent, "trusted", lambda path: False)
    capsys.readouterr()
    assert main(["replay", "--workdir", str(workdir), "--yes"]) == 1
    assert "Trust it once" in capsys.readouterr().out


def test_replay_needs_ready_cases(workdir, capsys):
    assert main(["replay", "--workdir", str(workdir), "--yes"]) == 1
    assert "No ready cases" in capsys.readouterr().out
    assert main(["replay", "--workdir", str(workdir), "--variants", "bogus"]) == 1


def test_doctor_reports_trust(workdir, monkeypatch, capsys):
    import twice.agent

    monkeypatch.setattr(twice.agent, "trusted", lambda path: False)
    main(["doctor", "--workdir", str(workdir)])
    out = capsys.readouterr().out
    assert "replay folder is not trusted" in out
    monkeypatch.setattr(twice.agent, "trusted", lambda path: True)
    main(["doctor", "--workdir", str(workdir)])
    assert "replay folder is trusted" in capsys.readouterr().out
