"""Ranking for the context engine: search channels fused by rank, a dependency-graph channel, and the rendered seed."""

import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

import numpy
from pydantic import BaseModel

from cw import context_index, embeddings
from cw.code_chunks import NAME_SEPARATOR, PYTHON_SUFFIX, ChunkKind, module_name, split_terms
from cw.config import Settings
from cw.context_index import SNAPSHOT_TABLE, ChunkRow, TicketRecord
from cw.errors import AppErrorCode, AppException
from cw.models import HydeSketch

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
PAGERANK_TOLERANCE = 1e-9
LAYOUT_DEPTH = 2
NOTES_SHOWN = 3
FOLDER_MARK = "/"
SYMBOL_COLUMN = 10
TICKETS_HEADER = "Similar finished tickets:\n"
SECTION_GAP = 2


class Channel(StrEnum):
    """One way of finding the code a request needs."""

    LEXICAL = "lexical"
    PATH = "path"
    GRAPH = "graph"
    HISTORY = "history"
    DENSE = "dense"
    HYDE = "hyde"


ALL_CHANNELS = frozenset(Channel)


class SeedSymbol(BaseModel):
    """A definition shown under a file in the seed."""

    start_line: int
    end_line: int
    signature: str
    qualified_name: str
    docstring: str


class SeedFile(BaseModel):
    """A file the seed points to, with its most relevant definitions."""

    path: str
    docstring: str
    symbols: list[SeedSymbol]


class ContextSeed(BaseModel):
    """The ranked shortlist for one request, and the text an agent gets."""

    ranking: list[str]
    files: list[SeedFile]
    tickets: list[TicketRecord]
    warnings: list[str]
    text: str


def query_terms(request: str, hyde: HydeSketch | None, limit: int) -> list[str]:
    """Return the request's distinct search terms, then the sketch's guessed paths and names, at most `limit`."""
    guessed = " ".join([*hyde.paths, *hyde.symbols]) if hyde else ""
    return list(dict.fromkeys(split_terms(request) + split_terms(guessed)))[:limit]


def _ranked_files(places: list[tuple[str, int]]) -> list[str]:
    """Return the files of ranked chunks, each at the rank of its best chunk."""
    return list(dict.fromkeys(path for path, _ in places))


def _path_ranking(paths: list[str], terms: list[str]) -> list[str]:
    """Return the files whose path shares words with the query, most shared first."""
    wanted = set(terms)
    scored = [(len(wanted & set(split_terms(path))), path) for path in paths]
    return [path for score, path in sorted(scored, key=lambda item: (-item[0], item[1])) if score]


def _dense_places(
    query: numpy.ndarray, places: list[tuple[str, int]], matrix: numpy.ndarray | None, limit: int
) -> list[tuple[str, int]]:
    """Return the chunks closest to a query vector, best first."""
    if matrix is None or not places:
        return []
    scores = matrix @ query
    order = numpy.argsort(-scores, kind="stable")[:limit]
    return [places[index] for index in order]


def _fuse(rankings: dict[Channel, list[str]], weights: dict[Channel, float], fusion_k: int) -> dict[str, float]:
    """Return each file's Reciprocal Rank Fusion score over the channels' rankings."""
    fused: dict[str, float] = defaultdict(float)
    for channel, ranking in rankings.items():
        for rank, path in enumerate(ranking, start=1):
            fused[path] += weights.get(channel, 1.0) / (fusion_k + rank)
    return fused


def _module_index(paths: list[str]) -> dict[str, set[str]]:
    """Return, for every dotted suffix of every Python module, the files that define it."""
    index: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if PurePosixPath(path).suffix != PYTHON_SUFFIX:
            continue
        parts = module_name(path).split(NAME_SEPARATOR)
        for start in range(len(parts)):
            index[NAME_SEPARATOR.join(parts[start:])].add(path)
    return index


