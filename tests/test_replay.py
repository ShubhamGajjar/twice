import subprocess
from pathlib import Path

import pytest
from conftest import make_repo

from twice.agent import AgentRun
from twice.cases import Case
from twice.replay import fisher_one_sided, format_summary, run_trial, summarize, verdict

DASH = "\u2014"


class FakeAgent:
    """Writes UI copy with an em dash unless a rule about em dashes reaches it."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def run(self, cwd, prompt, system_rule, tools):
        cwd = Path(cwd)
        claude_md = (cwd / "CLAUDE.md").read_text() if (cwd / "CLAUDE.md").exists() else ""
        sees_rule = "em dash" in (claude_md + (system_rule or "")).lower()
        (cwd / "welcome.txt").write_text(("Welcome, friend" if sees_rule else f"Welcome {DASH} friend") + "\n")
        self.calls.append({"cwd": cwd, "prompt": prompt, "system_rule": system_rule, "tools": tools, "claude_md": claude_md})
        return AgentRun(exit_code=0, model="fake-model", final_text="Done", assistant_text="I wrote the copy.",
                        tool_calls=[{"name": "Write", "input": {"file_path": "welcome.txt"}}], cost_usd=0.01, turns=2)


@pytest.fixture
def repo_case(tmp_path):
    repo = tmp_path / "app"
    sha = make_repo(repo, {"README.md": "# app\n", "CLAUDE.md": "# App\n\n- Use TypeScript.\n"})
    case = Case(id="no-em-dash", rule="Never use em dashes in UI copy.", prompt="Write a welcome message",
                repo=str(repo), commit=sha, checks=[{"type": "diff_regex", "pattern": DASH}])
    return case, repo


def _status(repo):
    return subprocess.run(["git", "status", "--porcelain"], check=False, cwd=repo, capture_output=True, text=True).stdout


def test_variants_change_what_the_agent_sees(repo_case, tmp_path):
    case, _repo = repo_case
    agent = FakeAgent()
    root = tmp_path / "replays"
    none = run_trial(case, "none", 1, agent, root, "r1")
    md = run_trial(case, "claude_md", 1, agent, root, "r1")
    system = run_trial(case, "system", 1, agent, root, "r1")
    assert none["status"] == "ok" and none["mistake"] is True
    assert none["fired"][0]["type"] == "diff_regex"
    assert md["mistake"] is False and system["mistake"] is False
    assert "Never use em dashes in UI copy." in agent.calls[1]["claude_md"]
    assert "Use TypeScript." in agent.calls[1]["claude_md"]  # existing project rules are kept
    assert agent.calls[2]["system_rule"] == "Never use em dashes in UI copy."
    assert agent.calls[0]["system_rule"] is None and "em dash" not in agent.calls[0]["claude_md"]
    assert none["changed_files"] == ["welcome.txt"]  # the setup commit keeps CLAUDE.md out of the diff
    assert none["cost_usd"] == 0.01 and none["model"] == "fake-model"


def test_copies_are_isolated_and_cleaned_up(repo_case, tmp_path):
    case, repo = repo_case
    agent = FakeAgent()
    root = tmp_path / "replays"
    row = run_trial(case, "claude_md", 1, agent, root, "r1", transcripts=tmp_path / "t")
    copy = agent.calls[0]["cwd"]
    assert copy.parent == root and not copy.exists()
    assert _status(repo) == "" and not (repo / "welcome.txt").exists()
    branches = subprocess.run(["git", "branch", "--list"], check=False, cwd=repo, capture_output=True, text=True).stdout
    assert branches.strip() == "* main"
    assert (tmp_path / "t" / "no-em-dash-claude_md-1.jsonl").exists()
    assert row["status"] == "ok"


def test_keep_leaves_copy_without_remotes(repo_case, tmp_path):
    case, _ = repo_case
    row = run_trial(case, "none", 1, FakeAgent(), tmp_path / "replays", "r1", keep=True)
    copy = Path(row["copy"])
    assert copy.exists()
    assert subprocess.run(["git", "remote"], check=False, cwd=copy, capture_output=True, text=True).stdout.strip() == ""


def test_bad_commit_is_an_error_not_a_crash(repo_case, tmp_path):
    case, _ = repo_case
    case.commit = "0" * 40
    row = run_trial(case, "none", 1, FakeAgent(), tmp_path / "replays", "r1")
    assert row["status"] == "error" and row["mistake"] is None and row["error"].startswith("git:")


def test_fisher_exact():
    assert fisher_one_sided(5, 5, 0, 5) == pytest.approx(1 / 252)
    assert fisher_one_sided(3, 3, 0, 3) == pytest.approx(1 / 20)
    assert fisher_one_sided(2, 5, 2, 5) > 0.5


def test_summary_and_verdicts():
    rows = [{"case_id": "c", "variant": "none", "status": "ok", "mistake": True, "cost_usd": 0.1} for _ in range(5)]
    rows += [{"case_id": "c", "variant": "claude_md", "status": "ok", "mistake": False, "cost_usd": 0.1} for _ in range(5)]
    rows += [{"case_id": "c", "variant": "claude_md", "status": "error", "mistake": None}]
    summary = summarize(rows)["c"]
    assert summary["none"]["mistakes"] == 5 and summary["claude_md"]["runs"] == 5 and summary["claude_md"]["errors"] == 1
    label, p = verdict(summary["none"], summary["claude_md"])
    assert label == "the rule helps" and p == pytest.approx(1 / 252)
    text = format_summary({"c": summary})
    assert "the rule helps" in text and "cost so far: $1.00" in text


def test_verdict_edge_cases():
    clean = {"runs": 5, "mistakes": 0, "rate": 0.0}
    arm = {"runs": 5, "mistakes": 0, "rate": 0.0}
    assert verdict(clean, arm)[0].startswith("the mistake didn't come back")
    assert verdict(None, arm)[0].startswith("no baseline")
    assert verdict({"runs": 2, "mistakes": 2, "rate": 1.0}, arm)[0].startswith("too few runs")
    same = {"runs": 5, "mistakes": 3, "rate": 0.6}
    assert verdict(same, dict(same))[0] == "no sign the rule helps"
