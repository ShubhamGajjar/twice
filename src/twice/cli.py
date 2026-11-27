"""Command line: twice sources | mine | label | judge | score | report."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from twice import __version__, store
from twice.cluster import build_lessons
from twice.label import pick_sample, run_labeling
from twice.mine import mine
from twice.report import build_report
from twice.score import format_scores, judge_agreement, miner_metrics, threshold_sweep
from twice.signals import DEFAULT_THRESHOLD
from twice.sources import claude_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workdir = Path(args.workdir).expanduser() if args.workdir else store.default_workdir()
    handler = {
        "sources": cmd_sources,
        "mine": cmd_mine,
        "label": cmd_label,
        "judge": cmd_judge,
        "score": cmd_score,
        "report": cmd_report,
    }[args.command]
    try:
        return handler(args, workdir)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", help="where twice keeps its files (default ~/.twice or $TWICE_HOME)")

    parser = argparse.ArgumentParser(
        prog="twice",
        description="Find the corrections you keep giving your coding agent, and measure them.",
    )
    parser.add_argument("--version", action="version", version=f"twice {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sources", parents=[common], help="list the Claude Code session logs twice can read")
    p.add_argument("--claude-dir", type=Path, help="Claude Code projects folder (default ~/.claude/projects)")

    p = sub.add_parser("mine", parents=[common], help="find corrections in your sessions and group them into lessons")
    p.add_argument("--claude-dir", type=Path, help="Claude Code projects folder (default ~/.claude/projects)")
    p.add_argument("--project", help="only sessions whose working directory contains this text")
    p.add_argument("--since", help="only sessions active on or after this date (YYYY-MM-DD)")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"score needed to flag a message (default {DEFAULT_THRESHOLD})")
    p.add_argument("--top", type=int, default=10, help="lessons to show (default 10)")

    p = sub.add_parser("label", parents=[common], help="hand-label a sample so the flags can be measured")
    p.add_argument("--n", type=int, default=40, help="messages to label this round (default 40)")
    p.add_argument("--unflagged-share", type=float, default=0.3, help="share drawn from unflagged messages (default 0.3)")
    p.add_argument("--seed", type=int, default=None, help="random seed for a repeatable sample")

    p = sub.add_parser("judge", parents=[common], help="ask Claude to confirm each flag and write its lesson (optional)")
    p.add_argument("--model", default=None, help="Claude model id (default claude-opus-5-5)")
    p.add_argument("--effort", choices=["low", "medium", "high"], default="low", help="model effort (default low)")
    p.add_argument("--max-calls", type=int, default=100, help="most API calls in this run (default 100)")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")

    sub.add_parser("score", parents=[common], help="measure the flags (and the judge) against your labels")

    p = sub.add_parser("report", parents=[common], help="rebuild the report from saved results")
    p.add_argument("--top", type=int, default=10, help="lessons to show (default 10)")
    return parser


def cmd_sources(args, workdir: Path) -> int:
    root = Path(args.claude_dir).expanduser() if args.claude_dir else claude_code.default_root()
    paths = claude_code.discover(root)
    if not paths:
        print(f"No Claude Code sessions found in {root}.")
        return 1
    by_project = Counter(p.parent.name for p in paths)
    size = sum(p.stat().st_size for p in paths)
    print(f"{len(paths)} Claude Code sessions in {root} ({size / 1e6:.0f} MB), {len(by_project)} project folders:")
    for name, count in by_project.most_common():
        print(f"  {count:>4}  {name}")
    return 0


def cmd_mine(args, workdir: Path) -> int:
    root = Path(args.claude_dir).expanduser() if args.claude_dir else claude_code.default_root()
    paths = claude_code.discover(root)
    if not paths:
        print(f"No Claude Code sessions found in {root}. Use --claude-dir to point at another folder.")
        return 1
    sessions = []
    skipped = 0
    for path in paths:
        try:
            session = claude_code.load_session(path)
        except OSError:
            skipped += 1
            continue
        if session is None:
            continue
        if args.project and args.project.lower() not in session.project.lower():
            continue
        if args.since and session.ended[:10] < args.since:
            continue
        sessions.append(session)
    turns = mine(sessions, args.threshold)
    store.ensure_workdir(workdir)
    store.write_jsonl(workdir / store.TURNS, (t.to_dict() for t in turns))
    meta = {
        "mined_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": claude_code.SOURCE,
        "root": str(root),
        "sessions": len(sessions),
        "projects": len({s.project for s in sessions}),
        "threshold": args.threshold,
        "filters": {"project": args.project, "since": args.since},
    }
    store.write_json(workdir / store.META, meta)
    if skipped:
        print(f"Skipped {skipped} unreadable files.")
    return _write_report(workdir, turns, meta, args.top)


def cmd_label(args, workdir: Path) -> int:
    turns = _need_turns(workdir)
    if turns is None:
        return 1
    threshold = store.read_json(workdir / store.META).get("threshold", DEFAULT_THRESHOLD)
    labels_path = workdir / store.LABELS
    labeled = set(store.latest_by_id(store.read_jsonl(labels_path)))
    sample = pick_sample(turns, labeled, args.n, args.unflagged_share, args.seed)
    if not sample:
        print("Nothing left to label.")
        return 0
    written = run_labeling(sample, labels_path, threshold)
    total = len(store.latest_by_id(store.read_jsonl(labels_path)))
    print(f"Saved {written} labels ({total} in total). Run `twice score` to see how the flags hold up.")
    return 0


def cmd_judge(args, workdir: Path) -> int:
    from twice.llm import DEFAULT_MODEL, Judge, LLMUnavailable, run_judgments

    turns = _need_turns(workdir)
    if turns is None:
        return 1
    model = args.model or DEFAULT_MODEL
    labels = store.latest_by_id(store.read_jsonl(workdir / store.LABELS))
    judged = _ok_judgments(workdir)
    # Labeled messages go first so the judge can be validated against you early.
    labeled_first = [t for t in turns if t.id in labels]
    flagged = sorted((t for t in turns if t.is_candidate and t.id not in labels), key=lambda t: -t.score)
    queue = [t for t in labeled_first + flagged if t.id not in judged][: args.max_calls]
    if not queue:
        print("Nothing left to judge.")
        return 0
    print(f"About to send {len(queue)} redacted messages to {model} (effort {args.effort}) through the Anthropic API.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1
    try:
        judge = Judge(model=model, effort=args.effort)
    except LLMUnavailable as exc:
        print(exc)
        return 1
    done, problem = run_judgments(queue, judge, workdir / store.JUDGMENTS)
    print(f"Judged {done} message(s).")
    if problem:
        print(problem)
        return 1
    meta = store.read_json(workdir / store.META)
    return _write_report(workdir, turns, meta, 10)


def cmd_score(args, workdir: Path) -> int:
    turns = _need_turns(workdir)
    if turns is None:
        return 1
    threshold = store.read_json(workdir / store.META).get("threshold", DEFAULT_THRESHOLD)
    labels = store.latest_by_id(store.read_jsonl(workdir / store.LABELS))
    metrics = miner_metrics(turns, labels, threshold)
    sweep = threshold_sweep(turns, labels, threshold)
    agreement = judge_agreement(labels, _ok_judgments(workdir))
    print(format_scores(metrics, sweep, agreement))
    return 0


def cmd_report(args, workdir: Path) -> int:
    turns = _need_turns(workdir)
    if turns is None:
        return 1
    return _write_report(workdir, turns, store.read_json(workdir / store.META), args.top)


def _write_report(workdir: Path, turns: list, meta: dict, top: int) -> int:
    flagged_ids = {t.id for t in turns if t.is_candidate}
    judgments = {i: j for i, j in _ok_judgments(workdir).items() if i in flagged_ids}
    complete = bool(flagged_ids) and flagged_ids <= judgments.keys()
    grouping = "Claude judgments" if complete else "text similarity"
    lessons = build_lessons(turns, judgments if complete else None)
    md, data = build_report(turns, lessons, meta, grouping, top)
    store.ensure_workdir(workdir)
    (workdir / store.REPORT_MD).write_text(md, encoding="utf-8")
    store.write_json(workdir / store.REPORT_JSON, data)

    print(
        f"Read {meta.get('sessions', 0)} sessions from {meta.get('projects', 0)} projects: "
        f"{data['messages']} messages after the agent acted, {data['flagged']} flagged as corrections."
    )
    if judgments and not complete:
        print(f"{len(judgments)} of {len(flagged_ids)} flagged messages judged; run `twice judge` to finish, then lessons use Claude's wording.")
    if lessons:
        print("Top lessons (most sessions first):")
        for i, lesson in enumerate(lessons[: min(top, 5)], 1):
            noun = "session" if lesson.sessions == 1 else "sessions"
            print(f"  {i}. [{lesson.sessions} {noun}, {lesson.count}x] {_short(lesson.label)}")
    print(f"Full report: {_tilde(workdir / store.REPORT_MD)}")
    print("Next: `twice label` to check the flags by hand, then `twice score`.")
    return 0


def _need_turns(workdir: Path):
    turns = store.load_turns(workdir)
    if not turns:
        print("No mined sessions yet. Run `twice mine` first.")
        return None
    return turns


def _ok_judgments(workdir: Path) -> dict[str, dict]:
    rows = store.latest_by_id(store.read_jsonl(workdir / store.JUDGMENTS))
    return {i: r for i, r in rows.items() if r.get("status") == "ok"}


def _short(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _tilde(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


if __name__ == "__main__":
    sys.exit(main())
