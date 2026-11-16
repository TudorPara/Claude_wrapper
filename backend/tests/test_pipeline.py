from pathlib import Path

import pytest
from conftest import FakeAgents, FakeHost, answer, git, make_repository, usage

from cw import onboarding, pipeline, reports
from cw.config import AgentRole, get_settings, save_settings
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequestThread
from cw.models import ReviewComment, ReviewerResult, Severity, Step, TicketStatus, Verdict, WorkerResult, WorkerStatus
from cw.tickets import find_ticket, save_ticket, ticket_path

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
            raise AppException(AppErrorCode.AGENT_FAILED, "No conversation found")
        return agents.default_worker(prompt, cwd, call)

    agents.worker = lost_session
    again = pipeline.follow_up(ticket.id, "rename it")
    assert again.status == TicketStatus.READY
    assert agents.prompts_for(WORKER)[-1].startswith("# add a file\n\n## Request")


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
    save_ticket(ticket)
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
    agents.reviewer = lambda prompt: ReviewerResult(verdict=Verdict.CHANGES, summary="no", comments=[finding, nit])
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    opened = pipeline.open_pull_request(ticket.id)
    assert opened.status == TicketStatus.PR_OPEN and opened.pr_number == 1
    assert "Review: 1 blocking findings left after the fix round." in opened.warnings
    assert len(agents.prompts_for(AgentRole.REVIEWER)) == 2 and host.reviews == 1
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

    pipeline.follow_up(ticket.id, "one more file")
    pipeline.open_pull_request(ticket.id)
    second_review = agents.prompts_for(AgentRole.REVIEWER)[1]
    assert "work_3.txt" in second_review and "g.txt" not in second_review
    assert host.reviews == 2


def test_a_new_project_is_created_planned_and_holds_every_check(workspace: Path, agents: FakeAgents) -> None:
    project = onboarding.create_project("reminders", "Remind me when the car insurance expires", workspace, None, None)
    assert (project.stack, project.base_branch) == ("python", "main")
    assert (project.path / "README.md").read_text().startswith("# reminders")
    ticket = pipeline.start_ticket(project.name, "Remind me when the car insurance expires", True, new_project=True)
    assert ticket.status == TicketStatus.AWAITING_APPROVAL
    assert ticket.baseline == {"lint": True, "test": True}
    planner_prompt = agents.prompts_for(AgentRole.PLANNER)[0]
    assert "## New project" in planner_prompt and "`true` (test)" in planner_prompt
    assert "You have up to 4 workers" in planner_prompt
    pipeline.approve(ticket.id)
    assert "(new project: set it up and make it pass)" in agents.prompts_for(WORKER)[0]


def test_files_the_checks_leave_behind_are_reported_on_the_ticket(workspace: Path, agents: FakeAgents) -> None:
    make_repository(workspace, with_remote=False)
    settings = get_settings()
    settings.project("demo").commands.test = "touch report.xml"
    save_settings(settings)
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
    save_ticket(ticket)
    done = pipeline.approve(ticket.id)
    assert "Scope: S1: worker W1 changed files outside its task: work_1.txt" in done.warnings
    assert not any(warning.startswith("Scope: S2") for warning in done.warnings)


def test_a_file_the_task_instructions_name_is_in_scope(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "two steps", plan_first=True)
    ticket.subtasks[0].files = ["planned.txt"]
    ticket.subtasks[0].instructions += "\nYou may also edit work_1.txt."
    save_ticket(ticket)
    done = pipeline.approve(ticket.id)
    assert not any(warning.startswith("Scope:") for warning in done.warnings)


def test_stats_count_a_resumed_session_once(local_repository: Path, agents: FakeAgents) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    costs = [(WORKER, "w", 1.0), (WORKER, "w", 1.5), (AgentRole.REVIEWER, "r", 0.5), (WORKER, "w2", 0.25)]
    ticket.runs = [usage(role, session).model_copy(update={"cost_usd": cost}) for role, session, cost in costs]
    save_ticket(ticket)
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