def dependency_graph(
    definitions: list[tuple[str, str]],
    references: list[tuple[str, str]],
    imports: list[tuple[str, str]],
    paths: list[str],
    import_edge_weight: float,
    reverse_edge_weight: float,
) -> dict[str, dict[str, float]]:
    """Return weighted file-to-file edges: a used name splits 1 over the files defining it; an import adds a weight.

    Every edge also runs backwards at `reverse_edge_weight` of its weight, so PageRank lifts the callers of found code
    as well as what it depends on.
    """
    defined_in: dict[str, set[str]] = defaultdict(set)
    for path, name in definitions:
        defined_in[name].add(path)
    edges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for path, name in references:
        owners = sorted(defined_in.get(name, set()) - {path})
        for owner in owners:
            edges[path][owner] += 1 / len(owners)
    modules = _module_index(paths)
    for path, module in imports:
        targets = sorted(modules.get(module, set()) - {path})
        for target in targets:
            edges[path][target] += import_edge_weight / len(targets)
    if reverse_edge_weight:
        for source, targets in list(edges.items()):
            for target, weight in list(targets.items()):
                edges[target][source] += reverse_edge_weight * weight
    return edges


def personalized_pagerank(
    edges: dict[str, dict[str, float]],
    nodes: list[str],
    personalization: dict[str, float],
    damping: float,
    iterations: int,
) -> dict[str, float]:
    """Return PageRank over the graph, restarting at the personalization (uniform when it is empty)."""
    total = sum(personalization.get(node, 0.0) for node in nodes)
    restart = {node: (personalization.get(node, 0.0) / total if total else 1 / len(nodes)) for node in nodes}
    out_weight = {node: sum(edges.get(node, {}).values()) for node in nodes}
    rank = dict(restart)
    for _ in range(iterations):
        dangling = sum(rank[node] for node in nodes if not out_weight[node])
        updated = {node: (1 - damping) * restart[node] + damping * dangling * restart[node] for node in nodes}
        for node in nodes:
            if out_weight[node]:
                for target, weight in edges[node].items():
                    updated[target] += damping * rank[node] * weight / out_weight[node]
        change = sum(abs(updated[node] - rank[node]) for node in nodes)
        rank = updated
        if change < PAGERANK_TOLERANCE:
            break
    return rank


def _graph_ranking(
    connection: sqlite3.Connection, settings: Settings, table: str, paths: list[str], fused: dict[str, float]
) -> list[str]:
    """Return the files ranked by Personalized PageRank from the searches' fused scores."""
    if not paths:
        return []
    definitions, references, imports = context_index.graph_rows(connection, table)
    edges = dependency_graph(
        definitions,
        references,
        imports,
        paths,
        settings.context.import_edge_weight,
        settings.context.reverse_edge_weight,
    )
    scores = personalized_pagerank(
        edges, paths, fused, settings.context.pagerank_damping, settings.context.pagerank_iterations
    )
    return [path for path, score in sorted(scores.items(), key=lambda item: (-item[1], item[0])) if score > 0]


def _history(
    connection: sqlite3.Connection,
    settings: Settings,
    terms: list[str],
    query: numpy.ndarray | None,
    before: datetime,
    exclude: set[str],
) -> list[TicketRecord]:
    """Return the most similar finished tickets, by keywords and by meaning, fused by rank."""
    limit = settings.context.max_similar_tickets
    by_terms = context_index.similar_records(connection, terms, before, exclude, limit)
    rankings = {Channel.LEXICAL: [record.ticket_id for record in by_terms]}
    known = {record.ticket_id: record for record in by_terms}
    if query is not None:
        candidates = context_index.records_before(connection, before, exclude)
        hashes = {record.ticket_id: context_index.text_hash(context_index.record_text(record)) for record in candidates}
        vectors = context_index.vectors_for(connection, context_index.model_key(settings), list(hashes.values()))
        scored = [
            (float(vectors[hashes[record.ticket_id]] @ query), record)
            for record in candidates
            if hashes[record.ticket_id] in vectors
        ]
        best = [record for _, record in sorted(scored, key=lambda item: (-item[0], item[1].ticket_id))][:limit]
        rankings[Channel.DENSE] = [record.ticket_id for record in best]
        known |= {record.ticket_id: record for record in best}
    fused = _fuse(rankings, {}, settings.context.fusion_k)
    order = sorted(fused, key=lambda ticket_id: (-fused[ticket_id], ticket_id))
    return [known[ticket_id] for ticket_id in order[:limit]]


