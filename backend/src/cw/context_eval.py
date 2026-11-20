"""`cw context eval`: how often the context seed finds the files a change really touched.

Each sample is scored against the code as it was when the change started, using only tickets that had finished
by then. Samples come from finished tickets, or from the project's git history with --from-commits.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from cw import context_index, embeddings, git
from cw.config import Project, Settings
from cw.context_engine import request_text, sketch_outside_ticket
from cw.context_index import EVAL_SNAPSHOT_TABLE, EmbeddingMode, opened_index
from cw.context_search import Channel, build_seed, layout_outline
from cw.errors import AppException
from cw.models import TicketStatus
from cw.tickets import list_tickets, project_lock, read_ticket, ticket_path

logger = logging.getLogger(__name__)

EVAL_LOCK_LABEL = "eval"
EVAL_EVENTS_PREFIX = "eval-"
SOURCE_TICKET = "ticket"
SOURCE_COMMIT = "commit"


class Method(StrEnum):
    """A combination of channels the eval scores."""

    PLANNER = "planner"
    LEXICAL = "lexical"
    LEXICAL_GRAPH = "lexical+graph"
    HISTORY = "+history"
    DENSE = "+dense"
    FULL = "full"


METHOD_CHANNELS: dict[Method, frozenset[Channel]] = {
    Method.LEXICAL: frozenset({Channel.LEXICAL, Channel.PATH}),
    Method.LEXICAL_GRAPH: frozenset({Channel.LEXICAL, Channel.PATH, Channel.GRAPH}),
    Method.HISTORY: frozenset({Channel.LEXICAL, Channel.PATH, Channel.GRAPH, Channel.HISTORY}),
    Method.DENSE: frozenset({Channel.LEXICAL, Channel.PATH, Channel.GRAPH, Channel.HISTORY, Channel.DENSE}),
    Method.FULL: frozenset(Channel),
}
SEMANTIC_METHODS = (Method.DENSE, Method.FULL)


class Sample(BaseModel):
    """One change to replay: the request, the commit it started from, and the files it really changed."""

    source: str
    sample_id: str
    request: str
    base: str
    at: datetime
    truth: list[str]
    dropped: int
    approximate: bool
    planner_files: list[str]


class MethodScore(BaseModel):
    """One method's results over the samples."""

    method: Method
    samples: int
    approximate: int
    recall: dict[int, float]
    mrr: float


class EvalReport(BaseModel):
    """What `cw context eval` prints."""

    project: str
    source: str
    samples: int
    dropped_truth_files: int
    skipped_methods: dict[str, str]
    scores: list[MethodScore]
    hyde_runs: int
    hyde_cost_usd: float
    elapsed_seconds: float


def _existing(project: Project, base: str, paths: list[str]) -> list[str]:
    """Return the paths that existed at the base commit: a file the change created cannot be found."""
    return [path for path in paths if git.exists_at(project.path, base, path)]


def ticket_samples(project: Project) -> list[Sample]:
    """Return a sample per finished ticket; tickets from before `changed_files` existed are marked approximate."""
    samples = []
    for summary in list_tickets():
        if summary.project != project.name or summary.status != TicketStatus.DONE:
            continue
        ticket = read_ticket(ticket_path(project.name, summary.id))
        truth = ticket.changed_files or list(dict.fromkeys(path for entry in ticket.work_log for path in entry.files))
        kept = _existing(project, ticket.base_sha, truth)
        samples.append(
            Sample(
                source=SOURCE_TICKET,
                sample_id=ticket.id,
                request=request_text(ticket),
                base=ticket.base_sha,
                at=ticket.created_at,
                truth=kept,
                dropped=len(truth) - len(kept),
                approximate=not ticket.changed_files,
                planner_files=ticket.files,
            )
        )
    return [sample for sample in samples if sample.truth]


def _cw_merge_commits(project: Project) -> list[str]:
    """Return the commits cw tickets were merged as; replaying them would let a ticket's record give the answer."""
    commits = []
    for summary in list_tickets():
        if summary.project == project.name and summary.status == TicketStatus.DONE:
            merged = read_ticket(ticket_path(project.name, summary.id)).merge_commit
            if merged:
                commits.append(merged)
    return commits


