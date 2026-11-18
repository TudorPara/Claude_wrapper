"""Write FRIDAY's test fixtures: real `cw ... --json` output for a made-up project with tickets in every state.

Run from the repository root: uv run --project backend python friday/Tests/make_fixtures.py
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "FridayTests" / "Fixtures"
PROJECT = "expense-tracker"


def _git(cwd: Path, *arguments: str) -> None:
    """Run git quietly."""
    subprocess.run(["git", *arguments], cwd=cwd, check=True, capture_output=True)


def _cw(*arguments: str) -> str:
    """Run the real cw command line and return its stdout."""
    command = [sys.executable, "-c", "from cw.cli import main; main()", *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def _setup(home: Path) -> Path:
    """Create a cw home with the repository's toolkit and one registered project."""
    os.environ["CW_HOME"] = str(home)
    for key, value in {"GIT_AUTHOR_NAME": "dev", "GIT_COMMITTER_NAME": "dev"}.items():
        os.environ[key] = value
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        os.environ[key] = "dev@example.test"
    config = json.loads(resources.files("cw").joinpath("config.example.json").read_text())
    config["toolkit"]["path"] = str(ROOT / "toolkit")
    (home / "config.json").write_text(json.dumps(config))
    repository = home / PROJECT
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    (repository / "pyproject.toml").write_text('[project]\nname = "expense-tracker"\nversion = "0"\n')
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "start")
    from cw import onboarding
    from cw.config import GitHostProvider

    onboarding.add_project(repository, PROJECT, GitHostProvider.GITHUB, onboard=False)
    return repository


