"""Run Claude Code headless for a replay, isolated from your personal setup.

What each run gets, and why:
- `--setting-sources project`: the repository's committed settings load, but your
  user settings (hooks, plugins, permissions) do not.
- `--strict-mcp-config` with no config: no MCP servers, so no tool can reach your accounts.
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`: no memory read or written.
- `--no-session-persistence`: replays never land in ~/.claude/projects, so they
  never show up in `twice mine`.
- `--permission-mode acceptEdits` with an allowlist (file tools by default) and
  web tools blocked. Calls outside the allowlist are denied, but still recorded.

What cannot be excluded while keeping your normal login: your global
~/.claude/CLAUDE.md loads in every run (only `--bare` skips it, and that mode
accepts an API key only). So the baseline is "your setup without this rule".

A project CLAUDE.md only loads when the folder is trusted in Claude Code, which
is why replay copies live under one folder you trust once (see `twice doctor`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BLOCKED_TOOLS = ("WebFetch", "WebSearch")


@dataclass
class AgentRun:
    exit_code: int
    timed_out: bool = False
    is_error: bool = False
    error: str = ""
    model: str = ""
    final_text: str = ""
    assistant_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    denials: list[dict] = field(default_factory=list)
    mcp_servers: list = field(default_factory=list)
    cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None
    transcript: list[str] = field(default_factory=list)
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.is_error


class ClaudeCodeAgent:
    name = "claude-code"

    def __init__(self, model: str | None = None, max_budget_usd: float | None = None, timeout: int = 900, executable: str = "claude"):
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.timeout = timeout
        self.executable = executable

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def command(self, system_rule: str | None, tools: list[str]) -> list[str]:
        cmd = [
            self.executable, "-p",
            "--output-format", "stream-json", "--verbose",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources", "project",
            "--permission-mode", "acceptEdits",
            "--allowedTools", ",".join(tools),
            "--disallowedTools", ",".join(BLOCKED_TOOLS),
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.max_budget_usd:
            cmd += ["--max-budget-usd", f"{self.max_budget_usd:g}"]
        if system_rule:
            cmd += ["--append-system-prompt", system_rule]
        return cmd

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        return env

    def run(self, cwd: Path, prompt: str, system_rule: str | None, tools: list[str]) -> AgentRun:
        cmd = self.command(system_rule, tools)
        try:
            proc = subprocess.run(
                cmd, check=False, input=prompt, cwd=cwd, env=self.environment(),
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            run = parse_stream(out.splitlines(), exit_code=124)
            run.timed_out = True
            run.error = f"timed out after {self.timeout}s"
            return run
        except OSError as exc:
            return AgentRun(exit_code=127, is_error=True, error=str(exc))
        run = parse_stream(proc.stdout.splitlines(), exit_code=proc.returncode)
        run.stderr = proc.stderr[-2000:]
        if proc.returncode != 0 and not run.error:
            run.error = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1][:300]
        return run


def parse_stream(lines: list[str], exit_code: int = 0) -> AgentRun:
    """Read Claude Code's stream-json output (one JSON event per line)."""
    run = AgentRun(exit_code=exit_code, transcript=list(lines))
    texts: list[str] = []
    saw_result = False
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            run.model = str(event.get("model") or "")
            run.mcp_servers = event.get("mcp_servers") or []
        elif etype == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    run.tool_calls.append({"name": block.get("name", ""), "input": block.get("input") or {}})
        elif etype == "result":
            saw_result = True
            run.final_text = str(event.get("result") or "")
            run.is_error = bool(event.get("is_error")) or str(event.get("subtype", "success")) != "success"
            if run.is_error:
                run.error = str(event.get("subtype") or "error")
            cost = event.get("total_cost_usd", event.get("cost_usd"))
            run.cost_usd = float(cost) if isinstance(cost, (int, float)) else None
            run.turns = event.get("num_turns")
            run.duration_ms = event.get("duration_ms")
            run.denials = event.get("permission_denials") or []
    run.assistant_text = "\n".join(texts)
    if not saw_result and exit_code == 0:
        run.is_error = True
        run.error = run.error or "no result event"
    return run


def trusted(path: Path, config: Path | None = None) -> bool | None:
    """Whether Claude Code trusts `path` (or a folder above it). None if that can't be read."""
    config = config or Path(os.environ.get("CLAUDE_CONFIG_DIR", "") or Path.home()) / ".claude.json"
    try:
        projects = json.loads(config.read_text(encoding="utf-8")).get("projects", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    ok = {p for p, v in projects.items() if isinstance(v, dict) and v.get("hasTrustDialogAccepted")}
    for candidate in {Path(path).absolute(), Path(path).resolve()}:
        for folder in (candidate, *candidate.parents):
            if str(folder) in ok:
                return True
    return False
