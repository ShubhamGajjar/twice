"""Write the report: which corrections you keep repeating, and where."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from twice.cluster import Lesson
from twice.model import Turn


def build_report(
    turns: list[Turn],
    lessons: list[Lesson],
    meta: dict,
    grouping: str,
    top: int = 10,
) -> tuple[str, dict]:
    flagged = [t for t in turns if t.is_candidate]
    repeated = [l for l in lessons if l.sessions >= 2]
    signals = Counter(s for t in flagged for s in t.signals)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    data = {
        "generated_at": generated,
        "sessions": meta.get("sessions", 0),
        "projects": meta.get("projects", 0),
        "threshold": meta.get("threshold"),
        "messages": len(turns),
        "flagged": len(flagged),
        "repeated_lessons": len(repeated),
        "grouping": grouping,
        "signals": dict(signals.most_common()),
        "lessons": [l.to_dict() for l in lessons],
    }

    share = f" ({len(flagged) / len(turns) * 100:.0f}%)" if turns else ""
    md = [
        "# twice report",
        "",
        f"Generated {generated[:10]} from {data['sessions']} sessions across {data['projects']} projects.",
        "",
        "| | |",
        "|---|---|",
        f"| Messages you sent after the agent acted | {len(turns)} |",
        f"| Flagged as corrections | {len(flagged)}{share} |",
        f"| Lessons repeated in 2+ sessions | {len(repeated)} |",
        f"| Grouping | {grouping} |",
        "",
        "## Lessons you repeated most",
        "",
    ]
    if not lessons:
        md.append("No corrections found at this threshold.")
    for i, lesson in enumerate(lessons[:top], 1):
        md.append(f"### {i}. {lesson.label}")
        md.append("")
        md.append(f"- Seen in {lesson.sessions} session(s), {lesson.count} time(s)")
        if lesson.projects:
            md.append(f"- Projects: {', '.join(lesson.projects)}")
        if lesson.first_seen:
            md.append(f"- First seen {lesson.first_seen[:10]}, last seen {lesson.last_seen[:10]}")
        for example in lesson.examples:
            md.append(f"  > {example}")
        md.append(f"- Test it: `twice case new --lesson {i}`")
        md.append("")
    if signals:
        md += ["## What triggered the flags", "", "| Signal | Messages |", "|---|---|"]
        md += [f"| {name} | {count} |" for name, count in signals.most_common()]
        md.append("")
    md += [
        "## How far to trust this",
        "",
        "These flags come from simple rules, so some are wrong. Run `twice label` to check a",
        "sample by hand and `twice score` to see the measured precision and recall.",
        "",
    ]
    return "\n".join(md), data
