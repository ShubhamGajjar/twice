"""Plain data types shared by every part of twice.

A source adapter (Claude Code today, others later) turns a log file into a
`Session`: an ordered list of `Event`s. Mining turns each human message that
follows agent activity into a scored `Turn`. Everything downstream (labeling,
judging, scoring, reports) works on `Turn`s, so adding a new agent only means
writing a new adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Event kinds
HUMAN = "human"
ASSISTANT = "assistant"
TOOL_RESULT = "tool_result"
INTERRUPT = "interrupt"  # the developer stopped the agent mid-action
DENIAL = "denial"  # the developer rejected a tool call; text holds any feedback they typed


@dataclass
class ToolCall:
    name: str
    target: str = ""  # file path, command or other short note on what the call touched


@dataclass
class Event:
    kind: str
    uuid: str
    timestamp: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str = ""
    is_error: bool = False


@dataclass
class Session:
    id: str
    path: str
    source: str
    project: str
    git_branch: str
    events: list[Event]

    @property
    def started(self) -> str:
        return next((e.timestamp for e in self.events if e.timestamp), "")

    @property
    def ended(self) -> str:
        return next((e.timestamp for e in reversed(self.events) if e.timestamp), "")


@dataclass
class Turn:
    """One human message that followed agent activity, scored as a possible correction."""

    id: str  # "<session id>:<event uuid>", stable across runs
    session_id: str
    source: str
    project: str
    git_branch: str
    timestamp: str
    text: str  # redacted
    context: str  # what the agent did just before, redacted
    signals: list[str]
    score: float
    is_candidate: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Turn:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})
