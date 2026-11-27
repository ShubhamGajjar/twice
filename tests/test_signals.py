import pytest

from twice.model import DENIAL, INTERRUPT
from twice.signals import DEFAULT_THRESHOLD, score_text

CORRECTIONS = [
    "No, I said never use em dashes in UI copy.",
    "That's not what I asked for, revert it.",
    "You forgot to run the tests again.",
    "why did you delete the migration? put it back",
    "stop adding comments to every line",
    "I told you to use pnpm, not npm",
    "ok but don't touch the config file",
]
NOT_CORRECTIONS = [
    "great, thanks",
    "Now add a logout button",
    "Can you write the release notes?",
    "What does this function return?",
    "looks good, ship it",
]


@pytest.mark.parametrize("text", CORRECTIONS)
def test_corrections_cross_threshold(text):
    score, signals = score_text(text)
    assert score >= DEFAULT_THRESHOLD, (text, score, signals)


@pytest.mark.parametrize("text", NOT_CORRECTIONS)
def test_non_corrections_stay_below(text):
    score, signals = score_text(text)
    assert score < DEFAULT_THRESHOLD, (text, score, signals)


def test_interrupt_and_denial_add_weight():
    base, _ = score_text("run the tests before pushing")
    after_interrupt, signals = score_text("run the tests before pushing", {INTERRUPT})
    assert after_interrupt > base and INTERRUPT in signals
    after_denial, signals = score_text("use the other file", {DENIAL})
    assert after_denial >= DEFAULT_THRESHOLD and DENIAL in signals


def test_approval_ignored_when_message_pushes_back():
    _, signals = score_text("ok but don't touch the config file")
    assert "approval" not in signals
    _, signals = score_text("ok thanks")
    assert "approval" in signals


def test_long_messages_are_discounted():
    short, _ = score_text("don't do that again")
    long, signals = score_text("don't do that again " + "context " * 300)
    assert long < short and "long_message" in signals
