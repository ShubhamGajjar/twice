from twice.model import ASSISTANT, DENIAL, HUMAN, INTERRUPT, TOOL_RESULT
from twice.sources import claude_code


def test_discover_skips_subagent_logs(demo_root):
    paths = claude_code.discover(demo_root)
    assert sorted(p.stem for p in paths) == ["sess-a", "sess-b", "sess-c"]


def test_discover_missing_root(tmp_path):
    assert claude_code.discover(tmp_path / "nope") == []


def test_default_root_honours_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_code.default_root() == tmp_path / "projects"


def _session(demo_root, name):
    path = next(p for p in claude_code.discover(demo_root) if p.stem == name)
    return claude_code.load_session(path)


def test_session_metadata(demo_root):
    s = _session(demo_root, "sess-a")
    assert s.id == "sess-a"
    assert s.project == "/work/webapp"
    assert s.git_branch == "main"
    assert s.source == "claude-code"
    assert s.started < s.ended


def test_event_sequence_skips_harness_records(demo_root):
    kinds = [e.kind for e in _session(demo_root, "sess-a").events]
    assert kinds == [
        HUMAN, ASSISTANT, TOOL_RESULT, ASSISTANT, HUMAN, ASSISTANT, DENIAL,
        ASSISTANT, INTERRUPT, HUMAN, ASSISTANT, HUMAN, ASSISTANT, HUMAN,
    ]
    texts = " ".join(e.text for e in _session(demo_root, "sess-a").events)
    assert "system-reminder" not in texts
    assert "background job" not in texts
    assert "Summary of earlier" not in texts
    assert "private" not in texts  # thinking blocks are never read


def test_denial_feedback_from_body_and_field(demo_root):
    a = next(e for e in _session(demo_root, "sess-a").events if e.kind == DENIAL)
    assert a.text == "Don't touch the tests folder, only the component."
    assert a.tool_name == "Edit"
    b = next(e for e in _session(demo_root, "sess-b").events if e.kind == DENIAL)
    assert b.text == "Don't edit the changelog by hand"


def test_tool_targets_recorded(demo_root):
    calls = [c for e in _session(demo_root, "sess-a").events for c in e.tool_calls]
    assert [(c.name, c.target) for c in calls] == [
        ("Write", "/work/webapp/src/login.tsx"),
        ("Edit", "/work/webapp/tests/login.test.tsx"),
        ("Bash", "git push origin main"),
    ]


def test_empty_file_gives_no_session(tmp_path):
    path = tmp_path / "p" / "empty.jsonl"
    path.parent.mkdir()
    path.write_text("")
    assert claude_code.load_session(path) is None
