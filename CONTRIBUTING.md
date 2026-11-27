# Contributing

Thanks for helping. A few ground rules first.

## Never commit real session data

Session logs contain secrets, private code and other people's names. Tests build synthetic
sessions with the helpers in `tests/conftest.py`. If you found a false positive or a miss in
your own logs, rewrite it as a synthetic example before opening an issue or pull request.

## Setup

```bash
git clone https://github.com/ShubhamGajjar/twice && cd twice
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,llm]"
pytest -q
ruff check src tests   # if you have ruff installed
```

## Good first contributions

- **Signal rules.** Add or tune a rule in `src/twice/signals.py`, with test cases in
  `tests/test_signals.py` for messages it should and shouldn't flag. Rules for other languages
  are welcome; give them a language suffix (for example `i_said_es`).
- **A new agent.** Add an adapter in `src/twice/sources/` exposing `SOURCE`, `default_root()`,
  `discover(root)` and `load_session(path)` that returns a `Session` of `Event`s (see
  `claude_code.py`). Add a synthetic log builder and tests in the same style.
- **Redaction.** New secret formats for `src/twice/redact.py`, each with a test.

## Pull requests

- Keep changes focused and include tests.
- Explain any change to scoring weights with evidence: labeled examples or `twice score`
  output (numbers only, no quotes from real sessions).
