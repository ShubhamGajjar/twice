from twice.checks import EXAMPLES, Evidence, evaluate, validate

DASH = "\u2014"


def _ev(tmp_path, **kw):
    return Evidence(cwd=tmp_path, **kw)


def test_examples_are_valid():
    for example in EXAMPLES:
        assert validate(example) == [], example


def test_validation_catches_mistakes():
    assert "unknown check type" in validate({"type": "nope"})[0]
    assert validate({"type": "diff_regex", "pattern": "("})[0].startswith("invalid regex")
    assert validate({"type": "changed_files", "globs": []})
    assert validate({"type": "tool_call"})
    assert validate({"type": "command", "run": "x", "mistake_if_exit": "maybe"})
    assert "still contains TODO" in validate({"type": "diff_regex", "pattern": "TODO"})


def test_diff_regex(tmp_path):
    check = {"type": "diff_regex", "pattern": DASH}
    assert evaluate(check, _ev(tmp_path, added_lines=["Welcome, friend"]))[0] is False
    fired, detail = evaluate(check, _ev(tmp_path, added_lines=["ok", f"Welcome {DASH} friend"]))
    assert fired and "Welcome" in detail


def test_output_regex(tmp_path):
    check = {"type": "output_regex", "pattern": "(?i)all tests pass"}
    assert evaluate(check, _ev(tmp_path, output="Done. All tests pass!"))[0]
    assert not evaluate(check, _ev(tmp_path, output="Done."))[0]


def test_changed_files(tmp_path):
    check = {"type": "changed_files", "globs": ["tests/*", "CHANGELOG.md"]}
    assert evaluate(check, _ev(tmp_path, changed_files=["src/a.py", "tests/unit/test_a.py"]))[0]
    assert not evaluate(check, _ev(tmp_path, changed_files=["src/a.py"]))[0]


def test_tool_call_counts_attempts(tmp_path):
    calls = [{"name": "Bash", "input": {"command": "git push origin main"}}, {"name": "Edit", "input": {}}]
    assert evaluate({"type": "tool_call", "tool": "Bash", "pattern": "git push"}, _ev(tmp_path, tool_calls=calls))[0]
    assert not evaluate({"type": "tool_call", "tool": "Bash", "pattern": "rm -rf"}, _ev(tmp_path, tool_calls=calls))[0]
    assert evaluate({"type": "tool_call", "tool": "Edit"}, _ev(tmp_path, tool_calls=calls))[0]


def test_command(tmp_path):
    (tmp_path / "marker").write_text("x")
    assert evaluate({"type": "command", "run": "test -f marker"}, _ev(tmp_path))[0] is False
    assert evaluate({"type": "command", "run": "test -f missing"}, _ev(tmp_path))[0] is True
    assert evaluate({"type": "command", "run": "test -f marker", "mistake_if_exit": "zero"}, _ev(tmp_path))[0] is True
