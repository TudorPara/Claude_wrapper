import json
import logging
import os
from pathlib import Path

import pytest
from conftest import make_repository

from cw import claude_runner, process
from cw.config import AgentRole, get_settings
from cw.errors import AppErrorCode, AppException
from cw.models import WorkerResult
from cw.tickets import events_path


def test_run_process_returns_output_and_reports_each_line(tmp_path: Path) -> None:
    seen = []
    code, stdout, stderr = process.run_process(
        "echo one; echo two; echo oops >&2; exit 3", tmp_path, dict(os.environ), 1, on_line=seen.append
    )
    assert (code, stdout, stderr.strip(), seen) == (3, "one\ntwo\n", "oops", ["one\n", "two\n"])


def test_run_process_kills_a_command_that_runs_past_its_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "SECONDS_PER_MINUTE", 0.2)
    with pytest.raises(AppException) as raised:
        process.run_process("sleep 30 & sleep 30", tmp_path, dict(os.environ), 1)
    assert raised.value.error_code == AppErrorCode.TIMED_OUT


def _stream(result: dict) -> str:
    tool_call = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.py"}}]},
    }
    return "\n".join(
        ["not json", json.dumps({"type": "system", "subtype": "init"}), json.dumps(tool_call), json.dumps(result)]
    )


def test_the_result_event_is_found_and_tool_calls_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    stream = _stream({"type": "result", "is_error": False, "num_turns": 3})
    assert claude_runner._result_event(stream)["num_turns"] == 3
    recorded = []
    with caplog.at_level(logging.INFO):
        for line in stream.splitlines():
            claude_runner._log_tool_calls("worker", line, recorded.append)
    assert "worker · Edit src/a.py" in caplog.text
    assert recorded == [{"kind": "tool", "tool": "Edit", "detail": "src/a.py"}]


def _fake_cli(monkeypatch: pytest.MonkeyPatch, result: dict) -> list[list[str]]:
    commands = []

    def run(command, cwd, environment, timeout_minutes, stdin_text=None, on_line=None):
        commands.append(command)
        assert "BITBUCKET__API_TOKEN" not in environment and environment["CW_RUN"] == "1"
        return 0, _stream(result), ""

    monkeypatch.setattr(claude_runner, "run_process", run)
    return commands


