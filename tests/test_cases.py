import json

from conftest import SessionWriter, make_repo

from twice.cases import Case, list_cases, load_case, save_case, scaffold, slugify
from twice.mine import mine
from twice.sources import claude_code


def _setup(tmp_path):
    repo = tmp_path / "webapp"
    old = make_repo(repo, {"README.md": "v1\n"}, date="2026-11-01T00:00:00Z")
    make_repo(repo, {"README.md": "v2\n"}, date="2026-11-20T00:00:00Z")  # after the session
    (repo / "packages" / "ui").mkdir(parents=True)
    root = tmp_path / "projects"
    s = SessionWriter("sess-x", str(repo / "packages" / "ui"))
    s.human("Write the welcome banner copy")
    s.assistant("Done. I used em dashes between the phrases.")
    s.human("No, never use em dashes in UI copy.")
    s.write(root)
    turns = mine([claude_code.load_session(p) for p in claude_code.discover(root)])
    return repo, old, root, turns[0]


def test_scaffold_fills_in_what_it_can(tmp_path):
    _repo, old, root, turn = _setup(tmp_path)
    case = scaffold(turn, root, "Never use em dashes in UI copy.", tmp_path / "work")
    assert case.prompt == "Write the welcome banner copy"
    assert case.commit == old  # the last commit before the prompt, not the later one
    assert case.subdir == "packages/ui"
    assert case.correction == "No, never use em dashes in UI copy."
    assert case.id == "never-use-em-dashes-in-ui"
    assert case.problems() == ["no checks yet: add at least one to 'checks'"]


def test_scaffold_without_rule_or_repo(tmp_path):
    root = tmp_path / "projects"
    s = SessionWriter("sess-y", str(tmp_path / "not-a-repo"))
    s.human("do the thing").assistant("did it").human("don't do it like that")
    s.write(root)
    turn = mine([claude_code.load_session(p) for p in claude_code.discover(root)])[0]
    case = scaffold(turn, root, "", tmp_path / "work")
    problems = case.problems()
    assert any(p.startswith("rule") for p in problems)
    assert any(p.startswith("repo") for p in problems)
    assert "not a git repository" in case.notes


def test_save_load_round_trip(tmp_path):
    case = Case(id="x", rule="r", prompt="p", repo=str(tmp_path), commit="abc",
                checks=[{"type": "diff_regex", "pattern": "a"}])
    path = save_case(tmp_path, case)
    data = json.loads(path.read_text())
    assert "_help" in data and "check_types" in data["_help"]
    loaded = load_case(tmp_path, "x")
    assert loaded == case and loaded.problems() == []
    assert [c.id for c in list_cases(tmp_path)] == ["x"]


def test_slugify():
    assert slugify("Never use em dashes in user-facing UI copy!") == "never-use-em-dashes-in-user"
    assert slugify("!!!") == "case"