def _tickets() -> list[str]:
    """Write tickets in the states FRIDAY shows, with runs, work, reviews and events; return their ids."""
    from cw.models import (
        PromptEntry,
        QuestionEntry,
        ReviewComment,
        ReviewEntry,
        Route,
        RunUsage,
        Severity,
        Subtask,
        Ticket,
        TicketStatus,
        Verdict,
        WorkEntry,
        WorkerStatus,
        utc_now,
    )
    from cw.tickets import append_event, write_ticket

    now = utc_now()

    def run(role: str, label: str, model: str, session: str, cost: float, turns: int, minutes_ago: int) -> RunUsage:
        return RunUsage(
            at=now - timedelta(minutes=minutes_ago), role=role, agent=role, model=model, session_id=session,
            input_tokens=1800 * turns, output_tokens=420 * turns, cache_read_tokens=21000 * turns,
            cache_creation_tokens=3000, cost_usd=cost, duration_ms=turns * 9000, num_turns=turns, label=label,
        )  # fmt: skip

    def ticket(identifier: str, title: str, status: TicketStatus, **fields) -> Ticket:
        return Ticket(
            id=identifier, project=PROJECT, title=title, status=status, route=Route.PLANNED,
            branch=f"cw/{identifier}", base_branch="origin/main", target_branch="main", previous_branch="main",
            base_sha="0" * 40, owner="dev", toolkit_version="0.4.0", created_at=now - timedelta(hours=2),
            updated_at=now, prompts=[PromptEntry(at=now - timedelta(hours=2), text=title)], **fields,
        )  # fmt: skip

    tasks = [
        Subtask(id="S1", title="Add the import table and repository", worker="W1",
                instructions="Create the imports table.\nDone when: tests/test_imports.py passes.",
                files=["src/expenses/models/imports.py", "src/expenses/repositories/imports.py"]),
        Subtask(id="S2", title="Parse CSV rows into expenses", worker="W1",
                instructions="Add the parser.\nDone when: tests/test_parser.py passes.",
                files=["src/expenses/services/csv_parser.py", "tests/test_parser.py"]),
        Subtask(id="S3", title="Add the upload endpoint", worker="W2",
                instructions="POST /imports.\nDone when: tests/test_import_route.py passes.",
                files=["src/expenses/api/routes/imports.py", "tests/test_import_route.py"]),
        Subtask(id="S4", title="Document the import in the README", worker="W2",
                instructions="Done when: lint passes.", files=["README.md"]),
    ]  # fmt: skip
    work = [
        WorkEntry(at=now - timedelta(minutes=50), subtask="S1", status=WorkerStatus.DONE, worker="W1",
                  summary="Added the imports table with a unique file hash.",
                  files=["src/expenses/models/imports.py", "src/expenses/repositories/imports.py"],
                  handoff="ImportRepository.create(file_hash, user_id) returns the Import."),
        WorkEntry(at=now - timedelta(minutes=30), subtask="S2", status=WorkerStatus.DONE, worker="W1",
                  summary="Parser reads date, amount and category; bad rows are reported, not skipped.",
                  files=["src/expenses/services/csv_parser.py", "tests/test_parser.py"],
                  handoff="parse_rows(text) -> ParsedImport(rows, errors)."),
    ]  # fmt: skip
    running = ticket(
        "261118-csv-import-a1b2", "Import expenses from a CSV file", TicketStatus.IN_PROGRESS,
        plan="- A table for imports\n- A parser\n- An upload endpoint", subtasks=tasks, tasks_done=["S1", "S2"],
        work_log=work, last_worker="W2", planner_session="p1", worker_sessions={"W1": "w1", "W2": "w2"},
        qa=[QuestionEntry(at=now - timedelta(minutes=40), worker="W1", task="S2",
                          question="Should a row with a missing category fail the import?",
                          answer="No: report it and keep the other rows.", answered_by="planner", delivered=True)],
        runs=[run("planner", "planner", "opus", "p1", 0.84, 9, 60), run("worker", "W1 · S1", "sonnet", "w1", 0.31, 14, 50),
              run("planner", "planner", "opus", "p1", 0.97, 2, 40), run("worker", "W1 · S2", "sonnet", "w1", 0.58, 12, 30)],
    )  # fmt: skip
    write_ticket(running)
    for minutes, event in [
        (61, {"kind": "start", "agent": "planner", "role": "planner", "label": "planner", "model": "opus"}),
        (52, {"kind": "done", "agent": "planner", "role": "planner", "label": "planner", "turns": 9}),
        (51, {"kind": "start", "agent": "worker", "role": "worker", "label": "W1 · S1", "model": "sonnet"}),
        (50, {"kind": "done", "agent": "worker", "role": "worker", "label": "W1 · S1", "turns": 14}),
        (49, {"kind": "check", "command": "uv run ruff check .", "passed": True}),
        (31, {"kind": "start", "agent": "worker", "role": "worker", "label": "W1 · S2", "model": "sonnet"}),
        (30, {"kind": "done", "agent": "worker", "role": "worker", "label": "W1 · S2", "turns": 12}),
        (29, {"kind": "check", "command": "uv run pytest", "passed": True}),
        (3, {"kind": "start", "agent": "worker", "role": "worker", "label": "W2 · S3", "model": "sonnet"}),
        (2, {"kind": "tool", "agent": "worker", "role": "worker", "label": "W2 · S3", "tool": "Read",
             "detail": "src/expenses/api/routes/expenses.py"}),
        (1, {"kind": "tool", "agent": "worker", "role": "worker", "label": "W2 · S3", "tool": "Write",
             "detail": "src/expenses/api/routes/imports.py"}),
        (0, {"kind": "tool", "agent": "worker", "role": "worker", "label": "W2 · S3", "tool": "Bash",
             "detail": "uv run pytest tests/test_import_route.py"}),
    ]:  # fmt: skip
        append_event(PROJECT, running.id, event)

    blocking = ReviewComment(path="src/expenses/services/reports.py", line=42, severity=Severity.BLOCKING,
                             body="Month boundaries use local time; the rules ask for UTC timestamptz.", id="F3")
    minor = ReviewComment(path="src/expenses/api/routes/reports.py", line=18, severity=Severity.MINOR,
                          body="Name the query helper after what it returns.", id="F4")
    fixed = ReviewComment(path="tests/test_reports.py", line=1, severity=Severity.BLOCKING,
                          body="No test for a month without expenses.", id="F1")
    review_ticket = ticket(
        "261118-monthly-report-c3d4", "Monthly report endpoint", TicketStatus.PR_OPEN,
        plan="- A report service\n- GET /reports/{month}", subtasks=tasks[:2], tasks_done=["S1", "S2"], work_log=work,
        reviews=[ReviewEntry(at=now - timedelta(minutes=20), round=1, reviewer="reviewer", verdict=Verdict.CHANGES,
                             summary="One missing test.", comments=[fixed]),
                 ReviewEntry(at=now - timedelta(minutes=10), round=2, reviewer="reviewer", verdict=Verdict.CHANGES,
                             summary="The test is there; the month boundary is still local time.",
                             comments=[blocking, minor])],
        open_findings=[blocking], pr_url="https://github.com/example/expense-tracker/pull/4", pr_number=4,
        pushed_at=now - timedelta(minutes=9), posted_review_round=2, commit_message="feat: monthly report",
        warnings=["Review: 1 blocking findings left after the fix round."],
        runs=[run("reviewer", "reviewer", "opus", "r1", 0.66, 11, 20), run("reviewer", "reviewer", "opus", "r2", 0.41, 7, 10)],
    )  # fmt: skip
    write_ticket(review_ticket)
    approval = ticket(
        "261118-budget-alerts-e5f6", "Warn me when a budget is almost spent", TicketStatus.AWAITING_APPROVAL,
        plan="- A budgets table\n- A daily check\n- An email", subtasks=tasks[:3], planner_session="p9",
        options_considered="A webhook instead of email: no receiver exists yet.",
        runs=[run("planner", "planner", "opus", "p9", 0.72, 10, 5)],
    )  # fmt: skip
    write_ticket(approval)
    question = ticket(
        "261118-currency-g7h8", "Support more currencies", TicketStatus.NEEDS_INPUT,
        questions=["Which currencies, and where do the exchange rates come from?"], question_rounds=1,
        runs=[run("planner", "planner", "opus", "p8", 0.35, 4, 8)],
    )  # fmt: skip
    write_ticket(question)
    blocked = ticket(
        "261118-cookie-auth-i9j0", "Cookie sessions for the web app", TicketStatus.BLOCKED,
        paused_step="work", error="Task S1: `uv run pytest` still fails after the fix round.",
        failing_checks=["uv run pytest"], subtasks=tasks[:1], last_worker="W1",
    )  # fmt: skip
    write_ticket(blocked)
    done = ticket("261117-categories-k1l2", "Expense categories", TicketStatus.DONE)
    write_ticket(done)
    return [running.id, review_ticket.id, approval.id, question.id, blocked.id, done.id]


def main() -> None:
    """Write every fixture FRIDAY's tests and snapshots read."""
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    with tempfile.TemporaryDirectory() as folder:
        _setup(Path(folder))
        ids = _tickets()
        FIXTURES.mkdir(parents=True, exist_ok=True)
        outputs = {
            "config.json": _cw("config", "--json"),
            "projects.json": _cw("project", "list"),
            "status.json": _cw("status", "--json", "--include-archived"),
            "stats.json": _cw("stats", "--json"),
            "toolkit.json": _cw("toolkit", "--json"),
            "doctor.json": _cw("doctor", "--json"),
        }
        for ticket_id in ids:
            outputs[f"show-{ticket_id}.json"] = _cw("show", ticket_id, "--json")
            outputs[f"events-{ticket_id}.json"] = _cw("events", ticket_id, "--json")
        local_paths = {str(Path(folder).resolve()): "/Users/dev", folder: "/Users/dev", str(ROOT): "/Users/dev/cw"}
        for name, text in outputs.items():
            for local, shown in local_paths.items():
                text = text.replace(local, shown)
            (FIXTURES / name).write_text(text)


if __name__ == "__main__":
    main()
