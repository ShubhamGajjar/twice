# twice

**Never correct your coding agent twice.**

You tell your agent "don't use em dashes", "run the tests before pushing", "stop editing the
changelog by hand". A week later you're saying it again. `twice` reads your Claude Code session
logs, finds the corrections you keep repeating, and measures how well it found them. Next, it
will measure which rules in your `CLAUDE.md` actually stop the mistakes.

> **Status: v0.1, early.** Mining, hand labeling, the Claude judge and scoring work today on
> Claude Code logs. Replaying sessions to test rules is the next milestone. See the
> [roadmap](#roadmap).

## Why

Plenty of tools turn corrections into rules: Claude Code's own auto memory, Agent.md, Blume,
claude-mem. What nobody measures is whether a rule works once it's written.

- A study of 5,000+ Claude Code runs found rule files help mostly by priming context, not
  through their specific instructions ([arXiv 2604.11088](https://arxiv.org/html/2604.11088)).
- Instruction compliance drops as files grow and rules conflict
  ([arXiv 2605.10039](https://arxiv.org/pdf/2605.10039)).

So rule files keep growing and nobody knows which lines earn their place. `twice` starts from
the evidence you already have: the corrections in your own sessions.

## Quickstart

```bash
pipx install "twice[llm] @ git+https://github.com/ShubhamGajjar/twice"

twice mine     # read ~/.claude/projects, flag corrections, group them into lessons
twice label    # hand-label about 40 messages (a few minutes)
twice score    # measured precision and recall of the flags
twice judge    # optional: Claude confirms each flag and writes the lesson as a rule
twice report   # rebuild the report from saved results
```

Python 3.10+. Mining has no dependencies. `judge` needs the `llm` extra and an Anthropic API
key (`ANTHROPIC_API_KEY`, or a profile from `ant auth login`).

## What it looks like

These runs use the synthetic sessions in the test suite, so you can reproduce them.

```text
$ twice mine
Read 3 sessions from 2 projects: 10 messages after the agent acted, 6 flagged as corrections.
Top lessons (most sessions first):
  1. [2 sessions, 2x] about: never, dash, comma, em
  2. [1 session, 1x] about: delete, don't, test, fix
  3. [1 session, 1x] about: hand, don't, edit, changelog
  4. [1 session, 1x] about: push, test, suite, run
  5. [1 session, 1x] about: test, folder, component, touch
Full report: ~/.twice/report.md
```

The report quotes each correction, where it happened and when. After `twice judge`, lessons
are grouped by the rule Claude wrote for each one ("Never use em dashes in user-facing
text.") instead of by shared words, which groups repeats far more reliably.

After labeling all 10 messages by hand:

```text
$ twice score
Population: 10 messages you sent after the agent acted
Flagged at threshold 1.0: 6

Miner
  precision: 100%  (95% CI 61%-100%, 6 labeled)
  unflagged that were corrections: 0%  (95% CI 0%-49%, 4 labeled)
  recall:    100%  (estimated)
  F1:        100%

  threshold  precision  recall
        1.0       100%    100%
        1.6       100%     83%
        2.0       100%     67%
        2.5       100%     50%
```

Perfect scores on six labels prove little, and the intervals say exactly that. On real
sessions, expect lower numbers and label more.

## How it works

1. **Read.** An adapter turns an agent's logs into events: your messages, the agent's replies
   and tool calls, tool results, interruptions and rejected tool calls. Claude Code is supported
   today (`~/.claude/projects/*/*.jsonl`, or `$CLAUDE_CONFIG_DIR`). Harness-injected messages,
   subagent sidechains and thinking blocks are skipped.
2. **Flag.** Every message you sent after the agent acted gets a score from transparent rules,
   regexes with weights in [`signals.py`](src/twice/signals.py). Interrupting the agent or
   rejecting a tool call counts strongly, and so do phrases like "I said", "that's not what I",
   "you forgot" and "again".
3. **Group.** Flagged messages that say the same thing are grouped and ranked by how many
   sessions they appear in. A correction you made in two different sessions is one that didn't
   stick.
4. **Measure.** `twice label` draws a stratified sample, mostly flagged messages plus some
   unflagged ones. `twice score` reports precision, estimated recall, 95% Wilson intervals and
   a threshold sweep, plus how often Claude's judgment matches yours (accuracy and Cohen's
   kappa). The judge is measured, not trusted.

| Signal | What it catches | Weight |
|---|---|---|
| `denial` | you rejected a tool call | 2.0 |
| `interrupt` | you stopped the agent mid-action | 1.5 |
| `i_said` | "I said", "I told you", "like I said" | 1.2 |
| `starts_with_no` | "No, ...", "Wait, ...", "Actually, ..." | 1.0 |
| `not_what` | "that's not what I", "this is wrong" | 1.0 |
| `you_did` | "you forgot", "you deleted", "you still" | 1.0 |
| `dont_or_never` | "don't", "never", "stop" | 0.8 |
| `repeat` | "again", "every time", "still" | 0.8 |
| `why_did_you`, `revert` | "why did you", "undo", "put it back" | 0.8 |
| `rule_statement` | "always", "from now on", "next time" | 0.6 |
| `instead`, `but_not` | "instead", "but don't" | 0.5 |
| `approval`, `new_task` | "thanks", "great"; "now add", "can you" | -1.0, -0.3 |

A message is flagged at a score of 1.0 or more (`--threshold` changes this).

## Privacy

- Everything runs on your machine. Working files go to `~/.twice` (readable only by you), never
  into a repository.
- Text is redacted before it is stored: API keys, tokens, private keys, email addresses and
  `password=` style values.
- Only `twice judge` sends anything anywhere: each redacted message and a short note on what
  the agent had just done, to the Anthropic API, after you confirm. It uses `claude-opus-5-5` at
  low effort with server-side refusal fallbacks on. Change the model with `--model`.

## Roadmap

- [x] **v0.1:** mine Claude Code logs, flag corrections, group lessons, hand labels, measured
  precision and recall, a Claude judge validated against your labels
- [ ] **Replay:** re-run a correction moment in an isolated git worktree, with and without a
  rule, several times, and report how often the mistake comes back
- [ ] **Verdicts:** keep, promote to a hook, or prune every rule, and show the token cost of
  always-loaded rules
- [ ] **More agents:** a Codex adapter first
- [ ] **CI:** a GitHub Action that runs your suite on pull requests touching `CLAUDE.md`,
  `AGENTS.md` or skills
- [ ] **Model upgrades and skill bundles:** see which rules a new model no longer needs, and
  whether a third-party skill bundle reduces your mistakes
- [ ] **Lesson packs:** shareable rules that ship with their evidence

The full plan is in [docs/design.md](docs/design.md).

## Contributing

Good first contributions: an adapter for another agent, signal rules for another language,
and false positives or misses you found, written as synthetic test cases. See
[CONTRIBUTING.md](CONTRIBUTING.md). Never include real session data in an issue or pull
request.

## License

MIT. Built with [Claude Code](https://claude.com/claude-code).
