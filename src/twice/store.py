"""Where twice keeps its working files.

Everything lives in `~/.twice` (or `$TWICE_HOME`, or `--workdir`), outside any
repository, because the files quote your own sessions. The directory is
created readable only by you.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from twice.model import Turn

TURNS = "turns.jsonl"
LABELS = "labels.jsonl"
JUDGMENTS = "judgments.jsonl"
REPORT_MD = "report.md"
REPORT_JSON = "report.json"
META = "meta.json"


def default_workdir() -> Path:
    env = os.environ.get("TWICE_HOME")
    return Path(env).expanduser() if env else Path.home() / ".twice"


def ensure_workdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_turns(workdir: Path) -> list[Turn]:
    return [Turn.from_dict(row) for row in read_jsonl(workdir / TURNS)]


def latest_by_id(rows: list[dict]) -> dict[str, dict]:
    """Labels and judgments are append-only; the last entry for an id wins."""
    out: dict[str, dict] = {}
    for row in rows:
        if "id" in row:
            out[row["id"]] = row
    return out


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
