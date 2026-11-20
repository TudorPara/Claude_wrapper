"""The context index: one SQLite file per project, a cache of the code at one commit, keyed by git blob.

A blob is the exact content of a file, so what is parsed or embedded from it is reused at every commit and path.
Nothing here is ever written into the project's repository.
"""

import hashlib
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath

import numpy
from pydantic import BaseModel

from cw import embeddings, git
from cw.code_chunks import parse_file, split_terms
from cw.config import Project, Settings, context_db_path
from cw.errors import AppErrorCode, AppException
from cw.repo_map import CODE_EXTENSIONS, IGNORED_DIRECTORIES

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
SNAPSHOT_TABLE = "snapshot"
EVAL_SNAPSHOT_TABLE = "eval_snapshot"
SNAPSHOT_TABLES = (SNAPSHOT_TABLE, EVAL_SNAPSHOT_TABLE)
BUSY_TIMEOUT_SECONDS = 5
TEXT_ENCODING = "utf-8"
VECTOR_TYPE = numpy.float32
META_SCHEMA = "schema_version"
META_COMMIT = "indexed_commit"
META_MODEL_KEY = "embedding_model_key"
TOKENIZER = "unicode61 tokenchars '_'"
MODEL_KEY_SEPARATOR = "@"
SQLITE_MAX_PARAMETERS = 900
UTC_TEXT_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
CHUNK_COLUMNS = (
    "s.path, c.blob, c.ordinal, c.kind, c.qualified_name, c.signature, c.start_line, c.end_line, c.docstring, c.text"
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blobs (blob TEXT PRIMARY KEY, language TEXT NOT NULL, docstring TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    blob TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL, qualified_name TEXT NOT NULL,
    signature TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
    docstring TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY (blob, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_terms USING fts5(
    terms, blob UNINDEXED, ordinal UNINDEXED, tokenize = "{TOKENIZER}"
);
CREATE TABLE IF NOT EXISTS definitions (blob TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (blob, name));
CREATE TABLE IF NOT EXISTS refs (blob TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (blob, name));
CREATE TABLE IF NOT EXISTS imports (blob TEXT NOT NULL, module TEXT NOT NULL, PRIMARY KEY (blob, module));
CREATE TABLE IF NOT EXISTS snapshot (path TEXT PRIMARY KEY, blob TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS definitions_by_name ON definitions (name);
CREATE TABLE IF NOT EXISTS chunk_hashes (
    path TEXT NOT NULL, blob TEXT NOT NULL, ordinal INTEGER NOT NULL, text_hash TEXT NOT NULL,
    PRIMARY KEY (path, blob, ordinal)
);
CREATE TABLE IF NOT EXISTS embeddings (
    model_key TEXT NOT NULL, text_hash TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (model_key, text_hash)
);
CREATE TABLE IF NOT EXISTS ticket_records (
    ticket_id TEXT PRIMARY KEY, finished_at TEXT NOT NULL, record TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS ticket_terms USING fts5(terms, ticket_id UNINDEXED, tokenize = "{TOKENIZER}");
"""
EVAL_SNAPSHOT_SCHEMA = (
    f"CREATE TEMP TABLE IF NOT EXISTS {EVAL_SNAPSHOT_TABLE} (path TEXT PRIMARY KEY, blob TEXT NOT NULL)"
)
FTS5_PROBE = "CREATE VIRTUAL TABLE temp.cw_fts5_probe USING fts5(probe)"
FTS5_PROBE_DROP = "DROP TABLE temp.cw_fts5_probe"


class EmbeddingMode(StrEnum):
    """When an update embeds the chunks that have no vector yet."""

    NONE = "none"
    AUTOMATIC = "automatic"
    ALL = "all"


class UpdateResult(BaseModel):
    """What an index update did."""

    blobs_parsed: int
    chunks_embedded: int
    files: int
    embedding_skipped: bool
    embedding_error: str = ""


class RecordFinding(BaseModel):
    """A review finding left open when a ticket finished."""

    path: str
    line: int
    severity: str
    summary: str


class TicketRecord(BaseModel):
    """What a finished ticket leaves for later tickets: built from the ticket, never rewritten by a model."""

    ticket_id: str
    title: str
    finished_at: datetime
    request: str
    plan_excerpt: str
    changed_files: list[str]
    open_findings: list[RecordFinding]
    notes: list[str]
    merge_commit: str = ""


class ChunkRow(BaseModel):
    """A chunk as stored, with the path it was found at."""

    path: str
    blob: str
    ordinal: int
    kind: str
    qualified_name: str
    signature: str
    start_line: int
    end_line: int
    docstring: str
    text: str


def utc_text(moment: datetime) -> str:
    """Return a moment as fixed-width UTC text, so moments compare correctly as strings in SQL."""
    return moment.astimezone(UTC).strftime(UTC_TEXT_FORMAT)


def _failed(detail: str) -> AppException:
    """Return the error every index failure raises, so callers can turn it into a warning."""
    return AppException(AppErrorCode.CONTEXT_INDEX_FAILED, detail)


def _check_table(table: str) -> str:
    """Return a snapshot table name after checking it is one cw owns, since it is formatted into SQL."""
    if table not in SNAPSHOT_TABLES:
        raise ValueError(f"Unknown snapshot table {table}.")
    return table


def _check_fts5(connection: sqlite3.Connection) -> None:
    """Refuse to work without SQLite's FTS5, which the keyword search needs."""
    try:
        connection.execute(FTS5_PROBE)
        connection.execute(FTS5_PROBE_DROP)
    except sqlite3.OperationalError as error:
        logger.error("This Python's SQLite has no FTS5: %s", error)
        raise AppException(
            AppErrorCode.INVALID_CONFIGURATION, "SQLite FTS5 is required by the context index."
        ) from error


def _connect(path: Path) -> sqlite3.Connection:
    """Open the database file with a busy timeout, so a short lock waits instead of failing."""
    return sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)


def open_index(project_name: str) -> sqlite3.Connection:
    """Open a project's index, creating it, or rebuilding it when its schema version differs (it is a cache)."""
    path = context_db_path(project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = _connect(path)
        _check_fts5(connection)
        version = meta_value(connection, META_SCHEMA) if _has_meta(connection) else SCHEMA_VERSION
        kept: list[TicketRecord] = []
        if version != SCHEMA_VERSION:
            logger.info("The context index of %s has an old format; rebuilding it.", project_name)
            kept = _readable_records(connection)
            connection.close()
            path.unlink()
            connection = _connect(path)
        connection.executescript(SCHEMA)
        connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (META_SCHEMA, SCHEMA_VERSION))
        connection.execute(EVAL_SNAPSHOT_SCHEMA)
        connection.commit()
        for record in kept:
            write_record(connection, record)
    except sqlite3.DatabaseError as error:
        raise _failed(
            f"{path} cannot be used ({error}); rebuild it with: cw context build {project_name} --prune"
        ) from error
    return connection


def _has_meta(connection: sqlite3.Connection) -> bool:
    """Return whether the database already has its meta table."""
    return (
        connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone() is not None
    )


@contextmanager
def opened_index(project_name: str) -> Iterator[sqlite3.Connection]:
    """Open a project's index for the duration of a block, and close it afterwards."""
    connection = open_index(project_name)
    try:
        yield connection
    finally:
        connection.close()


def _readable_records(connection: sqlite3.Connection) -> list[TicketRecord]:
    """Return the ticket records an index holds that can still be read; an unreadable one is left behind."""
    records = []
    try:
        rows = connection.execute("SELECT record FROM ticket_records").fetchall()
    except sqlite3.Error:
        return []
    for (text,) in rows:
        try:
            records.append(TicketRecord.model_validate_json(text))
        except ValueError:
            logger.warning("A ticket record could not be read and was not kept.")
    return records


def reset_index(project_name: str) -> int:
    """Delete a project's index to rebuild it, keeping its ticket records, which nothing else can rebuild.

    Returns how many records were kept; their vectors are made again by the build.
    """
    path = context_db_path(project_name)
    kept: list[TicketRecord] = []
    if path.exists():
        try:
            with closing(_connect(path)) as old:
                kept = _readable_records(old)
        except sqlite3.Error:
            kept = []
        path.unlink()
    with opened_index(project_name) as connection:
        for record in kept:
            write_record(connection, record)
    return len(kept)


def meta_value(connection: sqlite3.Connection, key: str) -> str:
    """Return a meta value, or an empty string."""
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def model_key(settings: Settings) -> str:
    """Return the key vectors are stored under: the model tag and the kept dimensions."""
    embeddings = settings.context.embeddings
    return f"{embeddings.model}{MODEL_KEY_SEPARATOR}{embeddings.dimensions}"


def is_indexed(path: str, size: int, project: Project, settings: Settings) -> bool:
    """Return whether a file belongs in the index: code, not ignored, not protected, not too big."""
    parts = PurePosixPath(path).parts
    return (
        PurePosixPath(path).suffix in CODE_EXTENSIONS
        and not IGNORED_DIRECTORIES.intersection(parts)
        and not project.is_protected(path)
        and size <= settings.context.max_file_bytes
    )


def indexed_tree(project: Project, settings: Settings, commit: str) -> dict[str, str]:
    """Return the indexed files of a commit as path → blob, paths relative to the project folder."""
    return {
        entry.path: entry.blob
        for entry in git.tree_entries(project.path, commit)
        if is_indexed(entry.path, entry.size, project, settings)
    }


def _ingest_blobs(connection: sqlite3.Connection, project: Project, settings: Settings, tree: dict[str, str]) -> int:
    """Parse and store every blob of the tree not yet in the index; return how many were parsed."""
    known = {row[0] for row in connection.execute("SELECT blob FROM blobs")}
    path_of = {blob: path for path, blob in tree.items() if blob not in known}
    for blob, content in git.read_blobs(project.path, list(path_of)):
        path = path_of[blob]
        parsed = parse_file(path, content.decode(TEXT_ENCODING, errors="replace"), settings.context.max_chunk_chars)
        connection.execute("INSERT OR REPLACE INTO blobs VALUES (?, ?, ?)", (blob, parsed.language, parsed.docstring))
        for ordinal, chunk in enumerate(parsed.chunks):
            connection.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    blob,
                    ordinal,
                    chunk.kind,
                    chunk.qualified_name,
                    chunk.signature,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.docstring,
                    chunk.text,
                ),
            )
            terms = split_terms(" ".join([chunk.qualified_name, chunk.signature, chunk.docstring, chunk.text]))
            connection.execute("INSERT INTO chunk_terms VALUES (?, ?, ?)", (" ".join(terms), blob, ordinal))
        connection.executemany(
            "INSERT OR IGNORE INTO definitions VALUES (?, ?)", [(blob, n) for n in parsed.definitions]
        )
        connection.executemany("INSERT OR IGNORE INTO refs VALUES (?, ?)", [(blob, n) for n in parsed.references])
        connection.executemany("INSERT OR IGNORE INTO imports VALUES (?, ?)", [(blob, m) for m in parsed.imports])
    connection.commit()
    return len(path_of)


def chunk_embedding_text(row: ChunkRow) -> str:
    """Return the text a chunk is embedded as: a header naming it, then its code. gc rebuilds hashes with it too."""
    header = [
        f"path: {row.path}",
        f"symbol: {row.qualified_name}{row.signature}" if row.signature else "",
        f"doc: {row.docstring}" if row.docstring else "",
    ]
    return "\n".join([line for line in header if line] + [row.text])


def text_hash(text: str) -> str:
    """Return the cache key of an embedded text."""
    return hashlib.sha256(text.encode(TEXT_ENCODING)).hexdigest()


def snapshot_chunks(connection: sqlite3.Connection, table: str = SNAPSHOT_TABLE) -> list[ChunkRow]:
    """Return every chunk of the files in a snapshot."""
    rows = connection.execute(f"SELECT {CHUNK_COLUMNS} FROM {_check_table(table)} s JOIN chunks c ON c.blob = s.blob")
    return _chunk_rows(rows)


def _chunk_rows(rows: Iterator[tuple]) -> list[ChunkRow]:
    """Return query rows of CHUNK_COLUMNS as chunks."""
    return [ChunkRow(**dict(zip(ChunkRow.model_fields, row, strict=True))) for row in rows]


def _hash_new_chunks(connection: sqlite3.Connection, table: str) -> None:
    """Record the embedding key of each snapshot chunk at its path that has none yet."""
    rows = connection.execute(
        f"SELECT {CHUNK_COLUMNS} FROM {_check_table(table)} s JOIN chunks c ON c.blob = s.blob "
        "LEFT JOIN chunk_hashes h ON h.path = s.path AND h.blob = c.blob AND h.ordinal = c.ordinal "
        "WHERE h.text_hash IS NULL"
    )
    connection.executemany(
        "INSERT OR REPLACE INTO chunk_hashes VALUES (?, ?, ?, ?)",
        [
            (chunk.path, chunk.blob, chunk.ordinal, text_hash(chunk_embedding_text(chunk)))
            for chunk in _chunk_rows(rows)
        ],
    )
    connection.commit()


def unembedded_chunks(connection: sqlite3.Connection, key: str, table: str = SNAPSHOT_TABLE) -> list[ChunkRow]:
    """Return the snapshot's chunks that have no vector under the model key yet."""
    rows = connection.execute(
        f"SELECT {CHUNK_COLUMNS} FROM {_check_table(table)} s JOIN chunks c ON c.blob = s.blob "
        "JOIN chunk_hashes h ON h.path = s.path AND h.blob = c.blob AND h.ordinal = c.ordinal "
        "LEFT JOIN embeddings e ON e.text_hash = h.text_hash AND e.model_key = ? WHERE e.text_hash IS NULL",
        (key,),
    )
    return _chunk_rows(rows)


def store_vectors(connection: sqlite3.Connection, key: str, hashes: list[str], vectors: numpy.ndarray) -> None:
    """Store vectors under their text hashes, and commit, so an interrupted build keeps what it finished."""
    connection.executemany(
        "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
        [(key, digest, vector.astype(VECTOR_TYPE).tobytes()) for digest, vector in zip(hashes, vectors, strict=True)],
    )
    connection.commit()


def missing_hashes(connection: sqlite3.Connection, key: str, hashes: list[str]) -> set[str]:
    """Return which of these text hashes have no vector under the model key yet."""
    stored = {row[0] for row in connection.execute("SELECT text_hash FROM embeddings WHERE model_key = ?", (key,))}
    return set(hashes) - stored


def embed_texts(
    connection: sqlite3.Connection,
    settings: Settings,
    texts: dict[str, str],
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Embed the texts (hash → text) that have no vector yet, in batches committed one by one; return how many."""
    key = model_key(settings)
    todo = sorted(missing_hashes(connection, key, list(texts)))
    batch_size = settings.context.embeddings.batch_size
    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        store_vectors(
            connection, key, batch, embeddings.encode_documents(settings, [texts[digest] for digest in batch])
        )
        if on_progress:
            on_progress(min(start + batch_size, len(todo)), len(todo))
    connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (META_MODEL_KEY, key))
    connection.commit()
    return len(todo)


def _replace_snapshot(connection: sqlite3.Connection, table: str, tree: dict[str, str]) -> None:
    """Replace a snapshot table's contents."""
    table = _check_table(table)
    connection.execute(f"DELETE FROM {table}")
    connection.executemany(f"INSERT INTO {table} VALUES (?, ?)", list(tree.items()))
    connection.commit()


def update_snapshot(
    connection: sqlite3.Connection,
    project: Project,
    settings: Settings,
    commit: str,
    table: str = SNAPSHOT_TABLE,
    embedding: EmbeddingMode = EmbeddingMode.AUTOMATIC,
    on_progress: Callable[[int, int], None] | None = None,
) -> UpdateResult:
    """Bring a snapshot table to a commit: parse unseen blobs, then embed chunks without vectors.

    An automatic update embeds only when at most `max_incremental_files` files lack vectors, so a ticket is never held
    up by a big embedding job, and an Ollama failure only skips the embedding. `cw context build` (ALL) embeds
    everything and stops on a failure. Parsed blobs and vectors are committed as they are made: they are a cache.
    """
    tree = indexed_tree(project, settings, commit)
    parsed = _ingest_blobs(connection, project, settings, tree)
    _replace_snapshot(connection, table, tree)
    if table == SNAPSHOT_TABLE:
        connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (META_COMMIT, commit))
        connection.commit()
    if embedding == EmbeddingMode.NONE:
        return UpdateResult(blobs_parsed=parsed, chunks_embedded=0, files=len(tree), embedding_skipped=True)
    _hash_new_chunks(connection, table)
    todo = unembedded_chunks(connection, model_key(settings), table)
    pending_files = {chunk.path for chunk in todo}
    if embedding == EmbeddingMode.AUTOMATIC and len(pending_files) > settings.context.embeddings.max_incremental_files:
        return UpdateResult(blobs_parsed=parsed, chunks_embedded=0, files=len(tree), embedding_skipped=True)
    texts = {text_hash(text): text for text in (chunk_embedding_text(chunk) for chunk in todo)}
    try:
        embedded = embed_texts(connection, settings, texts, on_progress)
    except AppException as error:
        if embedding == EmbeddingMode.ALL or error.error_code != AppErrorCode.CONTEXT_INDEX_FAILED:
            raise
        logger.warning("Embedding skipped: %s", error)
        return UpdateResult(
            blobs_parsed=parsed,
            chunks_embedded=0,
            files=len(tree),
            embedding_skipped=True,
            embedding_error=error.detail,
        )
    return UpdateResult(blobs_parsed=parsed, chunks_embedded=embedded, files=len(tree), embedding_skipped=False)


def vectors_for(connection: sqlite3.Connection, key: str, hashes: list[str]) -> dict[str, numpy.ndarray]:
    """Return the stored vectors of these text hashes under a model key."""
    found: dict[str, numpy.ndarray] = {}
    for start in range(0, len(hashes), SQLITE_MAX_PARAMETERS):
        batch = hashes[start : start + SQLITE_MAX_PARAMETERS]
        placeholders = ",".join("?" * len(batch))
        rows = connection.execute(
            f"SELECT text_hash, vector FROM embeddings WHERE model_key = ? AND text_hash IN ({placeholders})",
            (key, *batch),
        )
        found |= {digest: numpy.frombuffer(vector, dtype=VECTOR_TYPE) for digest, vector in rows}
    return found


def snapshot_vectors(
    connection: sqlite3.Connection, key: str, table: str = SNAPSHOT_TABLE
) -> tuple[list[tuple[str, int]], numpy.ndarray | None]:
    """Return the snapshot's embedded chunks as (path, ordinal) and one matrix of their vectors, row for row."""
    rows = connection.execute(
        f"SELECT s.path, h.ordinal, e.vector FROM {_check_table(table)} s "
        "JOIN chunk_hashes h ON h.path = s.path AND h.blob = s.blob "
        "JOIN embeddings e ON e.text_hash = h.text_hash AND e.model_key = ?",
        (key,),
    ).fetchall()
    if not rows:
        return [], None
    places = [(path, ordinal) for path, ordinal, _ in rows]
    matrix = numpy.stack([numpy.frombuffer(vector, dtype=VECTOR_TYPE) for _, _, vector in rows])
    return places, matrix


def record_text(record: TicketRecord) -> str:
    """Return the text a ticket record is searched and embedded by."""
    return "\n".join([record.title, record.request, record.plan_excerpt, *record.notes])


def write_record(connection: sqlite3.Connection, record: TicketRecord) -> None:
    """Store a ticket record, replacing only an earlier record of the same ticket."""
    connection.execute(
        "INSERT OR REPLACE INTO ticket_records VALUES (?, ?, ?)",
        (record.ticket_id, utc_text(record.finished_at), record.model_dump_json()),
    )
    connection.execute("DELETE FROM ticket_terms WHERE ticket_id = ?", (record.ticket_id,))
    connection.execute(
        "INSERT INTO ticket_terms VALUES (?, ?)", (" ".join(split_terms(record_text(record))), record.ticket_id)
    )
    connection.commit()


def all_records(connection: sqlite3.Connection) -> list[TicketRecord]:
    """Return every ticket record."""
    return [TicketRecord.model_validate_json(row[0]) for row in connection.execute("SELECT record FROM ticket_records")]


def _fts_query(terms: list[str]) -> str:
    """Return FTS5 query text that matches any of the terms, each quoted so user text cannot inject syntax."""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def lexical_chunks(
    connection: sqlite3.Connection, terms: list[str], limit: int, table: str = SNAPSHOT_TABLE
) -> list[tuple[str, int]]:
    """Return (path, ordinal) of the best keyword matches among the snapshot's chunks, best first."""
    if not terms:
        return []
    rows = connection.execute(
        f"SELECT s.path, chunk_terms.ordinal FROM chunk_terms JOIN {_check_table(table)} s "
        "ON s.blob = chunk_terms.blob WHERE chunk_terms MATCH ? ORDER BY chunk_terms.rank LIMIT ?",
        (_fts_query(terms), limit),
    )
    return [(path, int(ordinal)) for path, ordinal in rows]


def similar_records(
    connection: sqlite3.Connection, terms: list[str], before: datetime, exclude: set[str], limit: int
) -> list[TicketRecord]:
    """Return the records most similar by keywords, finished before a moment, without the excluded tickets."""
    if not terms:
        return []
    rows = connection.execute(
        "SELECT r.record FROM ticket_terms JOIN ticket_records r ON r.ticket_id = ticket_terms.ticket_id "
        "WHERE ticket_terms MATCH ? AND r.finished_at < ? ORDER BY ticket_terms.rank",
        (_fts_query(terms), utc_text(before)),
    )
    records = [TicketRecord.model_validate_json(row[0]) for row in rows]
    return [record for record in records if record.ticket_id not in exclude][:limit]


def records_before(connection: sqlite3.Connection, before: datetime, exclude: set[str]) -> list[TicketRecord]:
    """Return every record finished before a moment, without the excluded tickets."""
    rows = connection.execute("SELECT record FROM ticket_records WHERE finished_at < ?", (utc_text(before),))
    records = [TicketRecord.model_validate_json(row[0]) for row in rows]
    return [record for record in records if record.ticket_id not in exclude]


def graph_rows(
    connection: sqlite3.Connection, table: str = SNAPSHOT_TABLE
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return the snapshot's (path, defined name), (path, referenced name) and (path, imported module) rows."""
    table = _check_table(table)
    definitions = connection.execute(f"SELECT s.path, d.name FROM definitions d JOIN {table} s ON s.blob = d.blob")
    references = connection.execute(f"SELECT s.path, r.name FROM refs r JOIN {table} s ON s.blob = r.blob")
    imports = connection.execute(f"SELECT s.path, i.module FROM imports i JOIN {table} s ON s.blob = i.blob")
    return definitions.fetchall(), references.fetchall(), imports.fetchall()


def file_docstrings(connection: sqlite3.Connection, table: str = SNAPSHOT_TABLE) -> dict[str, str]:
    """Return each snapshot file's module docstring line."""
    rows = connection.execute(
        f"SELECT s.path, b.docstring FROM {_check_table(table)} s JOIN blobs b ON b.blob = s.blob"
    )
    return dict(rows.fetchall())


def snapshot_paths(connection: sqlite3.Connection, table: str = SNAPSHOT_TABLE) -> list[str]:
    """Return the snapshot's file paths."""
    return [row[0] for row in connection.execute(f"SELECT path FROM {_check_table(table)} ORDER BY path")]


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Return how many files, blobs, chunks, records and vectors the index holds."""
    queries = {
        "files": "SELECT COUNT(*) FROM snapshot",
        "blobs": "SELECT COUNT(*) FROM blobs",
        "chunks": "SELECT COUNT(*) FROM chunks",
        "ticket_records": "SELECT COUNT(*) FROM ticket_records",
        "vectors": "SELECT COUNT(*) FROM embeddings",
    }
    return {name: connection.execute(query).fetchone()[0] for name, query in queries.items()}


def embedded_snapshot_chunks(connection: sqlite3.Connection, key: str) -> int:
    """Return how many chunks of the snapshot have a vector under the model key."""
    return connection.execute(
        "SELECT COUNT(*) FROM snapshot s JOIN chunk_hashes h ON h.path = s.path AND h.blob = s.blob "
        "JOIN embeddings e ON e.text_hash = h.text_hash AND e.model_key = ?",
        (key,),
    ).fetchone()[0]


def chunks_of_paths(connection: sqlite3.Connection, paths: list[str], table: str = SNAPSHOT_TABLE) -> list[ChunkRow]:
    """Return every chunk of these snapshot files, in line order."""
    if not paths:
        return []
    placeholders = ",".join("?" * len(paths))
    rows = connection.execute(
        f"SELECT {CHUNK_COLUMNS} FROM {_check_table(table)} s JOIN chunks c ON c.blob = s.blob "
        f"WHERE s.path IN ({placeholders}) ORDER BY s.path, c.start_line",
        paths,
    )
    return _chunk_rows(rows)
