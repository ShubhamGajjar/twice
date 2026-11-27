"""Turn sessions into scored turns.

A turn is a human message that follows agent activity: the agent said or did
something, and then the developer spoke. The first message of a session, and
messages typed before the agent has acted, are never corrections and are left
out, so every statistic downstream is over the same population.

A rejected tool call where the developer typed feedback is also a turn: the
feedback is the correction.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from twice.model import ASSISTANT, DENIAL, HUMAN, INTERRUPT, Event, Session, Turn
from twice.redact import redact
from twice.signals import DEFAULT_THRESHOLD, score_text

_HOME = str(Path.home())


def turns_from_session(session: Session, threshold: float = DEFAULT_THRESHOLD) -> list[Turn]:
    turns: list[Turn] = []
    window: list[Event] = []  # events since the developer last spoke
    for event in session.events:
        if event.kind == HUMAN:
            if any(e.kind == ASSISTANT for e in window):
                kinds = {e.kind for e in window} & {INTERRUPT, DENIAL}
                turns.append(_turn(session, event, event.text, window, kinds, threshold))
            window = []
        elif event.kind == DENIAL and event.text:
            turns.append(_turn(session, event, event.text, window + [event], {DENIAL}, threshold))
            window = []
        else:
            window.append(event)
    return turns


def mine(sessions: Iterable[Session], threshold: float = DEFAULT_THRESHOLD) -> list[Turn]:
    turns: list[Turn] = []
    for session in sessions:
        turns.extend(turns_from_session(session, threshold))
    return turns


def describe_activity(events: list[Event], max_calls: int = 5) -> str:
    """A short, redacted note on what the agent did right before the developer spoke."""
    lines: list[str] = []
    calls = [call for e in events if e.kind == ASSISTANT for call in e.tool_calls]
    if calls:
        shown = calls[-max_calls:]
        more = len(calls) - len(shown)
        parts = [f"{c.name} {_shorten_path(c.target)}".strip() for c in shown]
        prefix = f"(+{more} earlier) " if more else ""
        lines.append("Tool calls: " + prefix + "; ".join(parts))
    said = next((e.text for e in reversed(events) if e.kind == ASSISTANT and e.text), "")
    if said:
        lines.append("Agent said: " + _clip(said, 400, keep_end=True))
    for e in events:
        if e.kind == INTERRUPT:
            lines.append("The developer interrupted the agent.")
        elif e.kind == DENIAL:
            lines.append(f"The developer rejected a {e.tool_name or 'tool'} call.")
    return redact("\n".join(lines))


def _turn(session: Session, event: Event, text: str, window: list[Event], kinds: set[str], threshold: float) -> Turn:
    score, signals = score_text(text, kinds)
    return Turn(
        id=f"{session.id}:{event.uuid}",
        session_id=session.id,
        source=session.source,
        project=session.project,
        git_branch=session.git_branch,
        timestamp=event.timestamp,
        text=redact(text),
        context=describe_activity(window),
        signals=signals,
        score=score,
        is_candidate=score >= threshold,
    )


def _shorten_path(value: str) -> str:
    return value.replace(_HOME, "~", 1) if value else value


def _clip(text: str, limit: int, keep_end: bool = False) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return "..." + text[-limit:] if keep_end else text[:limit] + "..."