def _symbols(chunks: list[ChunkRow], matched: list[int], limit: int) -> list[SeedSymbol]:
    """Return a file's definitions to show: the ones a search matched first, then its other top-level ones."""
    by_ordinal = {chunk.ordinal: chunk for chunk in chunks if chunk.kind != ChunkKind.FILE}
    top_level = [chunk.ordinal for chunk in chunks if chunk.kind in (ChunkKind.CLASS, ChunkKind.FUNCTION)]
    chosen = [ordinal for ordinal in dict.fromkeys([*matched, *sorted(top_level)]) if ordinal in by_ordinal][:limit]
    return [
        SeedSymbol(
            start_line=by_ordinal[ordinal].start_line,
            end_line=by_ordinal[ordinal].end_line,
            signature=by_ordinal[ordinal].signature,
            qualified_name=by_ordinal[ordinal].qualified_name,
            docstring=by_ordinal[ordinal].docstring,
        )
        for ordinal in chosen
    ]


def layout_outline(paths: list[str], max_folders: int) -> str:
    """Return the project's folders two levels deep with their file counts, so a broad request keeps its bearings."""
    folders = Counter(
        FOLDER_MARK.join(PurePosixPath(path).parts[:depth]) + FOLDER_MARK
        for path in paths
        for depth in range(1, min(LAYOUT_DEPTH, len(PurePosixPath(path).parts) - 1) + 1)
    )
    lines = [f"  {folder} {count}" for folder, count in sorted(folders.items())[:max_folders]]
    return "Layout (folder, code files):\n" + "\n".join(lines) if lines else ""


def _file_block(file: SeedFile) -> str:
    """Return one file's lines in the seed."""
    head = f"{file.path} — {file.docstring}" if file.docstring else file.path
    lines = [head]
    for symbol in file.symbols:
        doc = f" — {symbol.docstring}" if symbol.docstring else ""
        lines.append(f"  {symbol.start_line}-{symbol.end_line}".ljust(SYMBOL_COLUMN) + f" {symbol.signature}{doc}")
    return "\n".join(lines)


def _ticket_block(record: TicketRecord, shown_paths: set[str]) -> str:
    """Return one similar ticket's lines: what it changed, its notes, and its open findings on files in the seed."""
    changed = ", ".join(record.changed_files) or "nothing recorded"
    lines = [f'{record.ticket_id} "{record.title}" — changed: {changed}']
    lines += [f"  note: {note}" for note in record.notes[:NOTES_SHOWN]]
    lines += [
        f"  open finding left: {finding.path}:{finding.line} {finding.severity} — {finding.summary}"
        for finding in record.open_findings
        if finding.path in shown_paths
    ]
    return "\n".join(lines)


def render_seed(
    layout: str, files: list[SeedFile], tickets: list[TicketRecord], max_chars: int, ticket_chars: int
) -> tuple[str, list[SeedFile]]:
    """Return the seed within the budget (layout, files, then tickets; whole blocks only) and the files kept.

    When there are similar tickets, `ticket_chars` of the budget is kept for them: they carry the workers' notes.
    """
    budget = max_chars - len(layout) - SECTION_GAP
    file_budget = budget - (ticket_chars if tickets else 0)
    kept_files: list[SeedFile] = []
    blocks: list[str] = []
    for file in files:
        block = _file_block(file)
        if len(block) + 1 > file_budget:
            break
        blocks.append(block)
        kept_files.append(file)
        file_budget -= len(block) + 1
        budget -= len(block) + 1
    shown = {file.path for file in kept_files}
    budget -= len(TICKETS_HEADER) + SECTION_GAP
    ticket_blocks: list[str] = []
    for record in tickets:
        block = _ticket_block(record, shown)
        if len(block) + 1 > budget:
            break
        ticket_blocks.append(block)
        budget -= len(block) + 1
    sections = [layout, "\n".join(blocks)]
    if ticket_blocks:
        sections.append(TICKETS_HEADER + "\n".join(ticket_blocks))
    return "\n\n".join(section for section in sections if section), kept_files


