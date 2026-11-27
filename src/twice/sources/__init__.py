"""Adapters that read one coding agent's session logs into `twice.model.Session`s.

Each adapter exposes `SOURCE` (a short name), `default_root()`, `discover(root)`
and `load_session(path)`. See `claude_code.py` for the reference adapter.
"""
