"""Score a human message for how likely it is to be a correction of the agent.

Every rule is a plain regex with a weight, so anyone can read exactly why a
message was flagged, and contributors can add rules (or rules for other
languages) without touching the scoring code. The weights are a starting
point, not a claim: `twice label` and `twice score` exist to measure how well
they work on your own sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from twice.model import DENIAL, INTERRUPT

DEFAULT_THRESHOLD = 1.0
LONG_MESSAGE_CHARS = 1200  # long prompts are usually new tasks or pasted material


@dataclass(frozen=True)
class Rule:
    name: str
    weight: float
    pattern: re.Pattern


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# English rules. Add a language by appending rules with a language suffix.
POSITIVE_RULES: tuple[Rule, ...] = (
    Rule("starts_with_no", 1.0, _rx(r"^\s*(no|nope|nah|stop|wait|hold on|wrong|incorrect|undo|revert|actually)\b[\s,.!:;-]")),
    Rule("i_said", 1.2, _rx(r"\b(i (already |just )?(said|told you|asked( you)?|mentioned|meant)|as i said|like i said)\b")),
    Rule("not_what", 1.0, _rx(r"\b(not what i|that'?s not|this is not|this isn'?t|not like that|not right|(is|are|was|looks) wrong)\b")),
    Rule("you_did", 1.0, _rx(r"\byou (forgot|missed|ignored|didn'?t|did not|broke|removed|deleted|changed|added|keep|still|used)\b")),
    Rule("dont_or_never", 0.8, _rx(r"\b(don'?t|do not|never|shouldn'?t|should not|must not|mustn'?t|stop)\b")),
    Rule("repeat", 0.8, _rx(r"\b(again|once more|keep (doing|using|adding|making|writing)|every time|each time|still)\b")),
    Rule("why_did_you", 0.8, _rx(r"\bwhy (did|do|are|would|is) (you|it|this)\b")),
    Rule("revert", 0.8, _rx(r"\b(revert|undo|roll ?back|put (it|that) back|change (it|that) back)\b")),
    Rule("rule_statement", 0.6, _rx(r"\b(always|from now on|going forward|in (the )?future|next time|remember (to|that))\b")),
    Rule("instead", 0.5, _rx(r"\b(instead|rather than)\b")),
    Rule("but_not", 0.5, _rx(r"\b(but|however)\b[^.?!]{0,40}?\b(don'?t|do not|never|not|without)\b")),
)

APPROVAL = Rule(
    "approval",
    -1.0,
    _rx(r"^\s*(yes|yep|yeah|ok(ay)?|great|perfect|thanks|thank you|nice|cool|lgtm|looks good|good|awesome|go ahead|proceed|sure|continue)\b"),
)
NEW_TASK = Rule(
    "new_task",
    -0.3,
    _rx(r"^\s*(now|next|also|let'?s|can you|could you|please (create|build|add|write|make|implement)|create|build|implement|write|add|make)\b"),
)

EVENT_WEIGHTS = {INTERRUPT: 1.5, DENIAL: 2.0}


def score_text(text: str, preceding: set[str] | frozenset[str] = frozenset()) -> tuple[float, list[str]]:
    """Return (score, names of the signals that fired) for one human message.

    `preceding` holds the event kinds seen since the agent's last turn began,
    so an interrupt or a rejected tool call right before the message counts.
    """
    score = 0.0
    fired: list[str] = []
    for kind, weight in EVENT_WEIGHTS.items():
        if kind in preceding:
            score += weight
            fired.append(kind)
    text_hits = [rule for rule in POSITIVE_RULES if rule.pattern.search(text)]
    for rule in text_hits:
        score += rule.weight
        fired.append(rule.name)
    # An approval only counts against the message when nothing else pushes back,
    # so "ok but don't touch the tests" still scores as a correction.
    if not text_hits and APPROVAL.pattern.search(text):
        score += APPROVAL.weight
        fired.append(APPROVAL.name)
    if NEW_TASK.pattern.search(text):
        score += NEW_TASK.weight
        fired.append(NEW_TASK.name)
    if len(text) > LONG_MESSAGE_CHARS and score > 0:
        score *= 0.5
        fired.append("long_message")
    return round(score, 3), fired