def test_run_agent_passes_the_limits_and_returns_the_structured_result(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BITBUCKET__EMAIL", "me@example.test")
    monkeypatch.setenv("BITBUCKET__API_TOKEN", "secret")
    repository = make_repository(workspace, with_remote=False)
    answer = {"status": "done", "summary": "ok", "commit_message": "feat: x", "reason": ""}
    answer |= {"files": ["a.py"], "handoff": "", "questions": [], "project_notes": []}
    result = {
        "type": "result",
        "is_error": False,
        "structured_output": answer,
        "session_id": "abc",
        "num_turns": 4,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "modelUsage": {"small": {"outputTokens": 1}, "big": {"outputTokens": 9}},
    }
    commands = _fake_cli(monkeypatch, result)
    settings = get_settings()
    worker, usage = claude_runner.run_agent(
        settings, settings.project("demo"), "worker", AgentRole.WORKER, "do it", repository, WorkerResult, "t1"
    )
    command = commands[0]
    assert worker.summary == "ok" and (usage.session_id, usage.num_turns, usage.model) == ("abc", 4, "big")
    worker_turns = settings.project("demo").profile.limits.max_turns.worker
    assert command[command.index("--max-turns") + 1] == str(worker_turns)
    assert command[command.index("--output-format") + 1] == "stream-json" and "--verbose" in command
    events = [json.loads(line) for line in events_path("demo", "t1").read_text().splitlines()]
    assert [event["kind"] for event in events] == ["start", "done"]
    assert events[1]["label"] == "worker" and events[1]["turns"] == 4 and events[1]["session_id"] == "abc"
    assert usage.label == "worker"
    assert command[command.index("--setting-sources") + 1] == "project" and "--mcp-config" not in command


def test_a_run_loads_only_the_projects_own_mcp_servers(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = make_repository(workspace, with_remote=False)
    (repository / ".mcp.json").write_text('{"mcpServers": {}}')
    answer = {"status": "done", "summary": "ok", "commit_message": "feat: x", "reason": ""}
    answer |= {"files": [], "handoff": "", "questions": [], "project_notes": []}
    commands = _fake_cli(monkeypatch, {"type": "result", "is_error": False, "structured_output": answer})
    settings = get_settings()
    claude_runner.run_agent(
        settings, settings.project("demo"), "worker", AgentRole.WORKER, "do it", repository, WorkerResult, "t1"
    )
    command = commands[0]
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == str(repository / ".mcp.json")


def test_a_run_that_hits_the_turn_limit_fails_with_its_reason_and_tokens(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = make_repository(workspace, with_remote=False)
    _fake_cli(monkeypatch, {"type": "result", "is_error": True, "subtype": "error_max_turns", "num_turns": 40})
    settings = get_settings()
    with pytest.raises(AppException) as raised:
        claude_runner.run_agent(
            settings, settings.project("demo"), "worker", AgentRole.WORKER, "x", repository, WorkerResult, "t1"
        )
    assert "error_max_turns" in str(raised.value) and raised.value.usage.num_turns == 40
    last = json.loads(events_path("demo", "t1").read_text().splitlines()[-1])
    assert last["kind"] == "error" and "error_max_turns" in last["message"]


def test_a_resumed_session_that_no_longer_exists_is_reported_as_lost(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = make_repository(workspace, with_remote=False)
    lost = "No conversation found with session ID: 00000000-0000-4000-8000-000000000000"
    gone = {"type": "result", "is_error": True, "subtype": "error_during_execution", "errors": [lost]}
    _fake_cli(monkeypatch, gone)
    settings = get_settings()
    with pytest.raises(AppException) as raised:
        claude_runner.run_agent(
            settings, settings.project("demo"), "worker", AgentRole.WORKER, "x", repository, WorkerResult, "t1", "old"
        )
    assert raised.value.error_code == AppErrorCode.SESSION_LOST and lost in str(raised.value)


def test_rules_are_pinned_and_a_new_project_also_gets_the_stack_topic(workspace: Path) -> None:
    make_repository(workspace, with_remote=False)
    settings = get_settings()
    project = settings.project("demo")
    rules, topics = claude_runner._rules_prompt(settings, project, ["core:rule-precedence"])
    for heading in ("## Skill core:rule-precedence", "## Skill core:engineering", "## Skill python:python-rules"):
        assert heading in rules
    assert topics == ["core-rules", "python-style"] and "## 3. Naming" in rules and "## 1. Stack" not in rules
    rules, topics = claude_runner._rules_prompt(settings, project, [], new_project=True)
    assert topics == ["core-rules", "python-style", "python-stack", "service-architecture"] and "## 1. Stack" in rules


def test_a_review_gets_the_rules_that_cover_the_changed_files(workspace: Path) -> None:
    make_repository(workspace, with_remote=False)
    settings = get_settings()
    changed = ["src/app/routes/items.py", "alembic/versions/001_add_items.py", "README.md"]
    rules, topics = claude_runner._rules_prompt(settings, settings.project("demo"), ["core:pr-review"], changed)
    assert topics == [
        "core-rules",
        "python-style",
        "git-and-pr",
        "review-checks",
        "service-architecture",
        "data-and-migrations",
    ]
    for heading in (
        "## Skill core:pr-review",
        "## 3. The pull request",
        "## The checks",
        "## 2. Layers",
        "## 4. Migrations",
    ):
        assert heading in rules
    assert "# Nexttech agents" not in rules


def test_a_worker_may_narrow_the_checks_to_a_path_but_not_run_any_script(workspace: Path) -> None:
    repository = make_repository(workspace, with_remote=False)
    (repository / "tests").mkdir()
    settings = get_settings()
    project = settings.project("demo")
    project.commands.lint = ".venv/bin/ruff check tests"
    project.commands.test = "python run_tests.py"
    allow = claude_runner._build_run_settings(settings, project, AgentRole.WORKER)["permissions"]["allow"]
    assert "Bash(.venv/bin/ruff check:*)" in allow and "Bash(.venv/bin/ruff check tests:*)" in allow
    assert "Bash(python run_tests.py:*)" in allow and "Bash(python:*)" not in allow
    assert "Bash(tail:*)" in allow
    assert claude_runner._build_run_settings(settings, project, AgentRole.REVIEWER)["permissions"]["allow"] == []
