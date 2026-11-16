from pathlib import Path

from cw.project_memory import NOTES_HEADING, add_notes


def test_notes_create_the_section_and_skip_duplicates(tmp_path: Path) -> None:
    assert add_notes(tmp_path, []) == 0
    assert not (tmp_path / "CLAUDE.md").exists()
    assert add_notes(tmp_path, ["Run tests with `uv run pytest`.", "Run tests with `uv run pytest`."]) == 1
    assert add_notes(tmp_path, ["- run tests with `uv run pytest`.", "Settings live in src/config.py."]) == 2
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text == f"{NOTES_HEADING}\n- Run tests with `uv run pytest`.\n- Settings live in src/config.py.\n"


def test_notes_go_into_their_own_section_and_leave_the_rest_alone(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(f"# App\n\nIntro.\n\n{NOTES_HEADING}\n- Old note.\n\n## Commands\n\n- make\n")
    assert add_notes(tmp_path, ["New note.", "Intro."]) == 2
    expected = f"# App\n\nIntro.\n\n{NOTES_HEADING}\n- Old note.\n- New note.\n\n## Commands\n\n- make\n"
    assert (tmp_path / "CLAUDE.md").read_text() == expected


def test_a_claude_md_kept_in_the_claude_folder_is_used_instead_of_making_a_second_one(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("# Rules\n")
    assert add_notes(tmp_path, ["A note."]) == 1
    assert not (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".claude" / "CLAUDE.md").read_text() == f"# Rules\n\n{NOTES_HEADING}\n- A note.\n"
