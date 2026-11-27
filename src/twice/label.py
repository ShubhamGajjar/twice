"""Hand-label a sample of turns so the miner (and the Claude judge) can be measured.

The sample is stratified: most items come from flagged turns (to measure
precision) and the rest from unflagged turns (to measure what the miner
misses). Each label records which stratum it came from, so `twice score` can
weight the two back to the full population.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from twice.model import Turn
from twice.store import append_jsonl


def pick_sample(
    turns: list[Turn],
    already_labeled: set[str],
    n: int = 40,
    unflagged_share: float = 0.3,
    seed: int | None = None,
) -> list[Turn]:
    rng = random.Random(seed)
    flagged = [t for t in turns if t.is_candidate and t.id not in already_labeled]
    unflagged = [t for t in turns if not t.is_candidate and t.id not in already_labeled]
    n_unflagged = min(len(unflagged), round(n * unflagged_share))
    n_flagged = min(len(flagged), n - n_unflagged)
    # Fill from the other stratum if one runs short.
    n_unflagged = min(len(unflagged), n - n_flagged)
    sample = rng.sample(flagged, n_flagged) + rng.sample(unflagged, n_unflagged)
    rng.shuffle(sample)
    return sample


def run_labeling(
    sample: list[Turn],
    labels_path: Path,
    threshold: float,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
) -> int:
    """Interactive loop. Returns how many labels were written."""
    ask = ask or (lambda prompt: input(prompt))
    say = say or print
    written = 0
    say("Is each message below a correction of something the agent did?")
    say("A correction pushes back: a mistake, an unwanted action, an ignored instruction.")
    say("New requests, questions and approvals are not corrections.\n")
    for i, turn in enumerate(sample, 1):
        say(f"--- {i}/{len(sample)}  {_when(turn.timestamp)}  {turn.project or '(unknown project)'}")
        if turn.context:
            say(_indent(turn.context))
        say("You said:")
        say(_indent(_clip(turn.text, 900)))
        answer = _ask_choice(ask, "Correction? [y]es / [n]o / [s]kip / [q]uit: ")
        if answer == "q":
            break
        if answer == "s":
            continue
        lesson = ""
        if answer == "y":
            lesson = ask("Optional, the rule in one line (enter to skip): ").strip()
        append_jsonl(
            labels_path,
            {
                "id": turn.id,
                "is_correction": answer == "y",
                "lesson": lesson,
                "stratum": "flagged" if turn.is_candidate else "unflagged",
                "score": turn.score,
                "threshold": threshold,
                "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        written += 1
        say("")
    return written


def _ask_choice(ask: Callable[[str], str], prompt: str) -> str:
    while True:
        try:
            answer = ask(prompt).strip().lower()[:1]
        except EOFError:
            return "q"
        if answer in ("y", "n", "s", "q"):
            return answer


def _when(ts: str) -> str:
    return ts[:16].replace("T", " ") if ts else ""


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [...]"
