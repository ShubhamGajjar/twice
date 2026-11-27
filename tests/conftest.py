"""Synthetic Claude Code session logs that mirror the real record format.

No real session data lives in this repository. These builders produce the
fields twice reads (type, uuid, timestamp, sessionId, cwd, message content
blocks, isMeta, turnOrigin, toolDenialKind, userFeedback) plus some of the
record types it must skip.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

_counter = itertools.count(1)


def _uuid() -> str:
    return f"u{next(_counter):04d}"


class SessionWriter:
    def __init__(self, session_id: str, cwd: str, start_minute: int = 0):
        self.session_id = session_id
        self.cwd = cwd
        self.minute = start_minute
        self.lines: list[str] = []

    def _base(self, rtype: str, **extra) -> dict:
        self.minute += 1
        rec = {
            "type": rtype,
            "uuid": _uuid(),
            "timestamp": f"2026-11-{10 + self.minute // 1440:02d}T{(self.minute // 60) % 24:02d}:{self.minute % 60:02d}:00.000Z",
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "gitBranch": "main",
            "isSidechain": False,
            "userType": "external",
            "entrypoint": "cli",
        }
        rec.update(extra)
        return rec

    def human(self, text: str, **extra) -> SessionWriter:
        rec = self._base("user", promptSource="typed", turnOrigin="human", message={"role": "user", "content": text}, **extra)
        self.lines.append(json.dumps(rec))
        return self

    def raw_user(self, content, **extra) -> SessionWriter:
        self.lines.append(json.dumps(self._base("user", message={"role": "user", "content": content}, **extra)))
        return self

    def assistant(self, text: str = "", tools: list[tuple[str, str, dict]] = ()) -> SessionWriter:
        if text:
            msg = {"role": "assistant", "content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": text}]}
            self.lines.append(json.dumps(self._base("assistant", message=msg)))
        for tool_id, name, tool_input in tools:
            msg = {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}
            self.lines.append(json.dumps(self._base("assistant", message=msg)))
        return self

    def tool_ok(self, tool_id: str, text: str = "ok") -> SessionWriter:
        return self.raw_user([{"type": "tool_result", "tool_use_id": tool_id, "content": text}])

    def tool_rejected(self, tool_id: str, feedback: str = "", via_field: bool = False) -> SessionWriter:
        body = (
            "The user doesn't want to proceed with this tool use. The tool use was rejected "
            "(eg. if it was a file edit, the new_string was NOT written to the file). "
        )
        extra = {"toolDenialKind": "user-rejected"}
        if feedback and via_field:
            extra["userFeedback"] = feedback
        elif feedback:
            body += f"To tell you how to proceed, the user said:\n{feedback}"
        return self.raw_user([{"type": "tool_result", "tool_use_id": tool_id, "content": body, "is_error": True}], **extra)

    def interrupt(self) -> SessionWriter:
        return self.raw_user([{"type": "text", "text": "[Request interrupted by user for tool use]"}])

    def noise(self) -> SessionWriter:
        self.lines.append(json.dumps({"type": "api-request-blob", "data": {"type": "user", "x": 1}}))
        self.lines.append(json.dumps({"type": "ai-title", "title": "demo"}))
        self.lines.append('{"type":"user", this is not json')
        return self

    def write(self, root: Path) -> Path:
        folder = root / ("-" + self.cwd.strip("/").replace("/", "-"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.session_id}.jsonl"
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


def build_demo_root(root: Path) -> Path:
    """Two projects, three sessions; the em dash correction repeats across two sessions."""
    s1 = SessionWriter("sess-a", "/work/webapp")
    s1.human("Add a login page with a short welcome message")
    s1.assistant(tools=[("t1", "Write", {"file_path": "/work/webapp/src/login.tsx", "content": "x"})])
    s1.tool_ok("t1")
    s1.assistant("I added the login page. The welcome copy uses em dashes between clauses.")
    s1.human("No, I said never use em dashes in UI copy. Use commas.")
    s1.assistant(tools=[("t2", "Edit", {"file_path": "/work/webapp/tests/login.test.tsx"})])
    s1.tool_rejected("t2", feedback="Don't touch the tests folder, only the component.")
    s1.assistant(tools=[("t3", "Bash", {"command": "git push origin main"})])
    s1.interrupt()
    s1.human("Run the test suite before pushing anything.")
    s1.raw_user("<system-reminder>internal</system-reminder>", isMeta=True)
    s1.raw_user("<task-notification>background job done</task-notification>", turnOrigin="task_notification")
    s1.assistant("Tests pass now and I pushed the branch.")
    s1.human("great, thanks")
    s1.raw_user("Summary of earlier conversation", isCompactSummary=True)
    s1.noise()
    s1.assistant("Anything else?")
    s1.human("Also add a logout button. My key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz for the demo.")

    s2 = SessionWriter("sess-b", "/work/webapp", start_minute=200)
    s2.human("Write the release notes")
    s2.assistant("Here are the notes, with em dashes separating each item.")
    s2.human("Again with the em dashes. Never use em dashes, use commas instead.")
    s2.assistant(tools=[("t4", "Edit", {"file_path": "/work/webapp/CHANGELOG.md"})])
    s2.tool_rejected("t4", feedback="Don't edit the changelog by hand", via_field=True)
    s2.assistant("Understood.")
    s2.human("Can you also bump the version to 1.2.0?")

    s3 = SessionWriter("sess-c", "/work/api", start_minute=400)
    s3.human("Fix the flaky test in the payments module")
    s3.assistant("I removed the flaky test since it was failing intermittently.")
    s3.human("Why did you delete the test? Don't delete tests, fix them.")
    s3.assistant("Restored and fixed the race condition.")
    s3.human("perfect")

    for writer in (s1, s2, s3):
        writer.write(root)
    sub = root / "-work-webapp" / "sess-a" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(SessionWriter("sub", "/work/webapp").human("subagent text").lines[0] + "\n")
    return root


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    return build_demo_root(tmp_path / "projects")


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path / "work"
