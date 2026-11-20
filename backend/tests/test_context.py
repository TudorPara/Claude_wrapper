import json
import os
from datetime import timedelta
from pathlib import Path

import numpy
import pytest
from conftest import FakeAgents, FakeHost, FakeOllama, answer, git, make_repository

from cw import (
    benchmark,
    claude_runner,
    cli,
    context_engine,
    context_eval,
    context_index,
    embeddings,
    onboarding,
    pipeline,
    storage,
)
from cw.claude_runner import STRUCTURED_OUTPUT_TOOL
from cw.code_chunks import ChunkKind, parse_file, split_terms
from cw.config import AgentRole, GitHostProvider, context_db_path, get_settings
from cw.context_index import EmbeddingMode, TicketRecord, opened_index
from cw.context_search import (
    ALL_CHANNELS,
    Channel,
    SeedFile,
    SeedSymbol,
    build_seed,
    dependency_graph,
    personalized_pagerank,
    render_seed,
)
from cw.errors import AppErrorCode, AppException
from cw.models import ReviewComment, ReviewerResult, Severity, TicketStatus, WorkerStatus, utc_now
from cw.project_memory import NOTES_HEADING
from cw.tickets import append_event, events_path, find_ticket, project_lock, read_events, ticket_path, tickets_root
from cw.toolkit import load_agent

FILES = {
    "app/__init__.py": "",
    "app/errors.py": '"""Errors every module raises."""\n\n\nclass AppError(Exception):\n    """Base error."""\n',
    "app/services/__init__.py": "",
    "app/services/accounts.py": (
        '"""Account operations that move money."""\n\nfrom app.errors import AppError\n'
        "from app.models.currency import format_amount_minor\n\n\n"
        "def transfer_funds(source_id, target_id, *, amount_minor):\n"
        '    """Moves money between two accounts."""\n'
        "    if amount_minor <= 0:\n        raise AppError()\n    return format_amount_minor(amount_minor)\n"
    ),
    "app/models/__init__.py": "",
    "app/models/currency.py": (
        '"""Currencies and minor units."""\n\nfrom app.errors import AppError\n\n\n'
        "def format_amount_minor(amount_minor, currency='EUR'):\n"
        '    """Formats an integer amount."""\n    if amount_minor is None:\n        raise AppError()\n'
        "    return f'{amount_minor / 100:.2f} {currency}'\n"
    ),
    "app/routes/__init__.py": "",
    "app/routes/reports.py": (
        '"""Monthly report endpoints."""\n\nfrom app.errors import AppError\n\n\n'
        'def monthly_report(month):\n    """Builds the monthly report."""\n    raise AppError()\n'
    ),
    "node_modules/lib/index.js": "export const ignored = 1;\n",
    "secrets/keys.py": "API_KEY = 'secret'\n",
}


def run_cli(*arguments: str) -> None:
    parsed = cli._parser().parse_args(list(arguments))
    parsed.handler(parsed)


