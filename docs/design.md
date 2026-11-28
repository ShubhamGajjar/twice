# Design

## The problem

Developers correct their coding agents constantly, and many corrections repeat across
sessions. The usual fix is to write a rule into `CLAUDE.md`, `AGENTS.md`, a skill or a memory
file. Several tools now do that automatically. None of them check whether the rule works.

The evidence says they should:

- Rule files mainly help by priming context, not through their specific instructions
  (5,000+ Claude Code runs on SWE-bench Verified, [arXiv 2604.11088](https://arxiv.org/html/2604.11088)).
- Compliance falls with file size, instruction position and conflicting rules (1,650 Claude
  Code sessions, [arXiv 2605.10039](https://arxiv.org/pdf/2605.10039)).
- In practice most rules rarely fire: one developer found 76% of tracked rules fired in 3 or
  fewer of 175 sessions ([write-up](https://dev.to/xinandeq/why-adding-more-rules-makes-your-agent-dumber-268-rules-14-always-loaded-and-a-tool-to-audit-4e8j)).

## Landscape

| Tool | What it does | What it doesn't |
|---|---|---|
| Claude Code auto memory | records corrections and preferences into `MEMORY.md` | measure whether entries change behavior |
| [Agent.md](https://github.com/Aaditya1273/Agent.md) `learn` | groups repeated corrections from Claude Code logs, proposes rules | test the rules |
| [Blume](https://www.blume.codes/blog/blume-vision) | desktop app proposing rules, skills and hooks across agents | outcome measurement is on its roadmap, not shipped |
| claude-mem | persistent, searchable memory for Claude Code | judge rules |
| [ACE](https://github.com/ace-agent/ace) | research framework for evolving agent playbooks | target coding agents or personal history |

`twice` works alongside these. Whatever writes your rules, `twice` measures them.

## Approach

**Every correction becomes a test. Every rule has to pass it.**

1. **Mine** (v0.1): find the moments you corrected the agent.
2. **Measure the miner** (v0.1): hand labels, precision and estimated recall with confidence
   intervals, and a Claude judge validated against the labels.
3. **Build test cases** (v0.2): for each repeated lesson, capture the situation: the repository
   at the last commit before the original request (logs record `cwd`, `gitBranch` and
   `timestamp`), the request itself, and checks for whether the mistake happened again.
   Checks are deterministic (regex on the diff or the output, changed-file globs, tool calls
   including denied ones, a shell command). An LLM-judged check comes later, with published
   agreement.
4. **Replay** (v0.2): run the agent headless (`claude -p`, later `codex exec`) in a throwaway
   shared clone, with the rule absent, in `CLAUDE.md`, or in the system prompt. Skill and
   hook variants come next. Agents are not deterministic, so variants alternate across
   several trials, and results come with Wilson intervals and a one-sided Fisher exact test.
5. **Verdicts:** keep (measurably helps), promote to a hook (prose isn't reliable enough),
   prune (never fires or changes nothing). Report the token cost of always-loaded rules.
6. **Reruns:** on every model upgrade, and for any third-party skill bundle.

## Data model

```
log file --adapter--> Session(events) --mine--> Turn(score, signals, context)
                                                   |
                       labels.jsonl (you) ---------+--> score: precision, recall, sweep
                       judgments.jsonl (Claude) ---+--> agreement with you; lesson text
                                                   |
                                         build_lessons --> report.md / report.json
```

- **Event kinds:** human, assistant (text plus tool calls), tool_result, interrupt, denial.
- **Population:** human messages that follow agent activity. First messages and messages typed
  before the agent acted are excluded, so every rate has the same denominator.
- **Turn ids** are `<session id>:<event uuid>`, stable across runs, so labels and judgments
  survive re-mining.
- **Files** are append-only JSONL in `~/.twice`; the latest row for an id wins.

## Measurement

- **Precision** comes from labeled flagged turns.
- **Recall** needs labels on unflagged turns: the share of unflagged turns that are corrections,
  times the number of unflagged turns, estimates the misses. The sample is stratified
  (default 70% flagged, 30% unflagged), and the threshold sweep weights each label back to its
  stratum.
- **Intervals:** Wilson 95% intervals on every rate, because a few dozen labels leave real
  uncertainty.
- **Judge agreement:** accuracy, precision, recall and Cohen's kappa against hand labels,
  computed on the labeled sample (which over-represents flagged turns, and the report says so).

## Replay isolation: what was measured

A fair baseline needs the rule out of every channel except the one being tested. Claude Code
2.1.332 was probed with marker strings planted in a project `CLAUDE.md`, a project skill and
the user's global `~/.claude/CLAUDE.md`, with all tools disabled (`--tools ""`) so the model
could only report what was loaded, not go and read files:

| Mode | Project `CLAUDE.md` | Global `CLAUDE.md` |
|---|---|---|
| default, trusted folder | loaded | loaded |
| default, untrusted folder | not loaded | loaded |
| `--setting-sources project` | loaded (if trusted) | loaded |
| `--restricted`, `--safe-mode` | not loaded | not loaded |
| `--restricted` or `--safe-mode` with `--add-dir` | not loaded | not loaded |
| `--append-system-prompt` (any mode) | n/a | n/a, but the appended text arrives |

Trust carries from a trusted folder to its subfolders. `--bare` skips everything but accepts
only an API key, so it can't be the default for subscription users.

Hence the replay setup: `--setting-sources project` in a copy under a trusted folder, with no
MCP servers, auto memory off, no session persistence, an allowlist of file tools, and web tools
blocked. The global `CLAUDE.md` is the one thing that leaks into every variant, so the baseline
is "your setup without this rule". An early probe that left tools enabled gave misleading
answers because the model simply opened the files; probes must run with tools off.

## Privacy

Session logs contain secrets and personal data. Everything runs locally; working files live
outside repositories with owner-only permissions; text is redacted before it is stored; only
`judge` sends anything, and only after confirmation. Tests use synthetic sessions built in
`tests/conftest.py`. No real session data belongs in this repository.

## Risks

- **Cost:** replays use tokens. Replay only the relevant slice, cap budgets, run weekly.
- **Vendors may ship outcome measurement.** The bet is on being open, working across agents,
  and basing verdicts on replays rather than impressions.
- **Some moments can't be replayed** (external services, state that changed). Report the share
  of cases that could be replayed.
- **Regex signals are English-only and blunt.** That is why they are measured, and why the
  judge exists.