def commit_samples(project: Project, settings: Settings, count: int) -> list[Sample]:
    """Return a sample per recent non-merge commit of the base branch, without commits that cw tickets produced."""
    remote_base = f"{project.remote}/{project.base_branch}"
    ref = remote_base if git.resolve_commit(project.path, remote_base) else project.base_branch
    produced = _cw_merge_commits(project)
    samples = []
    for commit in git.recent_commits(project.path, ref, count):
        if any(commit.sha.startswith(merged) or merged.startswith(commit.sha) for merged in produced):
            continue
        changed = [
            path
            for path in git.changed_files(project.path, commit.parent, commit.sha)
            if context_index.is_indexed(path, 0, project, settings)
        ]
        kept = _existing(project, commit.parent, changed)
        if not kept or len(kept) > settings.context.eval_max_truth_files:
            continue
        samples.append(
            Sample(
                source=SOURCE_COMMIT,
                sample_id=commit.sha,
                request=commit.message,
                base=commit.parent,
                at=datetime.fromisoformat(commit.date),
                truth=kept,
                dropped=len(changed) - len(kept),
                approximate=False,
                planner_files=[],
            )
        )
    return samples


def score_ranking(ranking: list[str], truth: list[str], k_values: list[int]) -> tuple[dict[int, float], float]:
    """Return recall at each cut-off and the reciprocal rank of the first true file."""
    wanted = set(truth)
    recall = {k: len(wanted & set(ranking[:k])) / len(wanted) for k in k_values}
    first = next((rank for rank, path in enumerate(ranking, start=1) if path in wanted), None)
    return recall, (1 / first if first else 0.0)


def _semantic_ready(settings: Settings) -> str:
    """Return why the semantic methods cannot run, or an empty string when Ollama is ready."""
    try:
        embeddings.check_embeddings(settings)
    except AppException as error:
        return str(error)
    return ""


def evaluate(
    settings: Settings,
    project: Project,
    methods: list[Method],
    from_commits: int | None,
    confirm: Callable[[int], bool],
) -> EvalReport:
    """Replay every sample with each method on a temporary snapshot of its base, and report recall and MRR.

    The real snapshot is never touched. `full` runs one HyDE sketch per sample, so `confirm` is asked first.
    """
    started = time.monotonic()
    samples = commit_samples(project, settings, from_commits) if from_commits else ticket_samples(project)
    skipped: dict[str, str] = {}
    if from_commits and Method.PLANNER in methods:
        skipped[Method.PLANNER] = "commits have no planner"
    reason = _semantic_ready(settings) if any(method in SEMANTIC_METHODS for method in methods) else ""
    for method in SEMANTIC_METHODS:
        if reason and method in methods:
            skipped[method] = reason
    if Method.FULL in methods and Method.FULL not in skipped and not confirm(len(samples)):
        skipped[Method.FULL] = "not confirmed"
    active = [method for method in methods if method not in skipped]
    semantic = any(method in SEMANTIC_METHODS for method in active)
    totals = {method: ({k: 0.0 for k in settings.context.eval_k_values}, 0.0, 0) for method in active}
    hyde_runs, hyde_cost = 0, 0.0
    with project_lock(project.name, EVAL_LOCK_LABEL), opened_index(project.name) as connection:
        for sample in samples:
            mode = EmbeddingMode.ALL if semantic else EmbeddingMode.NONE
            context_index.update_snapshot(connection, project, settings, sample.base, EVAL_SNAPSHOT_TABLE, mode)
            hyde = None
            if Method.FULL in active:
                layout = layout_outline(
                    context_index.snapshot_paths(connection, EVAL_SNAPSHOT_TABLE), settings.context.layout_max_folders
                )
                events_id = f"{EVAL_EVENTS_PREFIX}{project.name}"
                hyde, usages, _ = sketch_outside_ticket(settings, project, events_id, sample.request, layout)
                hyde_runs += 1
                hyde_cost += sum(usage.cost_usd for usage in usages)
            for method in active:
                if method == Method.PLANNER:
                    ranking = sample.planner_files
                else:
                    seed = build_seed(
                        connection,
                        settings,
                        sample.request,
                        METHOD_CHANNELS[method],
                        sample.at,
                        {sample.sample_id},
                        hyde,
                        EVAL_SNAPSHOT_TABLE,
                    )
                    ranking = seed.ranking
                recall, reciprocal = score_ranking(ranking, sample.truth, settings.context.eval_k_values)
                sums, mrr, count = totals[method]
                totals[method] = ({k: sums[k] + recall[k] for k in sums}, mrr + reciprocal, count + 1)
    scores = [
        MethodScore(
            method=method,
            samples=count,
            approximate=sum(1 for sample in samples if sample.approximate) if count else 0,
            recall={k: (value / count if count else 0.0) for k, value in sums.items()},
            mrr=mrr / count if count else 0.0,
        )
        for method, (sums, mrr, count) in totals.items()
    ]
    return EvalReport(
        project=project.name,
        source=SOURCE_COMMIT if from_commits else SOURCE_TICKET,
        samples=len(samples),
        dropped_truth_files=sum(sample.dropped for sample in samples),
        skipped_methods=skipped,
        scores=scores,
        hyde_runs=hyde_runs,
        hyde_cost_usd=hyde_cost,
        elapsed_seconds=round(time.monotonic() - started, 1),
    )
