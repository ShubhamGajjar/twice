"""Replay a case with and without its rule, and measure how often the mistake comes back.

Each trial gets a fresh copy of the repository at the case's commit (a shared
clone, so your repository is only read, with its remotes removed). The rule is
applied for the variant, the agent runs the original prompt, and the case's
checks inspect what it did. Agents don't behave the same way twice, so each
variant runs several times and results carry intervals and a significance test.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from twice import gitutil
from twice.cases import Case
from twice.checks import Evidence, evaluate
from twice.score import wilson

VARIANTS = {
    "none": "no rule (your setup as it was)",
    "claude_md": "rule added to the repository's CLAUDE.md",
    "system": "rule appended to the system prompt",
}
MIN_TRIALS_FOR_VERDICT = 3
RULE_BLOCK = "\n## Rules\n\n- {rule}\n"


def prepare_copy(case: Case, root: Path, label: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=root))
    gitutil.git("clone", "--quiet", "--shared", "--no-checkout", case.repo, str(dest), cwd=root)
    gitutil.git("checkout", "--quiet", "--detach", case.commit, cwd=dest)
    for remote in gitutil.git("remote", cwd=dest).split():
        gitutil.git("remote", "remove", remote, cwd=dest)
    return dest


def apply_variant(copy: Path, case: Case, variant: str) -> str | None:
    """Set up the copy for `variant`. Returns text for --append-system-prompt, if any."""
    if variant == "claude_md":
        path = copy / "CLAUDE.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Project instructions\n"
        path.write_text(existing.rstrip() + "\n" + RULE_BLOCK.format(rule=case.rule), encoding="utf-8")
    elif variant == "system":
        return case.rule
    elif variant != "none":
        raise ValueError(f"unknown variant {variant!r}")
    return None


def snapshot(copy: Path) -> None:
    """Commit the setup so the agent's changes can be diffed on their own."""
    gitutil.git("add", "-A", cwd=copy)
    gitutil.git("commit", "--quiet", "--allow-empty", "--no-verify", "-m", "twice: replay setup", cwd=copy)


def collect_changes(copy: Path) -> tuple[list[str], list[str]]:
    gitutil.git("add", "-A", cwd=copy)
    diff = gitutil.git("diff", "--cached", "--no-color", "--no-ext-diff", "--unified=0", "HEAD", cwd=copy)
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    files = [f for f in gitutil.git("diff", "--cached", "--name-only", "HEAD", cwd=copy).splitlines() if f]
    return added, files