def test_older_tasks_keep_their_handoff_but_lose_their_summary_in_the_workers_view(
    local_repository: Path, agents: FakeAgents
) -> None:
    agents.task_workers = ["W1", "W2", "W3"]
    ticket = pipeline.start_ticket("demo", "three steps", plan_first=True)
    pipeline.approve(ticket.id)
    third = agents.prompts_for(WORKER)[2]
    assert "- Solved by W1\n- Files: work_1.txt\n- For the next worker: work_1.txt holds the result" in third
    assert "- Solved by W2: pass 2" in third


def test_a_workers_project_notes_land_in_the_projects_claude_md_and_in_the_diff(
    local_repository: Path, agents: FakeAgents
) -> None:
    def notes(prompt: str, cwd: Path, call: int) -> WorkerResult:
        (cwd / "app.py").write_text("x = 1\n")
        return answer(WorkerStatus.DONE, "added app", project_notes=["The entry point is app.py."])

    agents.worker = notes
    ticket = pipeline.start_ticket("demo", "add app", plan_first=False)
    assert "- The entry point is app.py." in (local_repository / "CLAUDE.md").read_text()
    assert ticket.work_log[0].files == ["app.py"]
    assert "CLAUDE.md" in git(local_repository, "status", "--porcelain")
    pipeline.review(ticket.id)
    assert "## cw's project notes" in agents.prompts_for(AgentRole.REVIEWER)[0]


def test_the_reviewer_hears_about_cw_notes_only_when_the_diff_has_them(
    local_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.review(ticket.id)
    assert "## cw's project notes" not in agents.prompts_for(AgentRole.REVIEWER)[0]


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
    assert "- `work_1.txt:1`\n  reviewer: be stricter\n  alice: and rename it" in prompt
    assert fixed.status == TicketStatus.PR_OPEN and host.resolved == ["T1"]
    assert host.summaries[-1] == "**W1** · 1 comment(s)\n\npass 2"
    assert git(remote_repository, "rev-list", "--count", f"main..{ticket.branch}") == "2"
    assert git(workspace / "origin.git", "rev-parse", ticket.branch) == git(remote_repository, "rev-parse", "HEAD")


def test_fix_without_open_comments_runs_no_agent(remote_repository: Path, agents: FakeAgents, host: FakeHost) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    runs = len(agents.prompts)
    pipeline.fix_pull_request(ticket.id)
    assert len(agents.prompts) == runs and host.resolved == []


def test_merge_refuses_until_ci_passed_and_no_blocking_finding_is_left(
    remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="wrong")
    agents.reviewer = lambda prompt: ReviewerResult(verdict=Verdict.CHANGES, summary="no", comments=[finding])
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    with pytest.raises(AppException, match="blocking review finding"):
        pipeline.merge_pull_request(ticket.id)

    stored = find_ticket(ticket.id)
    stored.reviews = []
    save_ticket(stored)
    host.ci = False
    with pytest.raises(AppException, match="CI has not passed"):
        pipeline.merge_pull_request(ticket.id)
    assert host.merged == [] and find_ticket(ticket.id).status == TicketStatus.PR_OPEN


def test_merge_squash_merges_on_the_host_closes_the_ticket_and_deletes_the_local_branch(
    workspace: Path, remote_repository: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)

    def squash_on_the_host(number: int, branch: str, message: str) -> None:
        host.merged.append((number, branch, message))
        git(workspace, "clone", str(workspace / "origin.git"), "host")
        git(workspace / "host", "merge", "--squash", f"origin/{branch}")
        git(workspace / "host", "commit", "-m", message)
        git(workspace / "host", "push", "origin", "main")

    host.merge = squash_on_the_host
    merged = pipeline.merge_pull_request(ticket.id)
    assert host.merged == [(1, ticket.branch, "feat: work")]
    assert merged.status == TicketStatus.DONE
    assert git(remote_repository, "branch", "--show-current") == "main"
    assert ticket.branch not in git(remote_repository, "branch", "--list")
    assert git(remote_repository, "rev-parse", "main") == git(workspace / "origin.git", "rev-parse", "main")
    assert (remote_repository / "work_1.txt").exists()
