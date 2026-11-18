import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import git

FAKE_CLAUDE = '''#!/usr/bin/env python3
"""Stands in for the Claude Code CLI: answers each agent with a fixed structured result, in stream-json."""
import json
import pathlib
import sys

agent = sys.argv[sys.argv.index("--agent") + 1]
prompt = sys.stdin.read()
if agent == "planner":
    tasks = [{"id": f"S{n}", "title": f"Step {n}", "instructions": "Done when: it works", "files": []} for n in (1, 2)]
    answer = {"questions": [], "split": [], "plan": "- build it", "options_considered": "", "files": [], "answers": []}
    answer["subtasks"] = tasks
elif agent == "worker":
    step = len(list(pathlib.Path(".").glob("step_*.txt"))) + 1
    pathlib.Path(f"step_{step}.txt").write_text("done")
    answer = {"status": "done", "summary": f"step {step}", "commit_message": "feat: build it", "reason": ""}
    answer |= {"files": [f"step_{step}.txt"], "handoff": "", "questions": [], "project_notes": []}
else:
    answer = {"verdict": "approve", "summary": "fine", "comments": []}
tool_call = {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}
print(json.dumps({"type": "assistant", "message": {"content": [tool_call]}}))
print(json.dumps({"type": "result", "is_error": False, "structured_output": answer, "session_id": "s", "num_turns": 2}))
'''


def test_a_project_goes_from_an_idea_to_a_merged_commit_through_the_command_line(workspace: Path) -> None:
    fake = workspace / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    config_file = Path(os.environ["CW_HOME"]) / "config.json"
    config = json.loads(config_file.read_text())
    config["claude_binary"] = str(fake)
    config_file.write_text(json.dumps(config))

    def cw(*arguments: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-c", "from cw.cli import main; main()", *arguments]
        return subprocess.run(command, capture_output=True, text=True, check=True)

    created = cw("new", "reminders", "Remind me when the car insurance expires", "--path", str(workspace))
    assert "planner · Read README.md" in created.stderr and "awaiting_approval" in created.stderr
    ticket = json.loads(cw("status", "--json").stdout)[0]["id"]

    approved = cw("approve", ticket).stderr
    assert "ready" in approved and "Task S2 · Step 2 (worker W1)" in approved
    events = json.loads(cw("events", ticket, "--json").stdout)
    started = [event["label"] for event in events if event["kind"] == "start"]
    assert started == ["planner", "W1 · S1", "W1 · S2"]
    assert {"tool", "done", "check"} <= {event["kind"] for event in events}
    assert len(json.loads(cw("events", ticket, "--json", "--last", "2").stdout)) == 2
    assert "merge it with: cw done" in cw("pr", ticket).stderr
    cw("done", ticket)

    project = workspace / "reminders"
    assert sorted(path.name for path in project.glob("step_*.txt")) == ["step_1.txt", "step_2.txt"]
    assert git(project, "log", "--format=%s") == "feat: build it\nchore: start project"
    assert git(project, "status", "--porcelain") == ""
    shown = json.loads(cw("show", ticket, "--json").stdout)
    assert shown["status"] == "done" and len(shown["runs"]) == 4
    assert [run["label"] for run in shown["runs"]] == ["planner", "W1 · S1", "W1 · S2", "reviewer"]
    assert {row["check"] for row in json.loads(cw("doctor", "--json").stdout)} >= {"binary git"}
    shown_text = cw("show", ticket).stdout
    assert (
        "### [x] S2 · Step 2 (worker W1)" in shown_text and "- Solved by W1: step 2\n- Files: step_2.txt" in shown_text
    )


def test_project_map_command(local_repository: Path) -> None:
    (local_repository / "app.py").write_text("class DemoService:\n    pass\n")
    command = [sys.executable, "-c", "from cw.cli import main; main()", "project", "map", "demo", "--json"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    parsed = json.loads(result.stdout)
    assert parsed["project"] == "demo"
    assert parsed["lines"] >= 2
