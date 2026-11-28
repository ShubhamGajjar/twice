"""Command line: twice sources | mine | label | judge | score | report | case | replay | results | doctor."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from twice import __version__, store
from twice.cases import list_cases, load_case, save_case, scaffold
from twice.cluster import build_lessons
from twice.label import pick_sample, run_labeling
from twice.mine import mine
from twice.report import build_report
from twice.score import format_scores, judge_agreement, miner_metrics, threshold_sweep
from twice.signals import DEFAULT_THRESHOLD
from twice.sources import claude_code

REPLAYS = "replays.jsonl"


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
        "case": cmd_case,
        "replay": cmd_replay,
        "results": cmd_results,
        "doctor": cmd_doctor,
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

    p = sub.add_parser("case", help="turn a correction into a replayable test case")
    case_sub = p.add_subparsers(dest="case_command", required=True)
    c = case_sub.add_parser("new", parents=[common], help="create a case from a lesson or a turn")
    pick = c.add_mutually_exclusive_group(required=True)
    pick.add_argument("--lesson", type=int, help="lesson number from the latest report")
    pick.add_argument("--turn", help="turn id (listed in report.json)")
    c.add_argument("--rule", help="the rule to test (default: Claude's or your lesson text, if any)")
    c.add_argument("--id", dest="case_id", help="case name (default: made from the rule)")
    case_sub.add_parser("list", parents=[common], help="list cases and whether they are ready")
    c = case_sub.add_parser("show", parents=[common], help="show one case and what it still needs")
    c.add_argument("case_id")

    p = sub.add_parser("replay", parents=[common], help="replay cases with and without their rule")
    p.add_argument("cases", nargs="*", help="case ids (default: every ready case)")
    p.add_argument("--variants", default="none,claude_md", help="comma-separated: none, claude_md, system (default none,claude_md)")
    p.add_argument("--trials", type=int, default=5, help="runs per variant (default 5)")
    p.add_argument("--model", help="Claude Code model alias or id (default: your Claude Code default)")
    p.add_argument("--max-budget-usd", type=float, default=1.0, help="spending cap per run in USD (default 1.0)")
    p.add_argument("--timeout", type=int, default=900, help="seconds per run (default 900)")
    p.add_argument("--replay-root", type=Path, help="folder for replay copies (default <workdir>/replays)")
    p.add_argument("--keep", action="store_true", help="keep the copies for inspection")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("--dry-run", action="store_true", help="prepare one copy and print the command, without running the agent")

    p = sub.add_parser("results", parents=[common], help="summarize replay results")
    p.add_argument("cases", nargs="*", help="case ids (default: all)")

    p = sub.add_parser("doctor", parents=[common], help="check that replays will run as intended")
    p.add_argument("--replay-root", type=Path, help="folder for replay copies (default <workdir>/replays)")
    p.add_argument("--probe", action="store_true", help="also run one short Claude Code call to confirm CLAUDE.md delivery")
    p.add_argument("--model", help="model for the probe (default: your Claude Code default)")
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
    if lessons:
        print("Or test a lesson: `twice case new --lesson 1`, add a check, then `twice replay`.")
    return 0


def cmd_case(args, workdir: Path) -> int:
    if args.case_command == "list":
        cases = list_cases(workdir)
        if not cases:
            print("No cases yet. Create one with `twice case new --lesson 1`.")
            return 0
        runs = Counter(r.get("case_id") for r in store.read_jsonl(workdir / REPLAYS))
        for case in cases:
            problems = case.problems()
            state = "ready" if not problems else f"needs {len(problems)} fix(es)"
            print(f"  {case.id:<40} {state:<16} {runs.get(case.id, 0):>3} runs  rule: {_short(case.rule, 60)}")
        return 0
    if args.case_command == "show":
        try:
            case = load_case(workdir, args.case_id)
        except FileNotFoundError as exc:
            print(exc)
            return 1
        data = case.to_dict()
        data.pop("_help", None)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        _print_problems(case)
        return 0
    return _case_new(args, workdir)


def _case_new(args, workdir: Path) -> int:
    turns = _need_turns(workdir)
    if turns is None:
        return 1
    by_id = {t.id: t for t in turns}
    if args.lesson is not None:
        lessons = store.read_json(workdir / store.REPORT_JSON).get("lessons", [])
        if not 1 <= args.lesson <= len(lessons):
            print(f"No lesson {args.lesson}; the latest report has {len(lessons)}.")
            return 1
        members = [by_id[i] for i in lessons[args.lesson - 1]["turn_ids"] if i in by_id]
        if not members:
            print("That lesson's messages are no longer in the mined set; run `twice mine` again.")
            return 1
        turn = max(members, key=lambda t: t.timestamp)  # the most recent time you made this correction
    else:
        turn = by_id.get(args.turn)
        if turn is None:
            print(f"No turn {args.turn!r} in the mined set.")
            return 1
    judgment = _ok_judgments(workdir).get(turn.id, {})
    label = store.latest_by_id(store.read_jsonl(workdir / store.LABELS)).get(turn.id, {})
    rule = args.rule or judgment.get("lesson") or label.get("lesson") or ""
    root = Path(store.read_json(workdir / store.META).get("root") or claude_code.default_root())
    case = scaffold(turn, root, rule, workdir, args.case_id)
    path = save_case(workdir, case)
    print(f"Created {_tilde(path)}")
    print(f"  correction: {_short(case.correction, 90)}")
    print(f"  prompt:     {_short(case.prompt, 90)}")
    print(f"  repo:       {case.repo or '(not found)'} @ {case.commit[:10] or '(no commit)'}")
    _print_problems(case)
    print("Edit the file (check examples are in its _help section), then run `twice replay " + case.id + "`.")
    return 0


def _print_problems(case) -> None:
    problems = case.problems()
    if problems:
        print("Still needed:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("Ready to replay.")


def cmd_replay(args, workdir: Path) -> int:
    from twice.agent import ClaudeCodeAgent, trusted
    from twice.replay import VARIANTS, apply_variant, format_summary, prepare_copy, run_trial, snapshot, summarize

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown or not variants:
        print(f"Unknown variant(s): {', '.join(unknown) or '(none given)'}. Choose from: {', '.join(VARIANTS)}.")
        return 1
    try:
        cases = [load_case(workdir, cid) for cid in args.cases] if args.cases else [c for c in list_cases(workdir) if not c.problems()]
    except FileNotFoundError as exc:
        print(exc)
        return 1
    if not cases:
        print("No ready cases. Create one with `twice case new --lesson 1`, add a check, then rerun.")
        return 1
    for case in cases:
        if case.problems():
            print(f"Case {case.id} is not ready:")
            _print_problems(case)
            return 1
    root = (args.replay_root or workdir / "replays").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if "claude_md" in variants and trusted(root) is not True:
        print(_trust_help(root))
        return 1
    agent = ClaudeCodeAgent(model=args.model, max_budget_usd=args.max_budget_usd, timeout=args.timeout)
    if not agent.available():
        print("The `claude` command was not found on PATH.")
        return 1

    if args.dry_run:
        case = cases[0]
        variant = variants[-1]
        copy = prepare_copy(case, root, f"{case.id}-dryrun")
        rule = apply_variant(copy, case, variant)
        snapshot(copy)
        print(f"Copy:    {copy}{' (kept)' if args.keep else ''}")
        print(f"Runs in: {copy / case.subdir if case.subdir else copy}")
        print(f"Variant: {variant} ({VARIANTS[variant]})")
        print(f"Command: {shlex.join(agent.command(rule, case.tools))}")
        print(f"Prompt (sent on stdin): {_short(case.prompt, 200)}")
        if not args.keep:
            shutil.rmtree(copy, ignore_errors=True)
        return 0

    total = len(cases) * len(variants) * args.trials
    print(
        f"About to run {total} Claude Code sessions ({len(cases)} case(s) x {len(variants)} variants x {args.trials} trials) "
        f"with {args.model or 'your default model'}, each capped at ${args.max_budget_usd:g} "
        f"(worst case ${total * args.max_budget_usd:.2f})."
    )
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    transcripts = workdir / "transcripts" / run_id
    for case in cases:
        # Variants alternate within each trial so drift over time hits every variant alike.
        for trial in range(1, args.trials + 1):
            for variant in variants:
                row = run_trial(case, variant, trial, agent, root, run_id, transcripts, args.keep)
                store.append_jsonl(workdir / REPLAYS, row)
                outcome = {True: "MISTAKE", False: "clean"}.get(row.get("mistake"), f"error: {row.get('error', '')[:80]}")
                cost = f"  ${row['cost_usd']:.3f}" if isinstance(row.get("cost_usd"), (int, float)) else ""
                print(f"  {case.id} {variant:<9} #{trial}: {outcome}{cost}")
                if row.get("mcp_servers"):
                    print("  warning: MCP servers were loaded in this run; replays should run without them.")
    ids = {c.id for c in cases}
    print()
    print(format_summary(summarize([r for r in store.read_jsonl(workdir / REPLAYS) if r.get("case_id") in ids])))
    print(f"Transcripts: {_tilde(transcripts)}")
    return 0


def cmd_results(args, workdir: Path) -> int:
    from twice.replay import format_summary, summarize

    rows = store.read_jsonl(workdir / REPLAYS)
    if args.cases:
        rows = [r for r in rows if r.get("case_id") in set(args.cases)]
    print(format_summary(summarize(rows)))
    return 0


def cmd_doctor(args, workdir: Path) -> int:
    from twice.agent import trusted

    good = True
    exe = shutil.which("claude")
    if exe:
        version = subprocess.run([exe, "--version"], check=False, capture_output=True, text=True).stdout.strip()
        print(f"ok    claude found: {version}")
    else:
        print("fail  `claude` is not on PATH")
        good = False
    root = (args.replay_root or workdir / "replays").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    trust = trusted(root)
    if trust:
        print(f"ok    replay folder is trusted in Claude Code: {root}")
    else:
        print(f"fail  replay folder is not trusted, so the claude_md variant can't load its rule: {root}")
        print(_indent(_trust_help(root)))
        good = False
    cases = list_cases(workdir)
    ready = [c for c in cases if not c.problems()]
    print(f"{'ok' if ready else 'note':<5} {len(ready)} of {len(cases)} case(s) ready to replay")
    if args.probe and exe:
        good = _probe(exe, root, args.model) and good
    return 0 if good else 1


def _probe(exe: str, root: Path, model: str | None) -> bool:
    folder = Path(tempfile.mkdtemp(prefix="doctor-", dir=root))
    try:
        subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
        (folder / "CLAUDE.md").write_text("# Probe\n\n- Marker: CANARY-TWICE-PROBE\n", encoding="utf-8")
        cmd = [exe, "-p", "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "project"]
        if model:
            cmd += ["--model", model]
        question = "Without using any tools, list every string beginning with CANARY- in your context, or reply NONE."
        out = subprocess.run(cmd, check=False, input=question, cwd=folder, capture_output=True, text=True, timeout=300).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"fail  probe could not run: {exc}")
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if "CANARY-TWICE-PROBE" in out:
        print("ok    probe: a CLAUDE.md in the replay folder reaches the agent")
        return True
    print("fail  probe: the agent did not see the replay folder's CLAUDE.md")
    return False


def _trust_help(root: Path) -> str:
    return (
        f"Claude Code only loads a project's CLAUDE.md in folders you trust, and replay copies live in {root}.\n"
        f"Trust it once: run `cd {shlex.quote(str(root))} && claude`, accept the trust prompt, then type /exit.\n"
        "Or pass --replay-root with a folder inside one you already trust. The 'system' variant works without this."
    )


def _indent(text: str) -> str:
    return "\n".join("      " + line for line in text.splitlines())


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
