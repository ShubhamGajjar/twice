"""Read Claude Code session logs.

Claude Code writes one JSONL file per session under
`~/.claude/projects/<encoded project path>/<session id>.jsonl` (or under
`$CLAUDE_CONFIG_DIR/projects`). Each line is a record. Only `user` and
`assistant` records matter here; the many other record types (API request
blobs, snapshots, titles) are skipped before parsing, which keeps a large
log directory fast to read.

What becomes an event:
- a message the developer typed -> HUMAN
- "[Request interrupted by user...]" -> INTERRUPT
- a tool call the developer rejected -> DENIAL (with any feedback they typed)
- assistant text and tool calls -> ASSISTANT
- other tool results -> TOOL_RESULT

Skipped: harness-injected messages (isMeta, compaction summaries, task
notifications, slash-command echoes, system reminders), subagent sidechains,
and thinking blocks.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from twice.model import (
    ASSISTANT,
    DENIAL,
    HUMAN,
    INTERRUPT,
    TOOL_RESULT,
    Event,
    Session,
    ToolCall,
)

SOURCE = "claude-code"

_RECORD_TYPE = re.compile(r'"type":\s*"(user|assistant)"')
_HARNESS_PREFIXES = (
    "<task-notification>",
    "<local-command-",
    "<command-",
    "<system-reminder>",
    "<bash-",
    "<user-prompt-submit-hook>",
    "<user-memory-input>",
    "Caveat: The messages below were generated",
)
_INTERRUPT_PREFIX = "[Request interrupted by user"
_DENIAL_PREFIX = "The user doesn't want to proceed with this tool use"
_DENIAL_FEEDBACK = re.compile(r"the user said:\s*(.+)", re.DOTALL | re.IGNORECASE)
_IMAGE_MARKER = re.compile(r"\[Image(?: #\d+)?[^\]]*\]")
_NON_HUMAN_ORIGINS = {"task_notification", "peer", "auto_continuation"}


def default_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


def discover(root: Path | None = None) -> list[Path]:
    """Top-level session files. Subagent logs live one level deeper and are skipped."""
    root = Path(root) if root else default_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/*.jsonl") if p.is_file())


def load_session(path: Path) -> Session | None:
    events: list[Event] = []
    tool_names: dict[str, str] = {}  # tool_use id -> tool name, to name rejected tools
    meta: dict[str, str] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not _RECORD_TYPE.search(line):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("isSidechain"):
                continue
            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue
            if "id" not in meta and rec.get("sessionId"):
                meta["id"] = str(rec["sessionId"])
                meta["project"] = str(rec.get("cwd") or "")
                meta["git_branch"] = str(rec.get("gitBranch") or "")
            if rtype == "assistant":
                _read_assistant(rec, events, tool_names)
            else:
                _read_user(rec, events, tool_names)
    if not events:
        return None
    return Session(
        id=meta.get("id", Path(path).stem),
        path=str(path),
        source=SOURCE,
        project=meta.get("project", ""),
        git_branch=meta.get("git_branch", ""),
        events=events,
    )


def _read_assistant(rec: dict, events: list[Event], tool_names: dict[str, str]) -> None:
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            texts.append(str(block["text"]))
        elif block.get("type") == "tool_use":
            name = str(block.get("name") or "tool")
            tool_names[str(block.get("id") or "")] = name
            calls.append(ToolCall(name=name, target=_target(block.get("input"))))
    if not texts and not calls:
        return
    text = "\n".join(texts)
    # Claude Code writes one record per content block; merge consecutive ones.
    if events and events[-1].kind == ASSISTANT:
        prev = events[-1]
        prev.text = f"{prev.text}\n{text}".strip() if text else prev.text
        prev.tool_calls.extend(calls)
        prev.timestamp = rec.get("timestamp") or prev.timestamp
        return
    events.append(
        Event(
            kind=ASSISTANT,
            uuid=str(rec.get("uuid") or ""),
            timestamp=str(rec.get("timestamp") or ""),
            text=text,
            tool_calls=calls,
        )
    )


def _read_user(rec: dict, events: list[Event], tool_names: dict[str, str]) -> None:
    if rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
        return
    if rec.get("turnOrigin") in _NON_HUMAN_ORIGINS or rec.get("promptSource") == "system":
        return
    uuid = str(rec.get("uuid") or "")
    ts = str(rec.get("timestamp") or "")
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        _add_text(content, uuid, ts, events)
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            _add_text(str(block.get("text") or ""), uuid, ts, events)
        elif btype == "tool_result":
            body = _tool_result_text(block.get("content"))
            tool = tool_names.get(str(block.get("tool_use_id") or ""), "")
            if rec.get("toolDenialKind") == "user-rejected" or body.startswith(_DENIAL_PREFIX):
                events.append(
                    Event(
                        kind=DENIAL,
                        uuid=uuid,
                        timestamp=ts,
                        text=_denial_feedback(rec, body),
                        tool_name=tool,
                    )
                )
            else:
                # Denials by permission rules or auto mode are not the developer's
                # judgment, so they count as ordinary failed tool results.
                events.append(
                    Event(
                        kind=TOOL_RESULT,
                        uuid=uuid,
                        timestamp=ts,
                        text=body[:300],
                        tool_name=tool,
                        is_error=bool(block.get("is_error") or rec.get("toolDenialKind")),
                    )
                )


def _add_text(text: str, uuid: str, ts: str, events: list[Event]) -> None:
    stripped = text.strip()
    if not stripped:
        return
    if stripped.startswith(_INTERRUPT_PREFIX):
        events.append(Event(kind=INTERRUPT, uuid=uuid, timestamp=ts))
        return
    if stripped.startswith(_HARNESS_PREFIXES):
        return
    cleaned = _IMAGE_MARKER.sub("", stripped).strip()
    if cleaned:
        events.append(Event(kind=HUMAN, uuid=uuid, timestamp=ts, text=cleaned))


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""


def _denial_feedback(rec: dict, body: str) -> str:
    feedback = rec.get("userFeedback")
    if isinstance(feedback, str) and feedback.strip():
        return feedback.strip()
    match = _DENIAL_FEEDBACK.search(body)
    return match.group(1).strip() if match else ""


def _target(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "notebook_path", "path", "url", "pattern"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    command = tool_input.get("command")
    if isinstance(command, str) and command:
        return command.strip().splitlines()[0][:120] if command.strip() else ""
    description = tool_input.get("description")
    if isinstance(description, str):
        return description[:120]
    return ""
