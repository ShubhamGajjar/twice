"""Small git helpers. Every call carries a fixed identity so commits work on any machine."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

IDENTITY = ("-c", "user.name=twice", "-c", "user.email=twice@localhost", "-c", "commit.gpgsign=false")


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: Path | str, timeout: int = 300) -> str:
    try:
        proc = subprocess.run(
            ["git", *IDENTITY, *args], check=False, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} exited {proc.returncode}")
    return proc.stdout.strip()


def toplevel(path: Path | str) -> Path | None:
    p = Path(path)
    if not p.is_dir():
        return None
    try:
        return Path(git("rev-parse", "--show-toplevel", cwd=p))
    except GitError:
        return None


def git_date(timestamp: str) -> str:
    """'2026-11-10T09:30:00.123Z' -> '2026-11-10 09:30:00 +0000' (a form git always parses)."""
    try:
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")


def commit_before(repo: Path | str, timestamp: str, branch: str = "") -> str | None:
    """The last commit on `branch` (or HEAD) made before `timestamp`."""
    refs = [branch, "HEAD"] if branch else ["HEAD"]
    for ref in refs:
        try:
            sha = git("rev-list", "-1", f"--before={git_date(timestamp)}", ref, "--", cwd=repo)
        except GitError:
            continue
        if sha:
            return sha
    return None
