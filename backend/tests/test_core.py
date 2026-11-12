from pathlib import Path

from cw.models import Route, Subtask, Ticket, TicketStatus, utc_now
from cw.pipeline import next_task
from cw.tickets import looks_like_ticket_id, new_ticket_id, read_ticket


def _ticket(tmp_path: Path) -> Ticket:
    now = utc_now()
    return Ticket(
        id=new_ticket_id("Fix: the login --- timeout"),
        project="demo",
        title="Fix the login timeout",
        status=TicketStatus.READY,
        route=Route.PLANNED,
        branch="cw/x",
        base_branch="main",
        previous_branch="main",
        base_sha="abc123",
        owner="me",
        toolkit_version="0.1.0",
        created_at=now,
        updated_at=now,
        plan="## Heading\n\n---\n\nbody",
        subtasks=[Subtask(id=f"S{i}", title=f"task {i}", instructions="", files=[]) for i in (1, 2, 3)],
    )


def test_next_task_walks_the_list_in_order_and_ends(tmp_path: Path) -> None:
    ticket = _ticket(tmp_path)
    seen = []
    while (task := next_task(ticket)) is not None:
        seen.append(task.id)
        ticket.tasks_done.append(task.id)
    assert seen == ["S1", "S2", "S3"]


def test_ticket_file_round_trips(tmp_path: Path) -> None:
    ticket = _ticket(tmp_path)
    path = tmp_path / "ticket.json"
    path.write_text(ticket.model_dump_json())
    assert read_ticket(path) == ticket
    assert looks_like_ticket_id(ticket.id)
