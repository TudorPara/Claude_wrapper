import subprocess
from pathlib import Path

from cw.models import ContextAnchor, Route, Ticket, TicketStatus, utc_now
from cw.prompts import _context_pack
from cw.repo_map import MAX_MAP_LINES, generate_repo_map, write_project_repo_map


def repository(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_repo_map_extracts_python_classes_and_functions(tmp_path: Path) -> None:
    repository(tmp_path)
    source_file = tmp_path / "service.py"
    source_file.write_text(
        '"""Order processing service."""\n\n'
        "class OrderManager:\n"
        "    pass\n\n"
        "def create_order(customer_id, amount):\n"
        "    return True\n"
    )
    result = generate_repo_map(tmp_path)
    assert "service.py: Order processing service." in result
    assert "class OrderManager" in result
    assert "create_order(customer_id, amount)" in result


def test_repo_map_ignores_hidden_and_venv_directories(tmp_path: Path) -> None:
    repository(tmp_path)
    (tmp_path / ".gitignore").write_text("generated/\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "out.py").write_text("def ignored_by_git(): pass")
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "ignored.py").write_text("def should_not_appear(): pass")

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("def run(): pass")

    result = generate_repo_map(tmp_path)
    assert "should_not_appear" not in result
    assert "ignored_by_git" not in result
    assert "main.py" in result


def test_repo_map_writes_to_target_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    repository(project_dir)
    (project_dir / "mod.py").write_text("def hello(): pass")

    output_path = tmp_path / "output" / "map.txt"
    content = write_project_repo_map(project_dir, output_path)
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == content


def test_a_large_repository_keeps_every_folder_by_using_less_detail(tmp_path: Path) -> None:
    repository(tmp_path)
    for folder in ("alpha", "zeta"):
        (tmp_path / folder).mkdir()
        for number in range(MAX_MAP_LINES // 4):
            (tmp_path / folder / f"m{number}.py").write_text("def a(): pass\ndef b(): pass\ndef c(): pass\n")
    result = generate_repo_map(tmp_path)
    assert len(result.splitlines()) <= MAX_MAP_LINES
    assert "[zeta]" in result and "m0.py: a(), b(), c()" in result


def test_context_pack_rendering() -> None:
    ticket = Ticket(
        id="test-1",
        project="demo",
        title="test",
        status=TicketStatus.IN_PROGRESS,
        route=Route.PLANNED,
        branch="cw/test-1",
        base_branch="main",
        previous_branch="main",
        base_sha="sha",
        owner="test",
        toolkit_version="1",
        created_at=utc_now(),
        updated_at=utc_now(),
        context_pack=[
            ContextAnchor(path="backend/src/cw/git.py", lines="109-128", reason="tree snapshot functions"),
            ContextAnchor(path="backend/src/cw/pipeline.py", lines="244-280", reason="worker loop"),
        ],
    )
    rendered = _context_pack(ticket)
    assert "## Key code locations to inspect (from the planner)" in rendered
    assert "`backend/src/cw/git.py:109-128` (tree snapshot functions)" in rendered
    assert "`backend/src/cw/pipeline.py:244-280` (worker loop)" in rendered
