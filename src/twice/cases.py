"""Replay cases: one correction turned into a repeatable test.

A case holds the rule to test, the prompt that led to the mistake, the
repository and the commit to start from, and the checks that decide whether
the mistake happened again. `twice case new` fills in everything it can from
the session log; you write the checks, because only you know what the mistake
looked like. Case files are plain JSON in ~/.twice/cases, made to be edited.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from twice import gitutil
from twice.checks import CHECK_TYPES, EXAMPLES, validate
from twice.model import HUMAN, Turn
from twice.redact import redact
from twice.sources import claude_code

CASES_DIR = "cases"
DEFAULT_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write"]


@dataclass
class Case:
    id: str
    rule: str
    prompt: str
    repo: str
    commit: str
    subdir: str = ""
    checks: list[dict] = field(default_factory=list)
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    turn_id: str = ""
    correction: str = ""
    created_at: str = ""
    notes: str = ""

    def problems(self) -> list[str]:
        out = []
        for name in ("rule", "prompt", "repo", "commit"):
            value = getattr(self, name)
            if not value or "TODO" in value:
                out.append(f"{name} is missing")
        if self.repo and not Path(self.repo).is_dir():
            out.append(f"repo {self.repo} does not exist")
        if not self.checks:
            out.append("no checks yet: add at least one to 'checks'")
        for i, check in enumerate(self.checks, 1):
            out += [f"check {i}: {p}" for p in validate(check)]
        return out

    def to_dict(self) -> dict:
        data = asdict(self)
        data["_help"] = {
            "checks": "A replay counts as the mistake happening again when any check fires.",
            "check_types": CHECK_TYPES,
            "examples": EXAMPLES,
            "tools": "Tools the agent may use. Others are denied, but attempts still show up for tool_call checks.",
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Case:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def cases_dir(workdir: Path) -> Path:
    return workdir / CASES_DIR


def case_path(workdir: Path, case_id: str) -> Path:
    return cases_dir(workdir) / f"{case_id}.json"


def save_case(workdir: Path, case: Case) -> Path:
    path = case_path(workdir, case.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_case(workdir: Path, case_id: str) -> Case:
    path = case_path(workdir, case_id)
    if not path.exists():
        raise FileNotFoundError(f"No case named {case_id!r} in {cases_dir(workdir)}")
    return Case.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_cases(workdir: Path) -> list[Case]:
    folder = cases_dir(workdir)
    if not folder.is_dir():
        return []
    cases = []
    for path in sorted(folder.glob("*.json")):
        try:
            cases.append(Case.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError):
            continue
    return cases


def slugify(text: str, words: int = 6) -> str:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(tokens[:words]) or "case"


def unique_id(workdir: Path, base: str) -> str:
    candidate, n = base, 2
    while case_path(workdir, candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def find_session_file(root: Path, session_id: str) -> Path | None:
    return next(iter(sorted(Path(root).glob(f"*/{session_id}.jsonl"))), None)


def triggering_prompt(turn: Turn, root: Path) -> tuple[str, str]:
    """The developer message that started the agent work being corrected, and its timestamp."""
    path = find_session_file(root, turn.session_id)
    session = claude_code.load_session(path) if path else None
    if session is None:
        return "", ""
    uuid = turn.id.split(":", 1)[1] if ":" in turn.id else ""
    index = next((i for i, e in enumerate(session.events) if e.uuid == uuid), None)
    if index is None:
        return "", ""
    for event in reversed(session.events[:index]):
        if event.kind == HUMAN:
            return event.text, event.timestamp
    return "", ""


def scaffold(turn: Turn, root: Path, rule: str, workdir: Path, case_id: str | None = None) -> Case:
    prompt, prompt_ts = triggering_prompt(turn, root)
    repo = gitutil.toplevel(turn.project) if turn.project else None
    commit = gitutil.commit_before(repo, prompt_ts or turn.timestamp, turn.git_branch) if repo else None
    subdir = ""
    if repo and turn.project:
        rel = os.path.relpath(os.path.realpath(turn.project), os.path.realpath(repo))
        subdir = "" if rel == "." else rel
    notes = [
        "Replays start from the last commit before the original prompt; uncommitted work from that moment is not included.",
        "If the mistake depended on earlier conversation, add that context to the prompt.",
    ]
    if not repo:
        notes.insert(0, f"{turn.project or 'The session folder'} is not a git repository here, so this case cannot be replayed as is.")
    return Case(
        id=case_id or unique_id(workdir, slugify(rule if rule and "TODO" not in rule else turn.text)),
        rule=rule or "TODO: the rule to test, in one line",
        prompt=redact(prompt) or "TODO: the request that led to the mistake",
        repo=str(repo) if repo else "",
        commit=commit or "",
        subdir=subdir,
        checks=[],
        turn_id=turn.id,
        correction=turn.text,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        notes=" ".join(notes),
    )
