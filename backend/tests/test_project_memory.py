from pathlib import Path

from cw.project_memory import NOTES_HEADING, legacy_notes


def test_the_old_notes_section_is_read_and_the_file_left_alone(tmp_path: Path) -> None:
    text = f"# App\n\nIntro.\n\n{NOTES_HEADING}\n- Old note.\n- Second note.\n\n## Commands\n\n- make\n"
    (tmp_path / "CLAUDE.md").write_text(text)
    assert legacy_notes(tmp_path) == ["Old note.", "Second note."]
    assert (tmp_path / "CLAUDE.md").read_text() == text


def test_a_claude_md_in_the_claude_folder_is_read_and_a_missing_section_gives_nothing(tmp_path: Path) -> None:
    assert legacy_notes(tmp_path) == []
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text(f"# Rules\n\n{NOTES_HEADING}\n- A note.\n")
    assert legacy_notes(tmp_path) == ["A note."]
