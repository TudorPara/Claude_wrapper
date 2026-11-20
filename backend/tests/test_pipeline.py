import json
import os
from pathlib import Path

import pytest
from conftest import FakeAgents, FakeHost, answer, git, make_repository, usage

from cw import onboarding, pipeline, prompts, reports
from cw.config import AgentRole, get_settings, write_settings
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequestThread
from cw.models import (
    CommentOutcome,
    CommentOutcomeKind,
    PlannerResult,
    ReviewComment,
    ReviewerResult,
    Severity,
    Step,
    Subtask,
    TicketStatus,
    WorkerResult,
    WorkerStatus,
)
from cw.tickets import events_path, find_ticket, ticket_path, write_ticket

WORKER = AgentRole.WORKER


def test_direct_ticket_gives_the_worker_the_ticket_its_checks_and_ends_ready(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    assert ticket.status == TicketStatus.READY
    prompt = agents.prompts_for(WORKER)[0]
    assert prompt.startswith("# add a file\n\n## Request")
    assert "`test ! -f BROKEN` (passes on the base branch: keep it passing)" in prompt
    assert "## Off limits" in prompt and "## Do now\n\nadd a file" in prompt
    assert git(local_repository, "log", "--oneline").count("\n") == 0


def test_the_ticket_file_records_what_each_task_left_for_the_next_worker(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    pipeline.approve(ticket.id)
    markdown = ticket_path("demo", ticket.id).with_suffix(".md").read_text()
    assert "### [x] S1 · task 1 (worker W1)" in markdown
    assert "- Solved by W1: pass 1\n- Files: work_1.txt\n- For the next worker: work_1.txt holds the result" in markdown


def test_related_tasks_reuse_one_worker_session_and_another_worker_is_told_what_happened(
    local_repository: Path, agents: FakeAgents
) -> None:
    agents.task_workers = ["W1", "W1", "W2", "W1"]
    ticket = pipeline.start_ticket("demo", "four steps", plan_first=True)
    done = pipeline.approve(ticket.id)
    assert done.tasks_done == ["S1", "S2", "S3", "S4"]
    sessions = [session for role, session in agents.resumed if role == WORKER]
    assert sessions[0] is None and sessions[1] == done.worker_sessions["W1"]
    assert sessions[2] is None and sessions[3] == done.worker_sessions["W1"]
    first, second, third, fourth = agents.prompts_for(WORKER)
    assert "## Request" in first and "## Request" not in second
    assert second.startswith("## Your task: S2")
    assert "## Request" in third and "Solved by W1: pass 2" in third
    assert fourth.startswith("## What other workers did since your last turn\n\n- Solved by W2: pass 3")


def test_more_workers_than_the_limit_share_the_last_one(local_repository: Path, agents: FakeAgents) -> None:
    agents.task_workers = ["api", "ui", "docs", "ops", "extra", "more"]
    ticket = pipeline.start_ticket("demo", "many", plan_first=True)
    assert [task.worker for task in ticket.subtasks] == ["W1", "W2", "W3", "W4", "W4", "W4"]


def test_a_workers_question_is_answered_by_the_planner_and_work_continues(
    local_repository: Path, agents: FakeAgents
) -> None:
    def asks_once(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            return answer(WorkerStatus.QUESTION, questions=["Which timeout?"])
        return agents.default_worker(prompt, cwd, call)

    agents.worker = asks_once
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    done = pipeline.approve(ticket.id)
    assert done.status == TicketStatus.READY
    assert (done.qa[0].answer, done.qa[0].answered_by, done.qa[0].delivered) == (
        "use the settings value",
        "planner",
        True,
    )
    assert "## Questions from worker W1 on task S1\n\n1. Which timeout?" in agents.prompts_for(AgentRole.PLANNER)[1]
    answered = agents.prompts_for(WORKER)[1]
    assert answered.startswith(
        "## Answers to your questions\n\n- Which timeout?\n  The planner answered: use the settings"
    )
    assert "## Your task: S1" in answered


def test_a_question_the_planner_cannot_answer_goes_to_the_user(local_repository: Path, agents: FakeAgents) -> None:
    def asks_once(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            return answer(WorkerStatus.QUESTION, questions=["Which currency?"])
        return agents.default_worker(prompt, cwd, call)

    agents.worker = asks_once
    agents.planner_answers = [""]
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    waiting = pipeline.approve(ticket.id)
    assert (waiting.status, waiting.questions, waiting.tasks_done) == (
        TicketStatus.NEEDS_INPUT,
        ["Which currency?"],
        [],
    )

    done = pipeline.follow_up(ticket.id, "euro")
    assert (done.status, done.tasks_done) == (TicketStatus.READY, ["S1", "S2"])
    assert "The user answered: euro" in agents.prompts_for(WORKER)[1]
    assert done.prompts[-1].is_answer and len(agents.prompts_for(AgentRole.PLANNER)) == 2


def test_a_direct_tickets_question_goes_straight_to_the_user_and_the_answer_is_not_a_new_request(
    local_repository: Path, agents: FakeAgents
) -> None:
    def asks_once(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            return answer(WorkerStatus.QUESTION, questions=["Blue or green?"])
        return agents.default_worker(prompt, cwd, call)

    agents.worker = asks_once
    ticket = pipeline.start_ticket("demo", "colour the button", plan_first=False)
    assert ticket.status == TicketStatus.NEEDS_INPUT
    done = pipeline.follow_up(ticket.id, "blue")
    assert done.status == TicketStatus.READY
    answered = agents.prompts_for(WORKER)[1]
    assert "The user answered: blue" in answered and "## Do now\n\ncolour the button" in answered
    assert agents.prompts_for(AgentRole.PLANNER) == []


def test_a_follow_up_continues_in_the_same_worker_session(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    again = pipeline.follow_up(ticket.id, "rename it")
    assert agents.resumed[1] == (WORKER, ticket.worker_sessions["W1"])
    assert agents.prompts_for(WORKER)[1] == "## Do now\n\nrename it"
    assert again.status == TicketStatus.READY


def test_a_session_that_is_gone_is_replaced_by_a_fresh_one_with_the_whole_ticket(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)

    def lost_session(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if agents.resumed[-1][1] is not None:
            raise AppException(AppErrorCode.SESSION_LOST, "No conversation found")
        return agents.default_worker(prompt, cwd, call)

    agents.worker = lost_session
    again = pipeline.follow_up(ticket.id, "rename it")
    assert again.status == TicketStatus.READY
    assert agents.prompts_for(WORKER)[-1].startswith("# add a file\n\n## Request")


def test_a_resumed_run_that_fails_keeps_its_session_and_starts_no_fresh_run(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    session = ticket.worker_sessions["W1"]

    def crash(prompt: str, cwd: Path, call: int) -> WorkerResult:
        raise AppException(AppErrorCode.AGENT_FAILED, "printed no result")

    agents.worker = crash
    with pytest.raises(AppException, match="printed no result"):
        pipeline.follow_up(ticket.id, "rename it")
    stored = find_ticket(ticket.id)
    assert len(agents.prompts_for(WORKER)) == 2
    assert stored.status == TicketStatus.BLOCKED and stored.worker_sessions["W1"] == session


def test_a_broken_check_gets_one_fix_round_then_is_reported(local_repository: Path, agents: FakeAgents) -> None:
    def breaks_lint(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / "BROKEN").write_text("x")
        return answer(WorkerStatus.DONE, "broke it", files=["BROKEN"])

    agents.worker = breaks_lint
    ticket = pipeline.start_ticket("demo", "break it", plan_first=False)
    assert ticket.status == TicketStatus.READY
    assert ticket.failing_checks == ["test ! -f BROKEN"]
    assert "Checks: now failing `test ! -f BROKEN`" in ticket.warnings
    fix_prompt = agents.prompts_for(WORKER)[1]
    assert fix_prompt.startswith("## Fix the failing checks") and "Do not weaken, skip or delete" in fix_prompt


def test_an_interrupted_task_list_is_blocked_and_resumes_where_it_stopped(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    assert ticket.status == TicketStatus.AWAITING_APPROVAL

    def stopped_on_second(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 2:
            raise KeyboardInterrupt
        return agents.default_worker(prompt, cwd, call)

    agents.worker = stopped_on_second
    with pytest.raises(KeyboardInterrupt):
        pipeline.approve(ticket.id)
    stopped = find_ticket(ticket.id)
    assert (stopped.status, stopped.paused_step, stopped.tasks_done) == (TicketStatus.BLOCKED, Step.WORK, ["S1"])

    agents.worker = agents.default_worker
    resumed = pipeline.resume(ticket.id)
    assert (resumed.status, resumed.tasks_done) == (TicketStatus.READY, ["S1", "S2"])
    assert "## Your task: S2" in agents.prompts_for(WORKER)[-1]


def test_a_ticket_left_in_progress_by_a_killed_process_can_be_resumed(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    ticket.status = TicketStatus.IN_PROGRESS
    write_ticket(ticket)
    assert pipeline.resume(ticket.id).tasks_done == ["S1", "S2"]


def test_a_follow_up_on_a_blocked_plan_still_runs_the_remaining_tasks(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)

    def blocked_on_second(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 2:
            return answer(WorkerStatus.BLOCKED, reason="needs a key")
        return agents.default_worker(prompt, cwd, call)

    agents.worker = blocked_on_second
    with pytest.raises(AppException):
        pipeline.approve(ticket.id)
    agents.worker = agents.default_worker
    done = pipeline.follow_up(ticket.id, "the key is in settings")
    assert (done.status, done.tasks_done) == (TicketStatus.READY, ["S1", "S2"])
    assert "The unchecked tasks in the task list run after you" in agents.prompts_for(WORKER)[2]


def test_the_tokens_of_a_failed_run_are_kept(local_repository: Path, agents: FakeAgents) -> None:
    def fails(prompt: str, cwd: Path, call: int) -> WorkerResult:
        raise AppException(AppErrorCode.AGENT_FAILED, "error_max_turns", usage(WORKER))

    agents.worker = fails
    with pytest.raises(AppException):
        pipeline.start_ticket("demo", "x", plan_first=False)
    ticket = find_ticket(pipeline.list_tickets()[0].id)
    assert ticket.status == TicketStatus.BLOCKED and len(ticket.runs) == 1


def test_a_pull_request_without_changes_is_refused_and_the_ticket_stays_ready(
    local_repository: Path, agents: FakeAgents
) -> None:
    agents.worker = lambda prompt, cwd, call: answer(WorkerStatus.DONE, "nothing to do")
    ticket = pipeline.start_ticket("demo", "nothing", plan_first=False)
    with pytest.raises(AppException) as raised:
        pipeline.open_pull_request(ticket.id)
    assert raised.value.error_code == AppErrorCode.INVALID_STATE
    assert find_ticket(ticket.id).status == TicketStatus.READY


def test_a_review_alone_commits_nothing(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    reviewed = pipeline.review(ticket.id)
    assert reviewed.status == TicketStatus.READY and len(reviewed.reviews) == 1
    assert git(local_repository, "rev-list", "--count", f"main..{ticket.branch}") == "0"
    assert agents.review_files == [["work_1.txt"]]


def test_a_project_without_a_remote_is_reviewed_committed_and_merged_by_done(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    opened = pipeline.open_pull_request(ticket.id)
    assert opened.status == TicketStatus.PR_OPEN and opened.pr_url is None
    assert any(warning.startswith("Local:") for warning in opened.warnings)
    assert git(local_repository, "rev-list", "--count", f"main..{ticket.branch}") == "1"
    reviewer_prompt = agents.prompts_for(AgentRole.REVIEWER)[0]
    assert "- Solved by W1: pass 1" in reviewer_prompt and "- lint `test ! -f BROKEN`: passes" in reviewer_prompt

    pipeline.finish(ticket.id, abandon=False)
    assert git(local_repository, "branch", "--show-current") == "main"
    assert (local_repository / "work_1.txt").exists()
    assert find_ticket(ticket.id).status == TicketStatus.DONE


def test_the_pull_request_is_opened_after_the_review_even_when_findings_are_left(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    nit = ReviewComment(path="work_1.txt", line=2, severity=Severity.MINOR, body="rename it")
    invented = ReviewComment(path="nowhere.py", line=1, severity=Severity.BLOCKING, body="made up")
    agents.reviewer = lambda prompt: ReviewerResult(
        summary="no", comments=[] if "still open" in prompt else [finding, nit, invented]
    )
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    opened = pipeline.open_pull_request(ticket.id)
    assert opened.status == TicketStatus.PR_OPEN and opened.pr_number == 1
    assert "Review: 1 blocking findings left after the fix round." in opened.warnings
    assert len(agents.prompts_for(AgentRole.REVIEWER)) == 2 and host.reviews == 1
    assert "- F1 `work_1.txt:1` wrong" in agents.prompts_for(AgentRole.REVIEWER)[1]
    assert [(c.path, c.line) for c in opened.reviews[0].comments] == [("work_1.txt", 1), ("work_1.txt", 1)]
    fix_prompt = agents.prompts_for(WORKER)[1]
    assert fix_prompt.startswith("## Address these review findings, and only these")
    assert "(blocking) wrong" in fix_prompt and "(minor) rename it" in fix_prompt


def test_a_ticket_can_start_from_another_branch_and_its_pull_request_goes_back_there(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    git(remote_repository, "switch", "-c", "develop")
    (remote_repository / "only_on_develop.txt").write_text("x\n")
    git(remote_repository, "add", "-A")
    git(remote_repository, "commit", "-m", "develop work")
    git(remote_repository, "push", "-u", "origin", "develop")
    git(remote_repository, "switch", "main")

    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False, base="develop")
    assert (remote_repository / "only_on_develop.txt").exists()
    pipeline.open_pull_request(ticket.id)
    assert host.targets == ["develop"]


def test_conflicts_with_the_base_are_resolved_and_later_reviews_skip_the_base_changes(
    remote_repository: Path, workspace: Path, agents: FakeAgents, host: FakeHost
) -> None:
    def edits_f(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if "Resolve merge conflicts" not in prompt and call == 1:
            (cwd / "f.txt").write_text("ticket\n")
            return answer(WorkerStatus.DONE, "changed f", files=["f.txt"])
        return agents.default_worker(prompt, cwd, call)

    agents.worker = edits_f
    ticket = pipeline.start_ticket("demo", "change f", plan_first=False)

    git(workspace, "clone", str(workspace / "origin.git"), "teammate")
    teammate = workspace / "teammate"
    (teammate / "f.txt").write_text("main\n")
    (teammate / "g.txt").write_text("from main\n")
    git(teammate, "add", "-A")
    git(teammate, "commit", "-m", "teammate")
    git(teammate, "push", "origin", "main")

    opened = pipeline.open_pull_request(ticket.id)
    assert opened.status == TicketStatus.PR_OPEN and opened.pr_number == 1
    assert (remote_repository / "f.txt").read_text() == "ticket and main\n"
    assert (remote_repository / "g.txt").exists()
    assert git(remote_repository, "rev-list", "--merges", "--count", "HEAD") == "1"
    assert git(remote_repository, "status", "--porcelain") == ""
    assert host.reviews == 1
    conflict_review = agents.prompts_for(AgentRole.REVIEWER)[1]
    assert "+ticket and main" in conflict_review and "g.txt" not in conflict_review

    pipeline.follow_up(ticket.id, "one more file")
    pipeline.open_pull_request(ticket.id)
    second_review = agents.prompts_for(AgentRole.REVIEWER)[2]
    assert "work_3.txt" in second_review and "g.txt" not in second_review
    assert host.reviews == 2


def test_a_new_project_is_created_planned_and_holds_every_check(workspace: Path, agents: FakeAgents) -> None:
    settings = get_settings()
    settings.default_protected_paths = [".env", "**/.env", "**/*.key"]
    write_settings(settings)
    project = onboarding.create_project("reminders", "Remind me when the car insurance expires", workspace, None, None)
    assert (project.stack, project.base_branch) == ("python", "main")
    assert project.compose_file == "dev/docker-compose.yml" and project.protected_paths == ["**/*.key"]
    assert (project.path / "README.md").read_text().startswith("# reminders")
    ticket = pipeline.start_ticket(project.name, "Remind me when the car insurance expires", True, new_project=True)
    assert ticket.status == TicketStatus.AWAITING_APPROVAL
    assert ticket.baseline == {"lint": True, "test": True}
    planner_prompt = agents.prompts_for(AgentRole.PLANNER)[0]
    assert "## New project" in planner_prompt and "`true` (test)" in planner_prompt
    assert "adds `dev/docker-compose.yml` with a healthcheck" in planner_prompt and "writes `.env`" in planner_prompt
    assert "You have up to 4 workers" in planner_prompt
    pipeline.approve(ticket.id)
    assert "(new project: set it up and make it pass)" in agents.prompts_for(WORKER)[0]


def _fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int) -> Path:
    calls = tmp_path / "docker-calls.txt"
    binary = tmp_path / "bin" / "docker"
    binary.parent.mkdir()
    binary.write_text(f'#!/bin/sh\necho "$@" >> {calls}\necho "port is already allocated" >&2\nexit {exit_code}\n')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary.parent}:{os.environ['PATH']}")
    return calls


def _use_compose_file(repository: Path, compose_file: str, commit: bool) -> None:
    settings = get_settings()
    settings.project("demo").compose_file = compose_file
    write_settings(settings)
    if commit:
        (repository / compose_file).parent.mkdir(parents=True, exist_ok=True)
        (repository / compose_file).write_text("services: {}\n")
        git(repository, "add", "-A")
        git(repository, "commit", "-m", "add compose file")


def test_cw_starts_the_projects_containers_before_the_worker_and_the_checks(
    local_repository: Path, agents: FakeAgents, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_docker(tmp_path, monkeypatch, exit_code=0)
    _use_compose_file(local_repository, "dev/docker-compose.yml", commit=True)
    pipeline.start_ticket("demo", "add a file", plan_first=False)
    lines = calls.read_text().splitlines()
    assert len(lines) == 3 and set(lines) == {"compose --file dev/docker-compose.yml up --detach --wait"}


def test_no_container_is_started_until_the_compose_file_exists(
    local_repository: Path, agents: FakeAgents, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_docker(tmp_path, monkeypatch, exit_code=0)
    _use_compose_file(local_repository, "dev/docker-compose.yml", commit=False)
    pipeline.start_ticket("demo", "add a file", plan_first=False)
    assert not calls.exists()


def test_containers_that_do_not_start_stop_the_ticket_with_dockers_reason(
    local_repository: Path, agents: FakeAgents, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_docker(tmp_path, monkeypatch, exit_code=1)
    _use_compose_file(local_repository, "compose.yml", commit=True)
    with pytest.raises(AppException, match="Could not start the containers in compose.yml"):
        pipeline.start_ticket("demo", "add a file", plan_first=False)
    assert agents.prompts_for(WORKER) == []
    assert git(local_repository, "branch", "--show-current") == "main"
    assert "cw/" not in git(local_repository, "branch", "--list")


def test_files_the_checks_leave_behind_are_reported_on_the_ticket(workspace: Path, agents: FakeAgents) -> None:
    make_repository(workspace, with_remote=False)
    settings = get_settings()
    settings.project("demo").commands.test = "touch report.xml"
    write_settings(settings)
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    assert any("left files behind (report.xml)" in warning for warning in ticket.warnings)


def test_git_decides_which_files_a_worker_changed_and_an_empty_done_is_flagged(
    local_repository: Path, agents: FakeAgents
) -> None:
    agents.worker = lambda prompt, cwd, call: answer(WorkerStatus.DONE, "claims work", files=["made_up.py"])
    ticket = pipeline.start_ticket("demo", "do nothing", plan_first=False)
    assert ticket.work_log[0].files == []
    assert "No changes: follow-up: worker W1 reported done but changed no files." in ticket.warnings


def test_a_task_that_changes_files_outside_its_list_is_flagged(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    ticket.subtasks[0].files = ["planned.txt"]
    write_ticket(ticket)
    done = pipeline.approve(ticket.id)
    assert "Scope: S1: worker W1 changed files outside its task: work_1.txt" in done.warnings
    assert not any(warning.startswith("Scope: S2") for warning in done.warnings)


def test_a_file_the_task_instructions_name_is_in_scope(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    ticket.subtasks[0].files = ["planned.txt"]
    ticket.subtasks[0].instructions += "\nYou may also edit work_1.txt."
    write_ticket(ticket)
    done = pipeline.approve(ticket.id)
    assert not any(warning.startswith("Scope:") for warning in done.warnings)


def test_stats_count_a_resumed_session_once(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    costs = [(WORKER, "w", 1.0), (WORKER, "w", 1.5), (AgentRole.REVIEWER, "r", 0.5), (WORKER, "w2", 0.25)]
    ticket.runs = [usage(role, session).model_copy(update={"cost_usd": cost}) for role, session, cost in costs]
    write_ticket(ticket)
    spent = {row["role"]: row["cost_usd"] for row in reports.usage_stats(get_settings())}
    assert spent == {"worker": 1.75, "reviewer": 0.5}


def test_a_session_past_the_token_ceiling_starts_fresh(
    local_repository: Path, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "WORKER_SESSION_TOKEN_CEILING", 0)
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    pipeline.approve(ticket.id)
    assert [session for role, session in agents.resumed if role == WORKER] == [None, None]
    assert "## Request" in agents.prompts_for(WORKER)[1]


def test_the_checks_run_after_each_task_so_the_next_task_starts_from_working_code(
    local_repository: Path, agents: FakeAgents
) -> None:
    def breaks_then_fixes(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            (cwd / "BROKEN").write_text("x")
        if prompt.startswith("## Fix the failing checks"):
            (cwd / "BROKEN").unlink()
        return agents.default_worker(prompt, cwd, call)

    agents.worker = breaks_then_fixes
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    done = pipeline.approve(ticket.id)
    first, fix, second = agents.prompts_for(WORKER)
    assert "## Your task: S1" in first and fix.startswith("## Fix the failing checks") and "S2" in second
    assert done.failing_checks == [] and done.status == TicketStatus.READY


def test_a_task_whose_checks_still_fail_after_the_fix_round_stops_the_ticket_and_resumes_there(
    local_repository: Path, agents: FakeAgents
) -> None:
    def breaks_the_check(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / "BROKEN").write_text("x")
        return agents.default_worker(prompt, cwd, call)

    agents.worker = breaks_the_check
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    with pytest.raises(AppException, match="S1: `test ! -f BROKEN` still fails after the fix round"):
        pipeline.approve(ticket.id)
    stopped = find_ticket(ticket.id)
    assert stopped.status == TicketStatus.BLOCKED and stopped.tasks_done == []
    task, fix, backoff, backoff_fix = agents.prompts_for(WORKER)
    assert "## Request" in backoff and "## A previous attempt failed" in backoff
    assert backoff_fix.startswith("## Fix the failing checks")

    (local_repository / "BROKEN").unlink()
    agents.worker = agents.default_worker
    resumed = pipeline.resume(ticket.id)
    assert resumed.tasks_done == ["S1", "S2"] and resumed.status == TicketStatus.READY


def test_a_task_still_failing_after_its_fix_round_gets_a_fresh_session_that_starts_from_the_reflection(
    local_repository: Path, agents: FakeAgents
) -> None:
    def fixes_only_after_backing_off(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            (cwd / "BROKEN").write_text("x")
        if prompt.startswith("## Fix the failing checks"):
            return answer(WorkerStatus.DONE, "tried", reflection="BROKEN comes back; delete it instead")
        if "## A previous attempt failed" in prompt:
            (cwd / "BROKEN").unlink()
        return agents.default_worker(prompt, cwd, call)

    agents.worker = fixes_only_after_backing_off
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    done = pipeline.approve(ticket.id)
    _, _, backoff, _ = agents.prompts_for(WORKER)
    assert "Lesson from the previous attempt:\nBROKEN comes back; delete it instead" in backoff
    sessions = [session for role, session in agents.resumed if role == WORKER]
    assert sessions[1] is not None and sessions[2] is None and sessions[3] is not None
    assert done.tasks_done == ["S1", "S2"] and done.status == TicketStatus.READY


def test_older_tasks_keep_their_handoff_but_lose_their_summary_in_the_workers_view(
    local_repository: Path, agents: FakeAgents
) -> None:
    agents.task_workers = ["W1", "W2", "W3"]
    ticket = pipeline.start_ticket("demo", "three steps", plan_first=True)
    pipeline.approve(ticket.id)
    third = agents.prompts_for(WORKER)[2]
    assert "- Solved by W1\n- Files: work_1.txt\n- For the next worker: work_1.txt holds the result" in third
    assert "- Solved by W2: pass 2" in third


def test_a_workers_project_notes_stay_with_the_ticket_and_never_touch_claude_md(
    local_repository: Path, agents: FakeAgents
) -> None:
    def notes(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / "app.py").write_text("x = 1\n")
        return answer(WorkerStatus.DONE, "added app", project_notes=["The entry point is app.py."])

    agents.worker = notes
    ticket = pipeline.start_ticket("demo", "add app", plan_first=False)
    assert not (local_repository / "CLAUDE.md").exists()
    assert ticket.work_log[0].project_notes == ["The entry point is app.py."]
    assert ticket.work_log[0].files == ["app.py"]


def test_fix_sends_the_open_pr_comments_to_the_worker_pushes_the_fix_and_resolves_them(
    remote_repository: Path, workspace: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    host.threads = [
        PullRequestThread(id="T1", path="work_1.txt", line=1, body="reviewer: be stricter\nalice: and rename it"),
    ]
    fixed = pipeline.fix_pull_request(ticket.id)
    prompt = agents.prompts_for(WORKER)[1]
    assert prompt.startswith("## Address these pull request comments")
    assert "- [thread T1] `work_1.txt:1`\n  reviewer: be stricter\n  alice: and rename it" in prompt
    assert fixed.status == TicketStatus.PR_OPEN and host.resolved == ["T1"]
    assert host.summaries[-1] == "**W1** · 1 comment(s)\n\npass 2"
    assert git(remote_repository, "rev-list", "--count", f"main..{ticket.branch}") == "2"
    assert git(workspace / "origin.git", "rev-parse", ticket.branch) == git(remote_repository, "rev-parse", "HEAD")


def _pull_request_fix_prompts(agents: FakeAgents) -> list[str]:
    return [prompt for prompt in agents.prompts_for(WORKER) if prompt.startswith("## Address these pull request")]


def test_fix_posts_each_new_review_as_a_comment_and_stops_when_no_blocking_finding_is_open(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    nit = ReviewComment(path="work_1.txt", line=1, severity=Severity.MINOR, body="rename it")
    agents.reviewer = lambda prompt: ReviewerResult(summary="ok", comments=[nit])
    host.threads = [PullRequestThread(id="T1", path="work_1.txt", line=1, body="alice: be stricter")]
    fixed = pipeline.fix_pull_request(ticket.id)
    assert len(_pull_request_fix_prompts(agents)) == 1 and host.resolved == ["T1"]
    assert [thread.id for thread in host.threads] == ["T1"]
    assert "- `work_1.txt:1` **minor** [F1] rename it" in host.summaries[-2]
    assert fixed.open_findings == []


def test_fix_goes_round_again_while_a_blocking_finding_stays_open_up_to_its_cap(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="still wrong")
    agents.reviewer = lambda prompt: ReviewerResult(summary="no", comments=[] if "still open" in prompt else [finding])
    host.threads = [PullRequestThread(id="T1", path="work_1.txt", line=1, body="alice: be stricter")]
    fixed = pipeline.fix_pull_request(ticket.id)
    assert len(_pull_request_fix_prompts(agents)) == 3 and len(fixed.open_findings) == 1


def test_fix_stops_after_a_round_in_which_the_worker_changed_nothing(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    stored = find_ticket(ticket.id)
    stored.open_findings = [finding.model_copy(update={"id": "F9"})]
    write_ticket(stored)
    agents.worker = lambda prompt, cwd, call: answer(WorkerStatus.DONE, "already right")
    pipeline.fix_pull_request(ticket.id)
    assert len(_pull_request_fix_prompts(agents)) == 1


def test_fix_sends_the_open_blocking_findings_and_a_review_that_says_they_are_fixed_closes_them(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    agents.reviewer = lambda prompt: ReviewerResult(summary="no", comments=[] if "still open" in prompt else [finding])
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    host.threads = []
    assert [f.id for f in find_ticket(ticket.id).open_findings] == ["F1"]

    agents.reviewer = lambda prompt: ReviewerResult(summary="fixed", comments=[], resolved=["F1"])
    fixed = pipeline.fix_pull_request(ticket.id)
    assert (
        "Blocking findings of the last review, still open:\n\n- `work_1.txt:1` wrong"
        in (_pull_request_fix_prompts(agents)[0])
    )
    assert fixed.open_findings == [] and not any(w.startswith("Review:") for w in fixed.warnings)


def test_fix_without_open_comments_runs_no_agent(remote_repository: Path, agents: FakeAgents, host: FakeHost) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    runs = len(agents.prompts)
    pipeline.fix_pull_request(ticket.id)
    assert len(agents.prompts) == runs and host.resolved == []


def test_merge_refuses_until_no_blocking_finding_or_unresolved_thread_is_left_and_ci_passed(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    agents.reviewer = lambda prompt: ReviewerResult(summary="fine", comments=[finding])
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    with pytest.raises(AppException, match="blocking review finding"):
        pipeline.merge_pull_request(ticket.id)

    stored = find_ticket(ticket.id)
    stored.open_findings = []
    write_ticket(stored)
    with pytest.raises(AppException, match=r"unresolved pull request thread\(s\) \(not addressed: work_1.txt:1"):
        pipeline.merge_pull_request(ticket.id)

    host.resolved = [thread.id for thread in host.threads]
    host.ci = False
    with pytest.raises(AppException, match="CI has not passed"):
        pipeline.merge_pull_request(ticket.id)
    assert host.merged == [] and find_ticket(ticket.id).status == TicketStatus.PR_OPEN


def test_merge_expects_ci_checks_when_the_branch_has_ci_config(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    def adds_a_workflow(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / ".github" / "workflows").mkdir(parents=True)
        (cwd / ".github" / "workflows" / "ci.yml").write_text("on: pull_request\n")
        return answer(WorkerStatus.DONE, "added CI")

    agents.worker = adds_a_workflow
    ticket = pipeline.start_ticket("demo", "add CI", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    host.ci = False
    with pytest.raises(AppException, match="not reported yet"):
        pipeline.merge_pull_request(ticket.id)
    assert host.checks_expected == [True] and host.merged == []


def test_merge_squash_merges_on_the_host_closes_the_ticket_and_deletes_the_local_branch(
    workspace: Path, remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)

    def squash_on_the_host(number: int, branch: str, message: str, sha: str) -> str:
        host.merged.append((number, branch, message, sha))
        git(workspace, "clone", str(workspace / "origin.git"), "host")
        git(workspace / "host", "merge", "--squash", f"origin/{branch}")
        git(workspace / "host", "commit", "-m", f"{message} (#{number})")
        git(workspace / "host", "push", "origin", "main")
        return git(workspace / "host", "rev-parse", "HEAD")

    host.merge = squash_on_the_host
    pushed = git(remote_repository, "rev-parse", ticket.branch)
    merged = pipeline.merge_pull_request(ticket.id)
    assert host.merged == [(1, ticket.branch, "feat: work", pushed)] and host.checks_expected == [False]
    assert merged.status == TicketStatus.DONE
    assert git(remote_repository, "branch", "--show-current") == "main"
    assert ticket.branch not in git(remote_repository, "branch", "--list")
    assert git(remote_repository, "rev-parse", "main") == git(workspace / "origin.git", "rev-parse", "main")
    assert (remote_repository / "work_1.txt").exists()
    assert merged.merge_commit == git(workspace / "origin.git", "rev-parse", "main")
    assert merged.changed_files == ["work_1.txt"]


def test_a_conflict_resolution_is_checked_and_reviewed_before_it_is_pushed(
    remote_repository: Path, workspace: Path, agents: FakeAgents, host: FakeHost
) -> None:
    def breaks_while_resolving(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if call == 1:
            (cwd / "f.txt").write_text("ticket\n")
            return answer(WorkerStatus.DONE, "changed f", files=["f.txt"])
        if "Resolve merge conflicts" in prompt:
            (cwd / "BROKEN").write_text("x")
        if prompt.startswith("## Fix the failing checks"):
            (cwd / "BROKEN").unlink()
        return agents.default_worker(prompt, cwd, call)

    agents.worker = breaks_while_resolving
    ticket = pipeline.start_ticket("demo", "change f", plan_first=False)
    git(workspace, "clone", str(workspace / "origin.git"), "teammate")
    (workspace / "teammate" / "f.txt").write_text("main\n")
    git(workspace / "teammate", "commit", "-am", "teammate")
    git(workspace / "teammate", "push", "origin", "main")

    opened = pipeline.open_pull_request(ticket.id)
    worker_prompts = agents.prompts_for(WORKER)
    assert "Resolve merge conflicts" in worker_prompts[1] and worker_prompts[2].startswith("## Fix the failing")
    assert len(agents.prompts_for(AgentRole.REVIEWER)) == 2 and opened.failing_checks == []
    assert not (remote_repository / "BROKEN").exists()
    assert git(remote_repository, "status", "--porcelain") == ""


def test_a_blocking_finding_stays_open_when_a_later_review_sees_only_unrelated_work(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="SQL injection")
    agents.reviewer = lambda prompt: ReviewerResult(summary="no", comments=[] if "still open" in prompt else [finding])
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.review(ticket.id)
    pipeline.follow_up(ticket.id, "an unrelated change")
    pipeline.open_pull_request(ticket.id)
    assert "SQL injection" in agents.prompts_for(AgentRole.REVIEWER)[-1]
    with pytest.raises(AppException, match="1 blocking review finding"):
        pipeline.merge_pull_request(ticket.id)


def test_plan_feedback_resumes_the_planner_with_only_the_feedback(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    pipeline.follow_up(ticket.id, "use one worker")
    first, second = agents.prompts_for(AgentRole.PLANNER)
    assert "## Request" in first and "## Request" not in second and "You have up to" not in second
    assert "use one worker" in second and "## Constraints" in second
    assert agents.resumed[-1] == (AgentRole.PLANNER, ticket.planner_session)


def test_a_planner_that_still_asks_after_its_last_round_is_told_to_plan_and_never_leaves_an_empty_plan(
    local_repository: Path, agents: FakeAgents
) -> None:
    nothing = {"split": [], "options_considered": "", "files": [], "answers": []}
    task = Subtask(id="S1", title="do it", instructions="do it", files=[])

    def asks_until_told(prompt: str) -> PlannerResult:
        if "Write the plan now" in prompt:
            return PlannerResult(**nothing, questions=[], plan="the plan", subtasks=[task])
        return PlannerResult(**nothing, questions=["which colour?"], plan="", subtasks=[])

    agents.planner = asks_until_told
    ticket = pipeline.start_ticket("demo", "paint it", plan_first=True)
    assert ticket.status == TicketStatus.NEEDS_INPUT and ticket.questions == ["which colour?"]
    planned = pipeline.follow_up(ticket.id, "blue")
    assert planned.status == TicketStatus.AWAITING_APPROVAL and planned.plan == "the plan"
    assert len(agents.prompts_for(AgentRole.PLANNER)) == 3

    agents.planner = lambda prompt: PlannerResult(**nothing, questions=[], plan="", subtasks=[])
    empty = pipeline.follow_up(ticket.id, "start over")
    assert empty.status == TicketStatus.NEEDS_INPUT and empty.questions == [prompts.NO_PLAN]


def test_a_review_killed_without_cleanup_resumes_as_a_review_and_opens_no_pull_request(
    remote_repository: Path, agents: FakeAgents, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)

    def killed(*arguments: object) -> None:
        raise SystemExit(1)

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "_review", killed)
        with pytest.raises(SystemExit):
            pipeline.review(ticket.id)
    stale = find_ticket(ticket.id)
    assert (stale.status, stale.paused_step) == (TicketStatus.IN_REVIEW, Step.REVIEW)
    resumed = pipeline.resume(ticket.id)
    assert resumed.status == TicketStatus.READY and resumed.pr_number is None and host.targets == []


def test_split_creates_paused_tickets_without_running_agents_and_abandons_the_original_last(
    local_repository: Path, agents: FakeAgents
) -> None:
    nothing = {"questions": [], "plan": "", "options_considered": "", "subtasks": [], "files": [], "answers": []}
    agents.planner = lambda prompt: PlannerResult(**nothing, split=["add A", "add B"])
    ticket = pipeline.start_ticket("demo", "add A and B, each in its own PR", plan_first=True)
    created = pipeline.split_ticket(ticket.id)
    assert [(t.status, t.paused_step) for t in created] == [(TicketStatus.PAUSED, Step.PLAN)] * 2
    assert all(t.previous_branch == "main" for t in created) and agents.prompts_for(WORKER) == []
    assert find_ticket(ticket.id).status == TicketStatus.ABANDONED
    assert git(local_repository, "branch", "--show-current") == "main"

    agents.planner = None
    planned = pipeline.resume(created[0].id)
    assert planned.status == TicketStatus.AWAITING_APPROVAL


def test_a_ticket_keeps_at_most_five_notes(local_repository: Path, agents: FakeAgents) -> None:
    def three_notes(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / f"app_{call}.py").write_text("x = 1\n")
        notes = [f"Note {call}.{number}." for number in range(3)]
        return answer(WorkerStatus.DONE, "added app", project_notes=notes)

    agents.worker = three_notes
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    approved = pipeline.approve(ticket.id)
    kept = [note for entry in approved.work_log for note in entry.project_notes]
    assert len(kept) == 5 and "Note 2.1." in kept and "Note 2.2." not in kept
    assert not (local_repository / "CLAUDE.md").exists()


def test_worker_runs_are_labelled_with_worker_and_task_and_each_check_is_an_event(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    pipeline.approve(ticket.id)
    assert agents.labels == ["planner", "W1 · S1", "W1 · S2"]
    events = [json.loads(line) for line in events_path("demo", ticket.id).read_text().splitlines()]
    assert {event["kind"] for event in events} == {"check"} and all(event["passed"] for event in events)


def _set_limit(name: str, value: int) -> None:
    config_file = Path(os.environ["CW_HOME"]) / "config.json"
    config = json.loads(config_file.read_text())
    for project in config["projects"]:
        project["profile"]["limits"][name] = value
    config_file.write_text(json.dumps(config))
    get_settings.cache_clear()


def _reviewer_calls(agents: FakeAgents) -> int:
    return len(agents.prompts_for(AgentRole.REVIEWER))


def _comment_worker(outcome: CommentOutcomeKind | None, lines: int = 1, reason: str = ""):
    def work(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if "## Address these pull request comments" not in prompt:
            return FakeAgents().default_worker(prompt, cwd, call)
        if lines:
            (cwd / f"fix_{call}.txt").write_text("".join(f"line {number}\n" for number in range(lines)))
        outcomes = [CommentOutcome(thread_id="T1", outcome=outcome, reason=reason)] if outcome else []
        return answer(WorkerStatus.DONE, "handled", comment_outcomes=outcomes)

    return work


def _open_pr_with_comment(agents: FakeAgents, host: FakeHost, body: str = "alice: rename it") -> str:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    host.threads = [PullRequestThread(id="T1", path="work_1.txt", line=1, body=body, last_comment=body)]
    return ticket.id


def test_a_declined_comment_gets_a_reply_stays_open_and_is_not_sent_again_until_a_person_answers(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket_id = _open_pr_with_comment(agents, host)
    agents.worker = _comment_worker(CommentOutcomeKind.DECLINED, lines=0, reason="the name follows the module")
    pipeline.fix_pull_request(ticket_id)
    assert host.replies == [("T1", f"{pipeline.DECLINE_REPLY_PREFIX} the name follows the module")]
    assert host.resolved == []

    sent = len(_pull_request_fix_prompts(agents))
    pipeline.fix_pull_request(ticket_id)
    assert len(_pull_request_fix_prompts(agents)) == sent
    with pytest.raises(AppException, match="declined by the worker, for you to decide: work_1.txt:1"):
        pipeline.merge_pull_request(ticket_id)

    host.threads[0].last_comment = "alice: please rename it anyway"
    pipeline.fix_pull_request(ticket_id)
    assert len(_pull_request_fix_prompts(agents)) == sent + 1


def test_a_declined_thread_carrying_a_blocking_finding_is_not_sent_again_as_a_finding(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket_id = _open_pr_with_comment(agents, host, body="me: **blocking** [F9] must validate")
    stored = find_ticket(ticket_id)
    stored.open_findings = [ReviewComment(id="F9", path="work_1.txt", line=1, severity=Severity.BLOCKING, body="x")]
    write_ticket(stored)
    agents.worker = _comment_worker(CommentOutcomeKind.DECLINED, lines=0, reason="validated upstream")
    pipeline.fix_pull_request(ticket_id)
    sent = len(_pull_request_fix_prompts(agents))
    pipeline.fix_pull_request(ticket_id)
    assert len(_pull_request_fix_prompts(agents)) == sent


def test_a_fixed_comment_is_resolved_and_a_comment_without_an_outcome_stays_open(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket_id = _open_pr_with_comment(agents, host)
    agents.worker = _comment_worker(None)
    pipeline.fix_pull_request(ticket_id)
    assert host.resolved == [] and host.replies == []

    agents.worker = _comment_worker(CommentOutcomeKind.FIXED)
    pipeline.fix_pull_request(ticket_id)
    assert host.resolved == ["T1"]


def test_a_fix_round_reviews_once_and_leaves_new_blocking_findings_to_the_next_round(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket_id = _open_pr_with_comment(agents, host)
    reviews_before = _reviewer_calls(agents)
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="still wrong")
    agents.reviewer = lambda prompt: ReviewerResult(summary="no", comments=[finding])
    fixed = pipeline.fix_pull_request(ticket_id)
    rounds = len(_pull_request_fix_prompts(agents))
    assert rounds == get_settings().project("demo").profile.limits.max_pull_request_fix_rounds
    assert _reviewer_calls(agents) - reviews_before == rounds
    assert fixed.open_findings


def test_every_review_after_the_first_runs_on_the_follow_up_model(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.review(ticket.id)
    pipeline.follow_up(ticket.id, "one more file")
    pipeline.review(ticket.id)
    reviewer_models = [
        model for (role, _), model in zip(agents.prompts, agents.models, strict=True) if role == AgentRole.REVIEWER
    ]
    assert reviewer_models == [None, get_settings().project("demo").profile.models.reviewer_followup]
    assert get_settings().project("demo").profile.models.reviewer_followup == "sonnet"


def test_a_small_comment_only_fix_skips_the_review(remote_repository: Path, agents: FakeAgents, host: FakeHost) -> None:
    _set_limit("review_skip_max_changed_lines", 20)
    ticket_id = _open_pr_with_comment(agents, host)
    reviews_before = _reviewer_calls(agents)
    agents.worker = _comment_worker(CommentOutcomeKind.FIXED, lines=2)
    pipeline.fix_pull_request(ticket_id)
    assert _reviewer_calls(agents) == reviews_before
    assert "Review skipped: the round changed 2 line(s) (limit 20)" in host.summaries[-1]


@pytest.mark.parametrize(
    ("limit", "lines", "broken"),
    [(20, 30, False), (0, 2, False), (20, 2, True)],
    ids=["too-many-lines", "skip-disabled", "failing-checks"],
)
def test_a_comment_fix_is_reviewed_when_it_is_big_skipping_is_off_or_the_checks_fail(
    remote_repository: Path, agents: FakeAgents, host: FakeHost, limit: int, lines: int, broken: bool
) -> None:
    _set_limit("review_skip_max_changed_lines", limit)
    ticket_id = _open_pr_with_comment(agents, host)
    reviews_before = _reviewer_calls(agents)
    fixing = _comment_worker(CommentOutcomeKind.FIXED, lines=lines)

    def maybe_breaking(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if broken:
            (cwd / "BROKEN").write_text("x")
        return fixing(prompt, cwd, call)

    agents.worker = maybe_breaking
    pipeline.fix_pull_request(ticket_id)
    assert _reviewer_calls(agents) > reviews_before


def test_a_fix_of_a_blocking_finding_posted_as_a_thread_is_always_reviewed(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    _set_limit("review_skip_max_changed_lines", 20)
    ticket_id = _open_pr_with_comment(agents, host, body="me: **blocking** [F9] the text GitHub reformatted")
    stored = find_ticket(ticket_id)
    stored.open_findings = [
        ReviewComment(id="F9", path="work_1.txt", line=1, severity=Severity.BLOCKING, body="the original text")
    ]
    write_ticket(stored)
    reviews_before = _reviewer_calls(agents)
    agents.worker = _comment_worker(CommentOutcomeKind.FIXED, lines=2)
    pipeline.fix_pull_request(ticket_id)
    assert _reviewer_calls(agents) > reviews_before
    assert "Blocking findings of the last review" not in _pull_request_fix_prompts(agents)[0]


def test_a_fix_pass_keeps_no_notes_and_flags_an_unasked_readme_edit_but_not_a_test(
    local_repository: Path, agents: FakeAgents
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    agents.reviewer = lambda prompt: ReviewerResult(summary="no", comments=[] if "still open" in prompt else [finding])

    def fixing(prompt: str, cwd: Path, call: int) -> WorkerResult:
        if not prompt.startswith("## Address these review findings"):
            return agents.default_worker(prompt, cwd, call)
        (cwd / "README.md").write_text("rewritten\n")
        (cwd / "test_extra.py").write_text("def test_extra():\n    assert True\n")
        return answer(WorkerStatus.DONE, "fixed", project_notes=["a lasting fact"])

    agents.worker = fixing
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    reviewed = pipeline.review(ticket.id)
    assert not (local_repository / "CLAUDE.md").exists()
    scope = [warning for warning in reviewed.warnings if warning.startswith("Scope: fix:")]
    assert scope and "README.md" in scope[0] and "test_extra.py" not in scope[0]
    assert "leave `project_notes` empty" in agents.prompts_for(WORKER)[-1]
