from twice.cluster import build_lessons, group, tokenize
from twice.mine import mine
from twice.model import Turn
from twice.sources import claude_code


def _turn(i, text, session, candidate=True, project="/p/app"):
    return Turn(
        id=f"{session}:{i}", session_id=session, source="test", project=project, git_branch="main",
        timestamp=f"2026-11-{10 + i:02d}T00:00:00Z", text=text, context="", signals=[], score=2.0,
        is_candidate=candidate,
    )


def test_tokenize_drops_stopwords_and_stems():
    assert tokenize("Don't use the em dashes, please!") == ["don't", "em", "dash"]


def test_similar_corrections_group_together():
    groups = group([
        "never use em dashes in UI copy",
        "run the tests before pushing",
        "again with the em dashes, use commas",
        "always run the test suite before you push",
    ])
    as_sets = sorted(sorted(g) for g in groups)
    assert [0, 2] in as_sets and [1, 3] in as_sets


def test_lessons_rank_by_sessions(demo_root):
    sessions = [claude_code.load_session(p) for p in claude_code.discover(demo_root)]
    lessons = build_lessons(mine(sessions))
    top = lessons[0]
    assert top.sessions == 2
    assert "dash" in top.label
    assert all(l.sessions <= top.sessions for l in lessons)


def test_judgments_replace_text_and_filter():
    turns = [
        _turn(1, "no!! not those dashes", "s1"),
        _turn(2, "ugh, dashes again", "s2"),
        _turn(3, "add a button", "s3"),
    ]
    judgments = {
        "s1:1": {"is_correction": True, "lesson": "Never use em dashes in user-facing text."},
        "s2:2": {"is_correction": True, "lesson": "Do not use em dashes in user-facing text."},
        "s3:3": {"is_correction": False, "lesson": ""},
    }
    lessons = build_lessons(turns, judgments)
    assert len(lessons) == 1
    assert lessons[0].sessions == 2
    assert "em dashes" in lessons[0].label
    assert lessons[0].projects == ["app"]