def build_seed(
    connection: sqlite3.Connection,
    settings: Settings,
    request: str,
    channels: frozenset[Channel],
    before: datetime,
    exclude: set[str],
    hyde: HydeSketch | None = None,
    table: str = SNAPSHOT_TABLE,
) -> ContextSeed:
    """Rank the snapshot's files for a request and render the seed; a channel that fails is skipped with a warning.

    `before` and `exclude` keep the history channel to tickets that had finished when the request arrived.
    """
    context = settings.context
    warnings: list[str] = []
    paths = context_index.snapshot_paths(connection, table)
    terms = query_terms(request, hyde if Channel.HYDE in channels else None, context.max_query_terms)
    rankings: dict[Channel, list[str]] = {}
    matched: dict[str, list[int]] = defaultdict(list)
    if Channel.LEXICAL in channels:
        places = context_index.lexical_chunks(connection, terms, context.lexical_top_k, table)
        rankings[Channel.LEXICAL] = _ranked_files(places)
        for path, ordinal in places:
            matched[path].append(ordinal)
    if Channel.PATH in channels:
        rankings[Channel.PATH] = _path_ranking(paths, terms)
    query = None
    if Channel.DENSE in channels or (Channel.HYDE in channels and hyde):
        try:
            key = context_index.model_key(settings)
            vector_places, matrix = context_index.snapshot_vectors(connection, key, table)
            if Channel.DENSE in channels:
                query = embeddings.encode_query(settings, request)
                dense = _dense_places(query, vector_places, matrix, context.embeddings.dense_top_k)
                rankings[Channel.DENSE] = _ranked_files(dense)
                for path, ordinal in dense:
                    matched[path].append(ordinal)
            if Channel.HYDE in channels and hyde and hyde.code.strip():
                sketch = embeddings.encode_documents(settings, [hyde.code])[0]
                rankings[Channel.HYDE] = _ranked_files(
                    _dense_places(sketch, vector_places, matrix, context.embeddings.dense_top_k)
                )
        except AppException as error:
            if error.error_code != AppErrorCode.CONTEXT_INDEX_FAILED:
                raise
            logger.warning("Semantic search skipped: %s", error)
            warnings.append(f"semantic search skipped: {error.detail}")
            query = None
    tickets: list[TicketRecord] = []
    if Channel.HISTORY in channels:
        tickets = _history(connection, settings, terms, query, before, exclude)
        existing = set(paths)
        rankings[Channel.HISTORY] = list(
            dict.fromkeys(path for record in tickets for path in record.changed_files if path in existing)
        )
    weights = {Channel.HISTORY: context.history_weight, Channel.GRAPH: context.graph_weight}
    fused = _fuse(rankings, weights, context.fusion_k)
    if Channel.GRAPH in channels:
        graph = {Channel.GRAPH: _graph_ranking(connection, settings, table, paths, dict(fused))}
        for path, score in _fuse(graph, weights, context.fusion_k).items():
            fused[path] = fused.get(path, 0.0) + score
    ranking = sorted(fused, key=lambda path: (-fused[path], path))
    shortlist = ranking[: context.max_seed_files]
    chunks_by_path: dict[str, list[ChunkRow]] = defaultdict(list)
    for chunk in context_index.chunks_of_paths(connection, shortlist, table):
        chunks_by_path[chunk.path].append(chunk)
    docstrings = context_index.file_docstrings(connection, table)
    files = [
        SeedFile(
            path=path,
            docstring=docstrings.get(path, ""),
            symbols=_symbols(chunks_by_path[path], matched.get(path, []), context.max_symbols_per_file),
        )
        for path in shortlist
    ]
    layout = layout_outline(paths, context.layout_max_folders)
    text, kept = (
        render_seed(
            layout,
            files,
            tickets,
            context.seed_max_tokens * CHARS_PER_TOKEN,
            context.seed_ticket_tokens * CHARS_PER_TOKEN,
        )
        if files
        else ("", [])
    )
    return ContextSeed(ranking=ranking, files=kept, tickets=tickets, warnings=warnings, text=text)
