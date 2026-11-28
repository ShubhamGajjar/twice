import json

from twice.agent import ClaudeCodeAgent, parse_stream, trusted


def _lines(*events):
    return [json.dumps(e) for e in events]


INIT = {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "mcp_servers": [], "tools": ["Read"]}
ASSISTANT = {"type": "assistant", "message": {"content": [
    {"type": "thinking", "thinking": "hidden"},
    {"type": "text", "text": "Writing the file."},
    {"type": "tool_use", "name": "Bash", "input": {"command": "git push"}},
]}}
TOOL = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "denied", "is_error": True}]}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "Done.", "total_cost_usd": 0.0123,
          "num_turns": 3, "duration_ms": 4200, "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "git push"}}]}


def test_command_isolates_the_run():
    cmd = ClaudeCodeAgent(model="sonnet", max_budget_usd=0.5).command("Never use em dashes.", ["Read", "Edit"])
    joined = " ".join(cmd)
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--verbose"):
        assert flag in cmd
    assert "--setting-sources project" in joined
    assert "--output-format stream-json" in joined
    assert "--permission-mode acceptEdits" in joined
    assert "--allowedTools Read,Edit" in joined
    assert "--disallowedTools WebFetch,WebSearch" in joined
    assert "--model sonnet" in joined and "--max-budget-usd 0.5" in joined
    assert cmd[-2:] == ["--append-system-prompt", "Never use em dashes."]
    assert "--append-system-prompt" not in ClaudeCodeAgent().command(None, ["Read"])


def test_environment_disables_memory():
    assert ClaudeCodeAgent().environment()["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_parse_stream():
    run = parse_stream(_lines(INIT, ASSISTANT, TOOL, RESULT) + ["not json"])
    assert run.ok
    assert run.model == "claude-sonnet-5-5"
    assert run.assistant_text == "Writing the file."
    assert run.tool_calls == [{"name": "Bash", "input": {"command": "git push"}}]
    assert run.final_text == "Done." and run.cost_usd == 0.0123 and run.turns == 3
    assert run.denials[0]["tool_name"] == "Bash"
    assert "hidden" not in run.assistant_text


def test_error_results():
    over_budget = dict(RESULT, subtype="error_max_budget_usd", is_error=True)
    run = parse_stream(_lines(INIT, over_budget))
    assert not run.ok and run.error == "error_max_budget_usd"
    missing = parse_stream(_lines(INIT, ASSISTANT))
    assert not missing.ok and missing.error == "no result event"
    crashed = parse_stream([], exit_code=1)
    assert not crashed.ok


def test_trust_is_inherited(tmp_path):
    config = tmp_path / "claude.json"
    trusted_dir = tmp_path / "trusted"
    (trusted_dir / "replays").mkdir(parents=True)
    config.write_text(json.dumps({"projects": {str(trusted_dir): {"hasTrustDialogAccepted": True},
                                               str(tmp_path / "other"): {"hasTrustDialogAccepted": False}}}))
    assert trusted(trusted_dir / "replays", config) is True
    assert trusted(tmp_path / "other", config) is False
    assert trusted(tmp_path, config) is False
    assert trusted(tmp_path, tmp_path / "missing.json") is None
