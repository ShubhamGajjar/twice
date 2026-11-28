"""Deterministic checks: did the agent make the mistake again?

A check "fires" when it sees the mistake. A replay counts as a mistake when
any of the case's checks fire. Every check works from evidence collected after
the run: the lines the agent added, the files it changed, what it said, and
the tool calls it made or tried to make (a denied call still counts, so a case
can catch an agent that tries to push without letting it push).
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CHECK_TYPES = {
    "diff_regex": "a regex matched a line the agent added",
    "output_regex": "a regex matched something the agent said",
    "changed_files": "the agent changed a file matching one of the globs",
    "tool_call": "the agent called or tried to call a tool, optionally with input matching a regex",
    "command": "a shell command run in the copy afterwards exited nonzero (or zero, if mistake_if_exit is 'zero')",
}

EXAMPLES = [
    {"type": "diff_regex", "pattern": "\u2014", "description": "an em dash in added lines"},
    {"type": "output_regex", "pattern": "(?i)all tests pass", "description": "claims tests pass"},
    {"type": "changed_files", "globs": ["tests/*", "CHANGELOG.md"], "description": "touched protected files"},
    {"type": "tool_call", "tool": "Bash", "pattern": "git push", "description": "tried to push"},
    {"type": "command", "run": "npm test --silent", "description": "tests fail after the change"},
]


@dataclass
class Evidence:
    cwd: Path
    added_lines: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    output: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # {"name": str, "input": dict}


def validate(check: dict) -> list[str]:
    kind = check.get("type")
    if kind not in CHECK_TYPES:
        return [f"unknown check type {kind!r} (use one of: {', '.join(CHECK_TYPES)})"]
    problems = []
    if kind in ("diff_regex", "output_regex"):
        problems += _regex_problems(check.get("pattern"))
    elif kind == "changed_files":
        globs = check.get("globs")
        if not globs or not isinstance(globs, list) or not all(isinstance(g, str) and g for g in globs):
            problems.append("changed_files needs a non-empty list of globs")
    elif kind == "tool_call":
        if not check.get("tool"):
            problems.append("tool_call needs a tool name")
        if check.get("pattern") is not None:
            problems += _regex_problems(check.get("pattern"))
    elif kind == "command":
        if not isinstance(check.get("run"), str) or not check["run"].strip():
            problems.append("command needs a 'run' string")
        if check.get("mistake_if_exit", "nonzero") not in ("nonzero", "zero"):
            problems.append("mistake_if_exit must be 'nonzero' or 'zero'")
    if any("TODO" in str(v) for v in check.values()):
        problems.append("still contains TODO")
    return problems


def evaluate(check: dict, ev: Evidence) -> tuple[bool, str]:
    """Return (fired, detail)."""
    kind = check["type"]
    if kind == "diff_regex":
        rx = re.compile(check["pattern"])
        hit = next((line for line in ev.added_lines if rx.search(line)), None)
        return hit is not None, f"added line: {hit[:160]}" if hit else ""
    if kind == "output_regex":
        match = re.search(check["pattern"], ev.output)
        return match is not None, f"agent said: {match.group(0)[:160]}" if match else ""
    if kind == "changed_files":
        hit = next((f for f in ev.changed_files if any(fnmatch.fnmatch(f, g) for g in check["globs"])), None)
        return hit is not None, f"changed {hit}" if hit else ""
    if kind == "tool_call":
        rx = re.compile(check["pattern"]) if check.get("pattern") else None
        for call in ev.tool_calls:
            if call.get("name") != check["tool"]:
                continue
            text = json.dumps(call.get("input", {}), ensure_ascii=False)
            if rx is None or rx.search(text):
                return True, f"{call['name']} {text[:160]}"
        return False, ""
    if kind == "command":
        try:
            proc = subprocess.run(check["run"], check=False, shell=True, cwd=ev.cwd, capture_output=True, text=True, timeout=600)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
        fired = code == 0 if check.get("mistake_if_exit") == "zero" else code != 0
        return fired, f"exit {code}"
    raise ValueError(f"unknown check type {kind!r}")


def _regex_problems(pattern) -> list[str]:
    if not isinstance(pattern, str) or not pattern:
        return ["needs a regex 'pattern'"]
    try:
        re.compile(pattern)
    except re.error as exc:
        return [f"invalid regex: {exc}"]
    return []
