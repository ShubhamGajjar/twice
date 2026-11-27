"""Optional: ask Claude whether each flagged message is a correction, and to state the lesson.

Uses the Anthropic Python SDK (`pip install 'twice[llm]'`). Credentials come
from ANTHROPIC_API_KEY or an `ant auth login` profile. Only redacted text is
sent: your message and the short note on what the agent had just done.

The judge is itself measured: `twice score` compares its answers with your
hand labels, so you know how far to trust the lessons it writes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from twice.model import Turn
from twice.store import append_jsonl

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"  # a yes/no call plus one sentence; low effort holds quality here
CATEGORIES = ("correctness", "scope", "style", "workflow", "tools", "communication", "other")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You review moments from a developer's sessions with an AI coding agent. Each moment shows what the agent had just done and the developer's next message.

Decide whether the message corrects the agent: it points out a mistake, rejects an action or an output, repeats an instruction the agent ignored, or tells the agent to do something differently from what it just did. New requests, follow-up questions, approvals and added context are not corrections, even when they mention bugs in the code rather than problems with the agent's behavior.

If it is a correction, write the lesson as one short imperative rule the agent could follow in future sessions, general enough to apply beyond this moment (for example: "Run the test suite before saying a task is done."). Leave out names, secrets and file contents unless the rule cannot be stated without them. If it is not a correction, return an empty lesson and the category "other"."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_correction": {"type": "boolean"},
        "lesson": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
    },
    "required": ["is_correction", "lesson", "category"],
    "additionalProperties": False,
}


class LLMUnavailable(RuntimeError):
    pass


def render(turn: Turn) -> str:
    return (
        f"<agent_activity>\n{turn.context or '(no details recorded)'}\n</agent_activity>\n\n"
        f"<developer_message>\n{turn.text[:4000]}\n</developer_message>"
    )


class Judge:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMUnavailable("The judge needs the Anthropic SDK: pip install 'twice[llm]'") from exc
            client = anthropic.Anthropic(max_retries=5)
        self.client = client
        self.model = model
        self.effort = effort

    def judge(self, turn: Turn) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4096,
            betas=[FALLBACK_BETA],
            fallbacks="default",  # if the model declines, the API retries on a fallback model
            system=SYSTEM_PROMPT,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            messages=[{"role": "user", "content": render(turn)}],
        )
        base = {
            "id": turn.id,
            "model": getattr(response, "model", self.model),
            "judged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if response.stop_reason == "refusal":
            return {**base, "status": "refused"}
        if response.stop_reason == "max_tokens":
            return {**base, "status": "truncated"}
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            return {**base, "status": "empty"}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {**base, "status": "bad_json"}
        category = data.get("category")
        return {
            **base,
            "status": "ok",
            "is_correction": bool(data.get("is_correction")),
            "lesson": str(data.get("lesson") or "").strip(),
            "category": category if category in CATEGORIES else "other",
        }


def run_judgments(
    turns: list[Turn],
    judge: Judge,
    path: Path,
    say: Callable[[str], None] = print,
) -> tuple[int, str | None]:
    """Judge each turn, appending results as it goes so a stopped run loses nothing.

    Returns (how many were judged, a message if the run stopped early).
    """
    try:
        import anthropic
    except ImportError:
        anthropic = None
    done = 0
    for turn in turns:
        try:
            result = judge.judge(turn)
        except Exception as exc:  # narrowed below; anything unrecognized is re-raised
            message = _describe_error(exc, anthropic)
            if message is None:
                raise
            return done, message
        append_jsonl(path, result)
        done += 1
        if done % 10 == 0:
            say(f"  judged {done}/{len(turns)}")
    return done, None


def _describe_error(exc: Exception, anthropic) -> str | None:
    if anthropic is None:
        return None
    keep = "Finished judgments were saved; rerun to continue."
    if isinstance(exc, anthropic.AuthenticationError):
        return f"Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`. {keep}"
    if isinstance(exc, anthropic.NotFoundError):
        return f"Model not found ({exc.message}). Check --model. {keep}"
    if isinstance(exc, anthropic.RateLimitError):
        return f"Rate limited even after retries. {keep}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"API error {exc.status_code}: {exc.message}. {keep}"
    if isinstance(exc, anthropic.APIConnectionError):
        return f"Could not reach the API. {keep}"
    return None