def run_trial(case: Case, variant: str, trial: int, agent, root: Path, run_id: str, transcripts: Path | None = None, keep: bool = False) -> dict:
    row = {
        "run_id": run_id,
        "case_id": case.id,
        "variant": variant,
        "trial": trial,
        "agent": getattr(agent, "name", "agent"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    copy = None
    try:
        copy = prepare_copy(case, root, f"{case.id}-{variant}-{trial}")
        system_rule = apply_variant(copy, case, variant)
        snapshot(copy)
        cwd = copy / case.subdir if case.subdir else copy
        run = agent.run(cwd, case.prompt, system_rule, case.tools)
        added, files = collect_changes(copy)
        evidence = Evidence(
            cwd=cwd, added_lines=added, changed_files=files,
            output=f"{run.assistant_text}\n{run.final_text}", tool_calls=run.tool_calls,
        )
        fired = []
        for check in case.checks:
            hit, detail = evaluate(check, evidence)
            if hit:
                fired.append({"type": check["type"], "description": check.get("description", ""), "detail": detail})
        row.update(
            status="ok" if run.ok else "error",
            error=run.error,
            mistake=bool(fired) if run.ok else None,
            fired=fired,
            changed_files=files,
            model=run.model,
            cost_usd=run.cost_usd,
            turns=run.turns,
            duration_ms=run.duration_ms,
            denied_tools=sorted({str(d.get("tool_name", "")) for d in run.denials if isinstance(d, dict)} - {""}),
            mcp_servers=len(run.mcp_servers),
        )
        if transcripts is not None:
            transcripts.mkdir(parents=True, exist_ok=True)
            (transcripts / f"{case.id}-{variant}-{trial}.jsonl").write_text("\n".join(run.transcript) + "\n", encoding="utf-8")
        if keep:
            row["copy"] = str(copy)
    except gitutil.GitError as exc:
        row.update(status="error", error=f"git: {exc}", mistake=None)
    finally:
        if copy is not None and not keep:
            shutil.rmtree(copy, ignore_errors=True)
    return row


def fisher_one_sided(base_hits: int, base_n: int, arm_hits: int, arm_n: int) -> float:
    """P(the rule arm has this few mistakes or fewer) if the rule made no difference."""
    total = base_hits + arm_hits
    n = base_n + arm_n
    denom = math.comb(n, total)
    if denom == 0:
        return 1.0
    p = 0.0
    for k in range(arm_hits + 1):
        if k > total or total - k > base_n:
            continue
        p += math.comb(arm_n, k) * math.comb(base_n, total - k) / denom
    return min(1.0, p)


def summarize(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """{case_id: {variant: stats}} over successful runs."""
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["case_id"]][row["variant"]].append(row)
    out: dict[str, dict[str, dict]] = {}
    for case_id, variants in grouped.items():
        out[case_id] = {}
        for variant, items in variants.items():
            ok = [r for r in items if r.get("status") == "ok"]
            hits = sum(1 for r in ok if r.get("mistake"))
            costs = [r["cost_usd"] for r in items if isinstance(r.get("cost_usd"), (int, float))]
            lo, hi = wilson(hits, len(ok))
            out[case_id][variant] = {
                "runs": len(ok),
                "errors": len(items) - len(ok),
                "mistakes": hits,
                "rate": hits / len(ok) if ok else None,
                "ci": (lo, hi),
                "cost_usd": sum(costs) if costs else None,
            }
    return out


def verdict(base: dict | None, arm: dict) -> tuple[str, float | None]:
    if base is None or base["runs"] == 0:
        return "no baseline yet (run the 'none' variant)", None
    if base["runs"] < MIN_TRIALS_FOR_VERDICT or arm["runs"] < MIN_TRIALS_FOR_VERDICT:
        return f"too few runs (need {MIN_TRIALS_FOR_VERDICT}+ per variant)", None
    if base["mistakes"] == 0:
        return "the mistake didn't come back even without the rule: add context to the case, or the rule may not be needed", None
    p = fisher_one_sided(base["mistakes"], base["runs"], arm["mistakes"], arm["runs"])
    if p < 0.05:
        return "the rule helps", p
    if arm["rate"] >= base["rate"]:
        return "no sign the rule helps", p
    return "fewer mistakes with the rule, but not enough runs to be sure", p


def format_summary(summary: dict[str, dict[str, dict]]) -> str:
    if not summary:
        return "No replay results yet. Run `twice replay <case>`."
    lines = []
    for case_id, variants in sorted(summary.items()):
        lines.append(f"{case_id}")
        lines.append(f"  {'variant':<10} {'mistakes':>9} {'rate':>6}  {'95% CI':<11} {'errors':>6}  verdict")
        base = variants.get("none")
        for variant in [v for v in VARIANTS if v in variants] + sorted(set(variants) - set(VARIANTS)):
            s = variants[variant]
            rate = "n/a" if s["rate"] is None else f"{s['rate'] * 100:.0f}%"
            ci = f"{s['ci'][0] * 100:.0f}-{s['ci'][1] * 100:.0f}%" if s["runs"] else ""
            text = ""
            if variant != "none":
                label, p = verdict(base, s)
                text = label + (f" (p={p:.3f})" if p is not None else "")
            lines.append(f"  {variant:<10} {s['mistakes']:>4}/{s['runs']:<4} {rate:>6}  {ci:<11} {s['errors']:>6}  {text}")
        costs = [s["cost_usd"] for s in variants.values() if s["cost_usd"] is not None]
        if costs:
            lines.append(f"  cost so far: ${sum(costs):.2f}")
        lines.append("")
    return "\n".join(lines).rstrip()
