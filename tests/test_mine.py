from twice.mine import mine, turns_from_session
from twice.sources import claude_code


def _sessions(root):
    return [claude_code.load_session(p) for p in claude_code.discover(root)]


def _by_text(turns):
    return {t.text: t for t in turns}


def test_first_message_is_never_a_turn(demo_root):
    turns = mine(_sessions(demo_root))
    texts = {t.text for t in turns}
    assert "Add a login page with a short welcome message" not in texts
    assert "Write the release notes" not in texts


def test_corrections_flagged_and_others_not(demo_root):
    turns = _by_text(mine(_sessions(demo_root)))
    assert turns["No, I said never use em dashes in UI copy. Use commas."].is_candidate
    assert turns["Again with the em dashes. Never use em dashes, use commas instead."].is_candidate
    assert turns["Why did you delete the test? Don't delete tests, fix them."].is_candidate
    assert not turns["great, thanks"].is_candidate
    assert not turns["Can you also bump the version to 1.2.0?"].is_candidate


def test_denial_feedback_becomes_its_own_turn(demo_root):
    turns = _by_text(mine(_sessions(demo_root)))
    turn = turns["Don't touch the tests folder, only the component."]
    assert turn.is_candidate and "denial" in turn.signals
    assert "rejected a Edit call" in turn.context


def test_interrupt_counts_for_next_message(demo_root):
    turns = _by_text(mine(_sessions(demo_root)))
    turn = turns["Run the test suite before pushing anything."]
    assert "interrupt" in turn.signals and turn.is_candidate
    assert "Bash git push origin main" in turn.context


def test_context_describes_agent_activity(demo_root):
    turns = _by_text(mine(_sessions(demo_root)))
    ctx = turns["No, I said never use em dashes in UI copy. Use commas."].context
    assert "Write /work/webapp/src/login.tsx" in ctx
    assert "uses em dashes" in ctx


def test_secrets_are_redacted(demo_root):
    turns = mine(_sessions(demo_root))
    joined = " ".join(t.text for t in turns)
    assert "sk-ant-api03" not in joined
    assert "[REDACTED:anthropic_key]" in joined


def test_turn_ids_are_stable(demo_root):
    first = [t.id for t in mine(_sessions(demo_root))]
    second = [t.id for t in mine(_sessions(demo_root))]
    assert first == second and len(set(first)) == len(first)


def test_threshold_controls_flagging(demo_root):
    session = _sessions(demo_root)[0]
    strict = turns_from_session(session, threshold=99)
    assert not any(t.is_candidate for t in strict)