def write_files(repository: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (repository / path).parent.mkdir(parents=True, exist_ok=True)
        (repository / path).write_text(text)


def commit_all(repository: Path, message: str) -> str:
    git(repository, "add", "-A")
    git(repository, "commit", "-m", message)
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def code_repository(workspace: Path) -> Path:
    repository = make_repository(workspace, with_remote=False)
    write_files(repository, FILES)
    commit_all(repository, "code")
    return repository


def update(commit: str = "HEAD", mode: EmbeddingMode = EmbeddingMode.ALL, table: str = "snapshot"):
    settings = get_settings()
    project = settings.project("demo")
    with opened_index("demo") as connection:
        return context_index.update_snapshot(
            connection, project, settings, git(project.path, "rev-parse", commit), table, mode
        )


def seed_for(request: str, channels=ALL_CHANNELS, before=None, exclude=frozenset(), hyde=None):
    with opened_index("demo") as connection:
        return build_seed(
            connection, get_settings(), request, frozenset(channels), before or utc_now(), set(exclude), hyde
        )


def set_context(**values) -> None:
    config_file = Path(os.environ["CW_HOME"]) / "config.json"
    config = json.loads(config_file.read_text())
    for key, value in values.items():
        group, _, name = key.partition("__")
        target = config.setdefault("context", {})
        if group == "embeddings":
            target = target.setdefault("embeddings", {})
        elif group == "storage":
            target = config.setdefault("storage", {})
        target[name or group] = value
    config_file.write_text(json.dumps(config))
    get_settings.cache_clear()


def test_terms_split_identifiers_and_drop_stop_words() -> None:
    assert split_terms("transfer_funds ParseHTTPResponse v2 the a") == [
        "transfer_funds",
        "transfer",
        "funds",
        "parsehttpresponse",
        "parse",
        "http",
        "response",
        "v2",
    ]


def test_python_files_become_chunks_with_full_signatures_and_resolved_imports() -> None:
    source = (
        '"""Statements."""\nfrom ..models.currency import format_amount_minor\nfrom . import helpers\n\n'
        'class Exporter(Base):\n    """Writes."""\n'
        "    async def to_csv(self, rows, /, sep, *args, archived=False, **kw):\n"
        "        return format_amount_minor(rows)\n"
    )
    parsed = parse_file("app/services/statements.py", source, 6000)
    kinds = [(chunk.kind, chunk.qualified_name) for chunk in parsed.chunks]
    assert kinds == [
        (ChunkKind.FILE, "app/services/statements.py"),
        (ChunkKind.CLASS, "Exporter"),
        (ChunkKind.METHOD, "Exporter.to_csv"),
    ]
    assert parsed.chunks[2].signature == "async def to_csv(self, rows, /, sep, *args, archived=False, **kw)"
    assert "app.models.currency" in parsed.imports and "app.services.helpers" in parsed.imports
    assert [chunk.kind for chunk in parse_file("broken.py", "def (", 6000).chunks] == [ChunkKind.FILE]


def test_an_identifier_is_one_search_term_and_its_parts_still_match(code_repository: Path) -> None:
    update()
    with opened_index("demo") as connection:
        whole = context_index.lexical_chunks(connection, ["transfer_funds"], 10)
        part = context_index.lexical_chunks(connection, ["funds"], 10)
    assert whole[0][0] == "app/services/accounts.py" and part[0][0] == "app/services/accounts.py"


def test_only_changed_blobs_are_parsed_and_a_rename_parses_nothing(code_repository: Path) -> None:
    assert update().blobs_parsed > 0
    (code_repository / "app/routes/reports.py").write_text('"""Changed."""\n')
    commit_all(code_repository, "change one file")
    assert update().blobs_parsed == 1
    git(code_repository, "mv", "app/errors.py", "app/failures.py")
    commit_all(code_repository, "rename")
    assert update().blobs_parsed == 0


def test_ignored_folders_and_protected_paths_never_enter_the_index(code_repository: Path) -> None:
    update()
    with opened_index("demo") as connection:
        paths = context_index.snapshot_paths(connection)
    assert "app/services/accounts.py" in paths
    assert not any(path.startswith(("node_modules/", "secrets/")) for path in paths)


def test_a_project_in_a_subfolder_is_indexed_with_paths_relative_to_it(workspace: Path) -> None:
    outer = workspace / "mono"
    write_files(outer, {"README.md": "mono\n", "svc/app/main.py": "def main():\n    return 1\n"})
    git(outer, "init", "-b", "main")
    commit_all(outer, "mono")
    onboarding.add_project(outer / "svc", "svc", GitHostProvider.GITHUB, onboard=False)
    settings = get_settings()
    project = settings.project("svc")
    with opened_index("svc") as connection:
        context_index.update_snapshot(connection, project, settings, git(outer, "rev-parse", "HEAD"))
        assert context_index.snapshot_paths(connection) == ["app/main.py"]


def test_a_request_naming_a_function_ranks_its_file_first(code_repository: Path) -> None:
    update()
    seed = seed_for("fix transfer_funds for negative amounts", {Channel.LEXICAL, Channel.PATH, Channel.GRAPH})
    assert seed.ranking[0] == "app/services/accounts.py"
    assert "def transfer_funds(source_id, target_id, *, amount_minor)" in seed.text


def test_pagerank_matches_a_hand_computed_graph_and_falls_back_to_uniform() -> None:
    edges = {"a": {"b": 1.0}, "b": {"c": 1.0}, "c": {"a": 1.0}}
    uniform = personalized_pagerank(edges, ["a", "b", "c"], {}, 0.85, 100)
    assert all(abs(score - 1 / 3) < 1e-6 for score in uniform.values())
    focused = personalized_pagerank({"a": {"b": 1.0}}, ["a", "b"], {"a": 1.0}, 0.5, 100)
    assert abs(focused["a"] - 2 / 3) < 1e-6 and abs(focused["b"] - 1 / 3) < 1e-6


def test_a_hub_every_file_imports_does_not_outrank_the_file_that_matched(code_repository: Path) -> None:
    update()
    seed = seed_for("format_amount_minor rounding", {Channel.LEXICAL, Channel.PATH, Channel.GRAPH})
    assert seed.ranking.index("app/models/currency.py") < seed.ranking.index("app/errors.py")


def test_the_graph_links_names_to_their_definitions_and_imports_to_modules() -> None:
    edges = dependency_graph(
        [("b.py", "helper")], [("a.py", "helper")], [("a.py", "pkg.c")], ["a.py", "b.py", "pkg/c.py"], 2.0, 0.0
    )
    assert edges["a.py"] == {"b.py": 1.0, "pkg/c.py": 2.0} and "b.py" not in edges


def test_a_caller_of_the_matched_definition_rises_through_the_reverse_edge() -> None:
    paths = ["services/accounts.py", "routes/transfers.py"]
    calls = ([("services/accounts.py", "transfer")], [("routes/transfers.py", "transfer")], [], paths)
    forward = personalized_pagerank(dependency_graph(*calls, 2.0, 0.0), paths, {"services/accounts.py": 1.0}, 0.85, 30)
    both = personalized_pagerank(dependency_graph(*calls, 2.0, 0.5), paths, {"services/accounts.py": 1.0}, 0.85, 30)
    assert forward["routes/transfers.py"] == 0 and both["routes/transfers.py"] > 0


def test_the_seed_keeps_whole_blocks_within_its_budget_and_is_deterministic(code_repository: Path) -> None:
    files = [
        SeedFile(
            path=f"f{index}.py",
            docstring="doc",
            symbols=[SeedSymbol(start_line=1, end_line=2, signature="def x()", qualified_name="x", docstring="")],
        )
        for index in range(10)
    ]
    text, kept = render_seed("", files, [], 120, 40)
    assert len(text) <= 120 and all(text.count(file.path) == 1 for file in kept) and len(kept) < 10
    update()
    assert seed_for("transfer money").text == seed_for("transfer money").text


def test_the_planner_gets_the_seed_and_the_layout_instead_of_the_repo_map(
    code_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "fix transfer_funds for negative amounts", plan_first=True)
    planner_prompt = agents.prompts_for(AgentRole.PLANNER)[0]
    assert "## Code likely relevant to this request" in planner_prompt
    assert "app/services/accounts.py" in planner_prompt and "Layout (folder, code files):" in planner_prompt
    assert "## Codebase architecture map" not in planner_prompt
    assert context_db_path("demo").exists() and "app/services/accounts.py" in find_ticket(ticket.id).seed_files
    assert any(warning.startswith("Context: semantic search is incomplete") for warning in ticket.warnings)


def test_a_corrupt_index_gives_a_warning_and_the_planner_gets_the_repo_map(
    code_repository: Path, agents: FakeAgents
) -> None:
    context_db_path("demo").parent.mkdir(parents=True, exist_ok=True)
    context_db_path("demo").write_bytes(b"this is not a database" * 100)
    ticket = pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    planner_prompt = agents.prompts_for(AgentRole.PLANNER)[0]
    assert "## Codebase architecture map" in planner_prompt
    assert "## Code likely relevant" not in planner_prompt
    assert any(warning.startswith("Context:") for warning in ticket.warnings)
    assert ticket.status == TicketStatus.AWAITING_APPROVAL


def test_finish_indexes_the_tip_of_the_base_and_stores_a_record_but_abandon_does_not(
    code_repository: Path, agents: FakeAgents
) -> None:
    git(code_repository, "switch", "-c", "elsewhere")
    git(code_repository, "switch", "main")
    kept = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(kept.id)
    pipeline.finish(kept.id, abandon=False)
    dropped = pipeline.start_ticket("demo", "another file", plan_first=False)
    pipeline.finish(dropped.id, abandon=True)
    with opened_index("demo") as connection:
        records = {record.ticket_id: record for record in context_index.all_records(connection)}
        indexed = context_index.meta_value(connection, context_index.META_COMMIT)
    assert set(records) == {kept.id} and records[kept.id].changed_files == ["work_1.txt"]
    assert indexed == git(code_repository, "rev-parse", "main")


def test_changed_files_leave_out_what_the_base_brought_in(
    remote_repository: Path, workspace: Path, agents: FakeAgents, host: FakeHost
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    git(workspace, "clone", str(workspace / "origin.git"), "teammate")
    write_files(workspace / "teammate", {"b.py": "B = 1\n"})
    commit_all(workspace / "teammate", "teammate")
    git(workspace / "teammate", "push", "origin", "main")
    pipeline.open_pull_request(ticket.id)
    assert (remote_repository / "b.py").exists()
    merged = pipeline.merge_pull_request(ticket.id)
    assert merged.changed_files == ["work_1.txt"]


def _record(ticket_id: str, finished_at, changed: list[str], findings=(), notes=()) -> TicketRecord:
    return TicketRecord(
        ticket_id=ticket_id,
        title=f"Add CSV export {ticket_id}",
        finished_at=finished_at,
        request="add a csv export of account statements",
        plan_excerpt="",
        changed_files=changed,
        open_findings=list(findings),
        notes=list(notes),
    )


def test_history_uses_only_earlier_tickets_without_the_current_one_and_shows_findings_on_seed_files(
    code_repository: Path,
) -> None:
    update()
    now = utc_now()
    finding = context_index.RecordFinding(
        path="app/routes/reports.py", line=3, severity="minor", summary="ignores currency"
    )
    elsewhere = context_index.RecordFinding(path="docs/x.md", line=1, severity="minor", summary="not shown")
    with opened_index("demo") as connection:
        context_index.write_record(
            connection,
            _record(
                "old",
                now - timedelta(days=2),
                ["app/routes/reports.py"],
                [finding, elsewhere],
                ["exports go through reports"],
            ),
        )
        context_index.write_record(connection, _record("future", now + timedelta(days=1), ["app/errors.py"]))
        context_index.write_record(connection, _record("current", now - timedelta(days=1), ["app/models/currency.py"]))
        context_index.write_record(
            connection,
            _record(
                "old",
                now - timedelta(days=2),
                ["app/routes/reports.py"],
                [finding, elsewhere],
                ["exports go through reports"],
            ),
        )
        assert context_index.counts(connection)["ticket_records"] == 3
    seed = seed_for("add a csv export of account statements", {Channel.HISTORY}, now, {"current"})
    assert [record.ticket_id for record in seed.tickets] == ["old"]
    assert seed.ranking == ["app/routes/reports.py"]
    assert "open finding left: app/routes/reports.py:3 minor — ignores currency" in seed.text
    assert "not shown" not in seed.text and "note: exports go through reports" in seed.text


def test_queries_carry_the_instruction_documents_do_not_and_vectors_are_cut_to_unit_length(
    code_repository: Path, ollama: FakeOllama
) -> None:
    settings = get_settings()
    vector = embeddings.encode_query(settings, "find the export")
    assert ollama.batches[-1] == [f"Instruct: {settings.context.embeddings.query_instruction}\nQuery:find the export"]
    assert ollama.payloads[-1]["options"] == {"num_ctx": settings.context.embeddings.context_length}
    documents = embeddings.encode_documents(settings, [f"text {index}" for index in range(70)])
    assert [len(batch) for batch in ollama.batches[-3:]] == [32, 32, 6] and ollama.batches[-1][-1] == "text 69"
    assert vector.shape == (1024,) and abs(float(numpy.linalg.norm(documents[0])) - 1) < 1e-5


def test_vectors_are_cached_and_a_model_or_dimension_change_embeds_again(
    code_repository: Path, ollama: FakeOllama
) -> None:
    first = update()
    assert first.chunks_embedded > 0 and update().chunks_embedded == 0
    set_context(embeddings__dimensions=512)
    assert update().chunks_embedded == first.chunks_embedded


def test_an_automatic_update_embeds_only_when_few_files_lack_vectors(code_repository: Path) -> None:
    assert update(mode=EmbeddingMode.NONE).embedding_skipped
    set_context(embeddings__max_incremental_files=1)
    assert update(mode=EmbeddingMode.AUTOMATIC).embedding_skipped
    assert update(mode=EmbeddingMode.AUTOMATIC).embedding_skipped
    set_context(embeddings__max_incremental_files=100)
    assert update(mode=EmbeddingMode.AUTOMATIC).chunks_embedded > 0
    set_context(embeddings__max_incremental_files=1)
    path = code_repository / "app/routes/reports.py"
    path.write_text(path.read_text() + "\n\ndef yearly_report(year):\n    return year\n")
    commit_all(code_repository, "one more function")
    result = update(mode=EmbeddingMode.AUTOMATIC)
    assert not result.embedding_skipped and result.chunks_embedded > 0


def test_the_second_ticket_start_does_not_embed_the_whole_repository(
    code_repository: Path, agents: FakeAgents, ollama: FakeOllama
) -> None:
    set_context(embeddings__max_incremental_files=2)
    first = pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    pipeline.finish(first.id, abandon=True)
    batches = len(ollama.batches)
    second = pipeline.start_ticket("demo", "fix format_amount_minor", plan_first=True)
    embedded = sum(len(batch) for batch in ollama.batches[batches:])
    assert embedded <= 2
    assert any('run "cw context build demo" once' in warning for warning in second.warnings)


def test_ollama_down_at_finish_still_stores_the_record_with_its_notes(
    code_repository: Path, agents: FakeAgents, ollama: FakeOllama
) -> None:
    agents.worker = lambda prompt, cwd, call: (
        (cwd / "app/new_module.py").write_text("X = 1\n"),
        answer(WorkerStatus.DONE, "done", project_notes=["Money goes through transfer_funds."]),
    )[1]
    ticket = pipeline.start_ticket("demo", "add a module", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    ollama.down = True
    pipeline.finish(ticket.id, abandon=False)
    with opened_index("demo") as connection:
        records = context_index.all_records(connection)
    assert [record.notes for record in records] == [["Money goes through transfer_funds."]]


def test_a_rebuild_keeps_the_ticket_records(code_repository: Path) -> None:
    update()
    with opened_index("demo") as connection:
        context_index.write_record(connection, _record("notes-import", utc_now(), [], notes=["keep me"]))
    assert context_index.reset_index("demo") == 1
    with opened_index("demo") as connection:
        assert [record.notes for record in context_index.all_records(connection)] == [["keep me"]]
        assert context_index.counts(connection)["files"] == 0


def test_a_dry_run_gc_and_status_work_while_a_ticket_holds_the_project(code_repository: Path) -> None:
    update()
    settings = get_settings()
    with project_lock("demo", "busy-ticket"):
        report = storage.collect_garbage(settings, settings.project("demo"), dry_run=True)
        run_cli("context", "status", "demo", "--json")
        run_cli("gc")
    assert report.dry_run


def test_an_unreadable_ticket_file_does_not_stop_event_log_pruning(local_repository: Path) -> None:
    (tickets_root() / "demo").mkdir(parents=True, exist_ok=True)
    (tickets_root() / "demo" / "broken.json").write_text("{not json")
    assert storage.prune_event_logs(get_settings(), "demo") == []


def test_the_layout_counts_against_the_seed_budget() -> None:
    symbol = SeedSymbol(start_line=1, end_line=2, signature="def x()", qualified_name="x", docstring="")
    files = [SeedFile(path=f"f{index}.py", docstring="doc", symbols=[symbol]) for index in range(5)]
    layout = "Layout (folder, code files):\n" + "\n".join(f"  folder{index}/ 1" for index in range(5))
    text, kept = render_seed(layout, files, [], len(layout) + 60, 40)
    assert len(text) <= len(layout) + 60 and len(kept) < 5


def test_part_of_the_budget_is_kept_for_similar_tickets_and_their_notes() -> None:
    symbol = SeedSymbol(start_line=1, end_line=9, signature="def transfer(a, b)", qualified_name="t", docstring="")
    files = [SeedFile(path=f"app/m{index}.py", docstring="x" * 700, symbols=[symbol]) for index in range(15)]
    record = _record("t1", utc_now(), ["app/m0.py"], notes=["exports go through StatementExporter"])
    files_only, _ = render_seed("", files, [], 10_000, 2000)
    assert len(files_only) > 9000
    text, kept = render_seed("", files, [record], 10_000, 2000)
    assert "note: exports go through StatementExporter" in text and len(text) <= 10_000 and len(kept) < 15


def test_query_terms_and_keyword_results_have_separate_caps(code_repository: Path) -> None:
    set_context(max_query_terms=1, lexical_top_k=60)
    update()
    seed = seed_for("monthly_report transfer_funds", {Channel.LEXICAL})
    assert seed.ranking == ["app/routes/reports.py"]


def test_the_dense_channel_finds_a_file_that_shares_no_word_with_the_request(
    code_repository: Path, ollama: FakeOllama
) -> None:
    settings = get_settings()
    update()
    with opened_index("demo") as connection:
        chunks = [chunk for chunk in context_index.snapshot_chunks(connection) if chunk.path == "app/routes/reports.py"]
    request = "summaries per calendar period"
    query = settings.context.embeddings.query_template.format(
        instruction=settings.context.embeddings.query_instruction, query=request
    )
    ollama.same[query] = context_index.chunk_embedding_text(chunks[-1])
    seed = seed_for(request, {Channel.DENSE})
    assert seed.ranking[0] == "app/routes/reports.py"


def test_ollama_down_gives_a_seed_from_the_other_channels_but_stops_a_build(
    code_repository: Path, ollama: FakeOllama
) -> None:
    update(mode=EmbeddingMode.NONE)
    ollama.down = True
    seed = seed_for("fix transfer_funds")
    assert seed.ranking[0] == "app/services/accounts.py" and seed.warnings
    with pytest.raises(AppException) as raised:
        embeddings.check_embeddings(get_settings())
    assert raised.value.error_code == AppErrorCode.CONTEXT_INDEX_FAILED


def test_a_model_that_is_not_pulled_is_reported(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setattr(embeddings, "_request", lambda settings, path, payload=None: {"models": []})
    with pytest.raises(AppException, match="ollama pull"):
        embeddings.check_embeddings(get_settings())


def test_the_hyde_sketch_feeds_keywords_and_its_own_dense_channel_and_runs_once(
    code_repository: Path, agents: FakeAgents, ollama: FakeOllama
) -> None:
    update()
    agents.sketcher = lambda prompt: context_engine.HydeSketch(
        paths=[], symbols=["monthly_report"], code="def monthly_report(): ..."
    )
    ticket = pipeline.start_ticket("demo", "give me the numbers of last month", plan_first=True)
    assert "app/routes/reports.py" in find_ticket(ticket.id).seed_files
    assert "def monthly_report(): ..." in [text for batch in ollama.batches for text in batch]
    context_runs = [run for run in find_ticket(ticket.id).runs if run.role == AgentRole.CONTEXT]
    assert len(context_runs) == 1
    sketch_cwd = agents.cwds[[role for role, _ in agents.prompts].index(AgentRole.CONTEXT)]
    assert code_repository not in sketch_cwd.parents and sketch_cwd != code_repository
    pipeline.follow_up(ticket.id, "use the calendar month")
    assert len([run for run in find_ticket(ticket.id).runs if run.role == AgentRole.CONTEXT]) == 1


def test_a_failing_sketch_still_gives_a_seed(code_repository: Path, agents: FakeAgents) -> None:
    def broken(prompt: str):
        raise AppException(AppErrorCode.AGENT_FAILED, "sketch broke")

    agents.sketcher = broken
    ticket = pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    assert "app/services/accounts.py" in find_ticket(ticket.id).seed_files
    assert any("HyDE sketch failed" in warning for warning in find_ticket(ticket.id).warnings)


def test_the_context_role_runs_bare(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = []

    def run(command, cwd, environment, timeout_minutes, stdin_text=None, on_line=None):
        commands.append((command, cwd))
        result = {"type": "result", "is_error": False, "structured_output": {"paths": [], "symbols": [], "code": ""}}
        return 0, json.dumps(result), ""

    monkeypatch.setattr(claude_runner, "run_process", run)
    repository = make_repository(workspace, with_remote=False)
    settings = get_settings()
    context_engine.sketch(settings, settings.project("demo"), "t1", "do it", "")
    command, cwd = commands[0]
    assert "--append-system-prompt-file" not in command and "--plugin-dir" not in command
    assert cwd != repository and repository not in Path(cwd).parents
    assert command[command.index("--agent") + 1] == context_engine.CONTEXT_AGENT
    assert load_agent(settings, context_engine.CONTEXT_AGENT).tools == [STRUCTURED_OUTPUT_TOOL]


def test_a_direct_ticket_gets_the_seed_once_and_an_escalation_reuses_it(
    code_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "fix transfer_funds for negative amounts", plan_first=False)
    (first,) = agents.prompts_for(AgentRole.WORKER)
    assert "## Code likely relevant to this request" in first
    pipeline.follow_up(ticket.id, "also log it")
    assert "## Code likely relevant" not in agents.prompts_for(AgentRole.WORKER)[-1]

    agents.worker = lambda prompt, cwd, call: answer(WorkerStatus.NEEDS_PLAN, "", reason="too big")
    git(code_repository, "add", "-A")
    git(code_repository, "commit", "-m", "park the first ticket's work")
    big = pipeline.start_ticket("demo", "rework all of transfer_funds", plan_first=False)
    context_runs = [run for run in find_ticket(big.id).runs if run.role == AgentRole.CONTEXT]
    assert len(context_runs) == 1 and find_ticket(big.id).status == TicketStatus.AWAITING_APPROVAL
    assert "## Code likely relevant" in agents.prompts_for(AgentRole.PLANNER)[-1]


def test_eval_scores_tickets_without_created_files_and_keeps_the_real_snapshot(
    code_repository: Path, agents: FakeAgents
) -> None:
    def edits_accounts(prompt: str, cwd: Path, call: int):
        path = cwd / "app/services/accounts.py"
        path.write_text(path.read_text() + "\n\ndef audit_transfer():\n    return None\n")
        (cwd / f"new_{call}.py").write_text("X = 1\n")
        return answer(WorkerStatus.DONE, "done", files=["app/services/accounts.py"])

    agents.worker = edits_accounts
    ticket = pipeline.start_ticket("demo", "add an audit to transfer_funds", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    pipeline.finish(ticket.id, abandon=False)
    with opened_index("demo") as connection:
        before = dict(connection.execute("SELECT path, blob FROM snapshot").fetchall())
    settings = get_settings()
    report = context_eval.evaluate(
        settings,
        settings.project("demo"),
        [context_eval.Method.LEXICAL, context_eval.Method.PLANNER],
        None,
        lambda n: True,
    )
    assert report.samples == 1 and report.dropped_truth_files == 1
    lexical = next(score for score in report.scores if score.method == context_eval.Method.LEXICAL)
    assert lexical.recall[5] == 1.0 and lexical.mrr == 1.0
    with opened_index("demo") as connection:
        assert dict(connection.execute("SELECT path, blob FROM snapshot").fetchall()) == before


def test_eval_from_commits_uses_parents_and_skips_commits_cw_made(code_repository: Path) -> None:
    path = code_repository / "app/models/currency.py"
    path.write_text(path.read_text() + "\n\ndef round_minor(amount_minor):\n    return amount_minor\n")
    sha = commit_all(code_repository, "Round amounts in format_amount_minor")
    settings = get_settings()
    samples = context_eval.commit_samples(settings.project("demo"), settings, 5)
    assert samples[0].sample_id == sha and samples[0].truth == ["app/models/currency.py"]
    assert samples[0].base == git(code_repository, "rev-parse", f"{sha}^")
    report = context_eval.evaluate(settings, settings.project("demo"), [context_eval.Method.LEXICAL], 5, lambda n: True)
    assert report.source == "commit" and report.scores[0].samples >= 1


def test_gc_keeps_every_retained_tree_and_frees_the_rest(code_repository: Path) -> None:
    update()
    git(code_repository, "switch", "-c", "develop")
    write_files(code_repository, {"app/develop_only.py": "def only_on_develop():\n    return 1\n"})
    commit_all(code_repository, "develop work")
    update("develop")
    git(code_repository, "switch", "main")
    update("main")
    settings = get_settings()
    project = settings.project("demo")
    dry = storage.collect_garbage(settings, project, dry_run=True)
    real = storage.collect_garbage(settings, project)
    assert dry.blobs_deleted == real.blobs_deleted == 1 and real.vectors_deleted > 0
    assert update("develop").chunks_embedded > 0


def test_gc_keeps_an_open_tickets_base_and_removes_old_vectors_but_keeps_records(
    code_repository: Path, agents: FakeAgents
) -> None:
    git(code_repository, "switch", "-c", "develop")
    write_files(code_repository, {"app/develop_only.py": "def only_on_develop():\n    return 1\n"})
    commit_all(code_repository, "develop work")
    git(code_repository, "switch", "main")
    update("develop")
    ticket = pipeline.start_ticket("demo", "work on develop", plan_first=False, base="develop")
    update("main")
    with opened_index("demo") as connection:
        context_index.write_record(connection, _record("kept", utc_now(), ["app/errors.py"]))
        connection.execute("INSERT INTO embeddings VALUES ('old-model@8', 'x', ?)", (b"\0" * 32,))
        connection.commit()
    settings = get_settings()
    storage.collect_garbage(settings, settings.project("demo"))
    with opened_index("demo") as connection:
        assert connection.execute("SELECT COUNT(*) FROM embeddings WHERE model_key = 'old-model@8'").fetchone()[0] == 0
        assert context_index.counts(connection)["ticket_records"] >= 1
    assert update("develop").chunks_embedded == 0
    update("main")
    git(code_repository, "add", "-A")
    git(code_repository, "commit", "-m", "done")
    pipeline.finish(ticket.id, abandon=True)
    report = storage.collect_garbage(settings, settings.project("demo"))
    assert report.blobs_deleted >= 1


def test_gc_shrinks_the_file_and_a_missing_ref_is_skipped(code_repository: Path) -> None:
    update()
    write_files(
        code_repository,
        {f"app/bulk_{index}.py": f"def bulk_{index}():\n    return {index}\n" * 50 for index in range(30)},
    )
    commit_all(code_repository, "bulk")
    update()
    git(code_repository, "reset", "--hard", "HEAD~1")
    update()
    settings = get_settings()
    project = settings.project("demo")
    before = context_db_path("demo").stat().st_size
    report = storage.collect_garbage(settings, project)
    assert report.size_after_bytes < before == report.size_before_bytes


def test_event_logs_expire_only_for_closed_tickets(local_repository: Path, agents: FakeAgents) -> None:
    closed = pipeline.start_ticket("demo", "add a file", plan_first=False)
    git(local_repository, "add", "-A")
    git(local_repository, "commit", "-m", "keep")
    pipeline.finish(closed.id, abandon=False)
    still_open = pipeline.start_ticket("demo", "another", plan_first=False)
    for ticket_id in (closed.id, still_open.id):
        stored = find_ticket(ticket_id)
        stored.updated_at = utc_now() - timedelta(days=90)
        ticket_path("demo", ticket_id).write_text(stored.model_dump_json())
    settings = get_settings()
    removed = storage.prune_event_logs(settings, "demo")
    assert removed == [events_path("demo", closed.id)] and events_path("demo", still_open.id).exists()
    assert read_events("demo", closed.id, 10) == []


def test_a_big_index_warns_at_ticket_start(code_repository: Path, agents: FakeAgents) -> None:
    update()
    config_file = Path(os.environ["CW_HOME"]) / "config.json"
    config = json.loads(config_file.read_text())
    config["storage"] = {"max_index_mb": 1}
    config_file.write_text(json.dumps(config))
    get_settings.cache_clear()
    with open(context_db_path("demo"), "ab") as handle:
        handle.write(b"\0" * (2 * storage.BYTES_PER_MB))
    ticket = pipeline.start_ticket("demo", "fix transfer_funds", plan_first=False)
    assert any("run: cw gc demo" in warning for warning in ticket.warnings)


def test_the_index_never_lands_inside_the_checkout(code_repository: Path, agents: FakeAgents) -> None:
    pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    assert code_repository not in context_db_path("demo").parents
    assert not list(code_repository.rglob("*.sqlite3"))


def test_notes_migrate_into_a_record_once(code_repository: Path) -> None:
    (code_repository / "CLAUDE.md").write_text(f"# Demo\n\n{NOTES_HEADING}\n- Use make test.\n")
    cli._migrate_notes("demo")
    cli._migrate_notes("demo")
    with opened_index("demo") as connection:
        records = context_index.all_records(connection)
    assert [record.notes for record in records] == [["Use make test."]]
    assert NOTES_HEADING in (code_repository / "CLAUDE.md").read_text()


def test_project_remove_deletes_caches_and_purge_deletes_the_rest_only_when_confirmed(
    code_repository: Path, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    data = context_db_path("demo").parent
    (data / "conventions.md").write_text("decisions\n")
    monkeypatch.setattr("builtins.input", lambda question: "n")
    run_cli("project", "remove", "demo", "--purge")
    assert context_db_path("demo").exists() and (data / "conventions.md").exists()
    run_cli("project", "remove", "demo")
    assert not context_db_path("demo").exists() and (data / "conventions.md").exists()


def test_purge_with_yes_deletes_decisions_and_tickets(code_repository: Path, agents: FakeAgents) -> None:
    pipeline.start_ticket("demo", "fix transfer_funds", plan_first=True)
    run_cli("project", "remove", "demo", "--purge", "--yes")
    assert not context_db_path("demo").parent.exists() and not (tickets_root() / "demo").exists()


def test_an_audit_counts_findings_on_the_final_diff_without_touching_runs(
    code_repository: Path, agents: FakeAgents
) -> None:
    ticket = pipeline.start_ticket("demo", "add a file", plan_first=False)
    pipeline.open_pull_request(ticket.id)
    pipeline.finish(ticket.id, abandon=False)
    runs = len(find_ticket(ticket.id).runs)
    finding = ReviewComment(path="work_1.txt", line=1, severity=Severity.BLOCKING, body="missed")
    agents.reviewer = lambda prompt: ReviewerResult(summary="audit", comments=[finding])
    audited = benchmark.audit(ticket.id)
    assert audited.audit.blocking == 1 and len(audited.runs) == runs
    row = next(row for row in benchmark.benchmark_report("demo") if row["ticket"] == ticket.id)
    assert row["audit_blocking"] == 1 and row["reviews_by_model"]


def test_a_search_or_eval_sketch_leaves_no_event_log_behind(
    code_repository: Path, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_with_events(settings, project, agent_name, role, prompt, cwd, output_model, ticket_id, **options):
        append_event(project.name, ticket_id, {"kind": "start", "agent": agent_name})
        return agents.run(settings, project, agent_name, role, prompt, cwd, output_model, ticket_id, **options)

    monkeypatch.setattr(context_engine, "run_agent", run_with_events)
    update()
    run_cli("context", "search", "demo", "fix transfer_funds")
    settings = get_settings()
    context_eval.evaluate(settings, settings.project("demo"), [context_eval.Method.FULL], 3, lambda samples: True)
    assert agents.prompts_for(AgentRole.CONTEXT)
    assert not list((tickets_root() / "demo").glob("*.events.jsonl"))
