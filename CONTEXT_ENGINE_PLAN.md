# cw context engine and review cost: implementation plan

For: Claude Code, implementing in `TudorPara/Claude_wrapper` on branch `features/optimizations-and-embeddings`, cut from `main` at `255c789`.

**Status (2026-11-19): implemented, not committed.** Every step is built and tested (167 backend tests, 8 FRIDAY tests). How it works now is `EXPLANATION.md` §19; what changed and what was measured is the 2026-11-19 entry of `CHANGES_AND_ISSUES.md`. Changes made while implementing, from a second outside review and from measurement:
- `cw audit` and `cw benchmark` measure quality next to cost (a cheaper run that ships more bugs must not look like a win); `BENCHMARK.md` runs the baseline twice to show the noise;
- `--from-commits` skips the commits cw tickets produced, so their records cannot leak the answer (tickets store `merge_commit`);
- findings are posted with their id (`[F3]`) and matched to threads by it, not only by text;
- the seed always starts with a short folder outline (`layout_max_folders`), so broad requests keep their bearings;
- the merge error lists which threads were declined by the worker;
- a `chunk_hashes` table maps each chunk at its path to its embedding key, so search joins straight to the vectors;
- `RoleTurns.context` defaults to 1: one real Haiku sketch worked with one turn;
- `max_incremental_files` defaults to 20 and counts files without vectors (not changed files): the 8B model embeds about 1.5 chunks a second on the M5, so 200 files could hold a ticket for half an hour;
- `cw context build --prune` and a schema rebuild keep the ticket records;
- graph edges also run backwards (`reverse_edge_weight` 0.5) so callers rise; `seed_ticket_tokens` (500) is kept for similar tickets; `max_query_terms` is separate from `lexical_top_k`.

This is the revised plan. It replaces the first version after a review against the code (2026-11-19) and Tudor's decisions. Every feature below is built and enabled. The evaluation and the token benchmark measure the result; they do not decide what gets built.

---

## 0. Read this first

### 0.1 The goal

**Lower the cost and the use of the 5-hour usage window below what cw costs today, with the same quality, and workers still taking turns in one checkout.** The measure is cost (what `cw stats` reports), not the raw token count: the same tokens on a cheaper model, or read from the cache, count as a saving. Nothing in this plan runs workers in parallel.

Today's cost, measured on the 13 `shoplab` tickets of the 2026-11-19 manual test (the baseline for §2.4):

| Role | Runs | Cost | Share | Where it goes |
|---|---|---|---|---|
| Reviewer (Opus) | 19 | $4.21 | 47% | A new session every round. After a long fix round the prompt cache has expired, so a re-review pays 30–74k new tokens on Opus. `cw fix` reruns the whole review-and-fix loop inside every round: the layer refactor made 7 reviewer runs ($2.25 of its $3.40). |
| Worker (Sonnet) | 28 | $2.60 | 29% | Mostly cache reads; long fix rounds (up to 37 turns) |
| Planner (Opus) | 10 | $2.23 | 25% | First run 13–36k new tokens (rules, repo map, request), then exploration |
| **Total** | | **$9.04** | | |

So the plan has two parts:
1. **Step 0 cuts review cost** (the biggest share) and fixes the pull request flow.
2. **Steps 1–9 build the context engine**, which gives the planner (and the worker on direct tickets) the 10–20 places in the code most likely to matter for this request, plus the most similar finished tickets. Its job is fewer exploration turns, a smaller first prompt (the seed replaces the repo map), and a project memory that stops growing `CLAUDE.md`.

### 0.2 How the context engine works (one paragraph)

At ticket start, cw searches four channels over a per-project index:

1. **Lexical**: SQLite FTS5 / BM25 over identifiers, signatures, docstrings and code.
2. **Path**: path words matched against the request.
3. **History**: finished tickets similar to this request, and the files they changed.
4. **Semantic**: embeddings of code chunks and tickets from Qwen3-Embedding-8B, served by a local Ollama server.

The semantic query is expanded with **HyDE**: Haiku sketches the code the change would probably touch, and the sketch is searched next to the request, never instead of it. cw fuses the channel rankings with Reciprocal Rank Fusion. It adds a **graph channel**: Personalized PageRank over the file dependency graph (Python `ast`, no LLM), personalised by the fused ranking. It then renders the top files and symbols as a **context seed** within a token budget. The seed goes into the planner's fresh prompt, or the worker's first prompt on a direct ticket, **in place of** the repo map.

Workers' notes live in **ticket records** and reach later tickets through the history channel, not through `CLAUDE.md`. The index is keyed by **git blob SHA**, so unchanged files are never parsed or embedded twice, and any past commit can be indexed cheaply for evaluation.

### 0.3 Rules for the implementer

- **Generic configuration.** Every value a coworker might change comes from `config.json` (`Settings` in `cw/config.py`), never from code. Algorithmic internals nobody tunes are named module constants.
- **Every new field and group has a default** (Tudor's decision; the repo's `CLAUDE.md` rule). An existing `config.json` keeps working after the upgrade with no edits. `config.example.json` lists every new key with its default. New groups are declared as `context: ContextSettings = Field(default_factory=ContextSettings)`.
- **FRIDAY carries every new field.** Each new field the app saves goes into `friday/Sources/Friday/Models.swift`, optional where it is new. And in step 0 FRIDAY stops re-encoding the whole config from its own structs (§1.6), so no field it doesn't know is ever dropped again. Rerun `friday/Tests/make_fixtures.py` and `friday/test.sh` after every change to `--json` output.
- **No over-engineering.** No class where a function does, no wrapper around a library call. Use `sqlite3`, `ast`, `subprocess` and `urllib.request` from the standard library. The one new dependency is `numpy`. Embeddings come from Ollama over its local HTTP API: no model library, no `torch`, no Ollama client package.
- **Style:**
  - no magic numbers or strings;
  - no comments in code or config files;
  - one-line docstrings;
  - full type hints;
  - one `logger = logging.getLogger(__name__)` per module;
  - `ruff check` and `ruff format --check` clean;
  - names say what the thing does.
- **Errors.** `AppException(AppErrorCode.X)` from `cw/errors.py`. Reuse `INVALID_CONFIGURATION` and `GIT_FAILED`. Add `CONTEXT_INDEX_FAILED` for index and Ollama failures.
- **Best effort at ticket time.** The context engine never fails a ticket. Any error (database missing, locked or corrupt; Ollama down or the model not pulled; the HyDE run failing) logs a warning, adds a ticket warning with the prefix `Context:` through `_set_warning`, and continues with the channels that still work. If none work, the planner gets the repo map as today.
- **Workflow:**
  - all work on `features/optimizations-and-embeddings`, one step at a time, in order;
  - small conventional commits (`feat:`, `fix:`, `test:`, `docs:`), each step ending with a hand-over to Tudor;
  - no commit or push without Tudor's go-ahead;
  - run the backend checks (`uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run pytest -q`) and, when FRIDAY changed, `swift build` and `./test.sh` before every hand-over;
  - say plainly what was not verified.
- **Do not hallucinate APIs.** Before relying on behaviour, check the installed versions (`ollama --version`; 0.40.16 is installed), Ollama's API docs for `/api/embed`, the Qwen3-Embedding model card, and the `gh` and Bitbucket docs for thread replies. Tests never touch the network, never call Ollama or `claude`, and never download a model.

### 0.4 Implementation order

| Step | Delivers | Section |
|---|---|---|
| 0 | Pull request flow and review cost: merge gate on unresolved threads, comment outcomes (B11), one review per `cw fix` round, Sonnet re-reviews, review skip for small comment fixes, no README/`CLAUDE.md` churn in fix passes, FRIDAY saves without dropping keys | 1 |
| 1 | `changed_files`, `cw context eval` (tickets and git history), the token benchmark | 2 |
| 2 | Blob-keyed index, chunking, lexical and path channels, dependency graph, PageRank channel, seed in the planner prompt in place of the repo map, `context` CLI group | 3 |
| 3 | Ticket records and the history channel | 4 |
| 4 | Semantic channel through local Ollama | 5 |
| 5 | HyDE query expansion with Haiku | 6 |
| 6 | Notes go to ticket records instead of `CLAUDE.md` | 7 |
| 7 | Seed for direct tickets | 8 |
| 8 | Storage hygiene: `cw gc`, size warning, event retention, `cw project remove` cleanup | 9 |
| 9 | Docs, `cw doctor`, final report | 10 |

After each step from 1 on, run `cw context eval` (§2.3) and paste the table into the hand-over. After steps 0, 2, 5 and 7, run the token benchmark (§2.4) and paste it too.

---

## 1. Step 0: pull request flow and review cost

### 1.1 Unresolved pull request threads stop `cw merge`, and B11

**Today:** `pipeline.merge_pull_request` refuses only for `ticket.open_findings` and CI. It never looks at the PR's threads, and `cw fix` resolves every thread it sent, also the ones the worker declined (B11).

**Fix:**
- In `merge_pull_request`, after the `open_findings` check, call `host.open_threads(ticket.pr_number)`. If any is unresolved, raise `INVALID_STATE` with the count, the first few `path:line` locations, and the hint: `cw fix <ticket>`, resolve them on the host, or merge on the host yourself.
- **State this in the docs:** the first review posts *every* finding as a line thread, minor ones included (`_open_pull_request`, `first_post`). After this change, a minor finding blocks `cw merge` until `cw fix` handles it or someone resolves it. That's intended.
- **Comment outcomes (B11):**
  - add `comment_outcomes: list[CommentOutcome] = []` to `WorkerResult`, where `CommentOutcome` holds `thread_id: str`, `outcome: CommentOutcomeKind` (`StrEnum`: `fixed`, `declined`) and `reason: str`. Empty outside PR fix passes;
  - `prompts.pull_request_comments_instruction` labels every thread with its id and asks for one outcome per thread;
  - `_fix_pull_request` resolves only `fixed` threads. A `declined` thread gets a reply with the reason and stays open, so a person decides. A thread with no outcome stays open;
  - add `reply_to_thread(number, thread_id, body)` to the git host interface (GitHub: GraphQL `addPullRequestReviewThreadReply`; Bitbucket: a comment with `parent.id`). Check both APIs in their docs first.
- **Don't resend declined threads.** cw posts as the user's own account, so a reply cannot be told apart by author. Every decline reply starts with a named constant marker (for example `DECLINE_REPLY_PREFIX`). `_fix_pull_request` skips a thread whose last comment starts with it; when a person replies after it, the thread is sent again.
- Bitbucket's `open_threads` and replies have never run against a real Bitbucket. Say so in the hand-over.

**Tests:**
- `merge_pull_request` refuses with one unresolved thread on `FakeHost`, and merges with none;
- a declined comment stays open and gets a reply starting with the marker;
- a fixed comment is resolved;
- a thread with no outcome stays open;
- the next `cw fix` does not resend a thread whose last comment is the decline reply, and does resend it after a person replies.

### 1.2 One review per `cw fix` round

**Today:** every `cw fix` round calls `_open_pull_request`, which runs `_review` with its own fix-and-re-review loop (`max_review_rounds`). The loops multiply: 3 `cw fix` rounds × up to 3 reviews each.

**Fix:**
- `_open_pull_request` gets a parameter for the review rounds it may use. `cw pr` passes `max_review_rounds` as today. A `cw fix` round passes `0`: it reviews once, posts the review as a comment, and leaves any new blocking findings open. The next `cw fix` round, up to `max_pull_request_fix_rounds`, sends them back to the worker.
- So a `cw fix` run makes at most one review per round, and the total is bounded by `max_pull_request_fix_rounds`.

**Tests:**
- a `cw fix` round whose review finds a blocking issue makes exactly one reviewer call, and the finding is in the next round's instruction;
- `cw pr` still runs its full review loop.

### 1.3 Re-reviews on a cheaper model

A first review reads the whole change with fresh eyes, so it stays on the reviewer model (Opus). A later review only checks a fix against listed open findings plus a small new diff.

- Add `reviewer_followup: str = "sonnet"` to `RoleModels`, with that default, and add it to `Models.swift` and to FRIDAY's model settings.
- A review is a **follow-up** when the ticket already has at least one review entry. That covers the review loop's rounds 2+, every `cw fix` review, and conflict-resolution reviews.
- `run_agent` gets an optional `model: str | None = None` that overrides `_model_for(project, role)`; `_review` passes `reviewer_followup` for follow-ups. Turns stay `max_turns.reviewer`.

**Tests:**
- the first review runs with `reviewer`, the second with `reviewer_followup` (`FakeAgents` records the model);
- a config without `reviewer_followup` loads and uses `sonnet`.

### 1.4 Skip the review for small comment-only fixes

**Rule:** a `cw fix` round skips the review when all of these hold:
1. the round addressed **no open blocking finding**. That counts both the findings sent as findings and every thread whose body contains an open finding's body (the same match as `pipeline.py:1013`). The first review's blocking findings are also threads, and skipping the review on them would leave them open forever.
2. the checks pass;
3. the round's change is at most `review_skip_max_changed_lines` lines (added plus removed, `git diff --numstat` of the round) and touches no protected path.

Then cw commits, pushes, and posts the summary with one line saying the review was skipped and why. Otherwise the round reviews as in §1.2.

- New `Limits` field: `review_skip_max_changed_lines: NonNegativeInt = 20` (`0` means never skip).
- `cw pr` never skips its review.

**Tests:**
- a two-line fix of a person's comment makes no reviewer call;
- a fix of a thread that is an open blocking finding always reviews;
- a 30-line fix with the limit at 20 reviews;
- `0` always reviews;
- failing checks never skip.

### 1.5 No `README.md` / `CLAUDE.md` churn in fix passes

**Today:** `pipeline._worker` calls `project_memory.add_notes` after every pass, fix passes included (`pipeline.py:355`). And three rule files make "the README matches the tree" a check on every review:
- `review-checks.md:93`;
- `pr-review-procedure.md:165` and `:203`;
- `python-stack.md:74`.

`fix_review_instruction` also sends minor findings to the fix round, so a minor README finding becomes a README edit in every round.

**Fix:**
- Fix passes (`_fix`: checks, review findings, `cw fix` rounds, conflicts) don't write notes, and their instructions say `project_notes` must be empty. (Step 6 removes `CLAUDE.md` notes altogether.)
- In those four places, make the README rule conditional: a README finding is raised **only** when the diff changes something the README documents (a command, an env key, a setup step, a public endpoint), and it names that README line.
- `fix_review_instruction` and `pull_request_comments_instruction` add: change only what the findings and comments ask; edit `README.md` or `CLAUDE.md` only when a finding or comment names them.
- A fix pass that changes `README.md` or `CLAUDE.md` when no finding or comment names them gets a `SCOPE_WARNING`. Other files don't warn, because a fix often needs a test file no comment names.

**Tests:**
- a fix pass returning `project_notes` leaves `CLAUDE.md` unchanged;
- a fix pass that edits `README.md` without a finding on it gets the scope warning; one that edits a test file does not.

### 1.6 FRIDAY saves only what it edits

**Today:** `Store.swift:240` encodes FRIDAY's own `CWConfig` struct and sends it to `cw config set`, so every key the Swift structs don't know is dropped on save.

**Fix:**
- FRIDAY keeps the raw JSON from `cw config` next to the decoded struct, writes only the keys its screens edit into that raw JSON, and sends the result.
- The new fields of this plan that FRIDAY shows (`reviewer_followup`, `review_skip_max_changed_lines`, later `context` and `storage`) also go into `Models.swift`, as optionals.

**Tests (`friday/Tests`):** a fixture with an unknown key survives a save round-trip.

### 1.7 Settings and docs for step 0

- `config.example.json`: `reviewer_followup` in every `RoleModels` (defaults and stacks' profiles), and `review_skip_max_changed_lines` in `Limits`.
- `EXPLANATION.md`:
  - `cw merge` refuses with unresolved threads, including minor findings;
  - declined comments stay open with a reply;
  - `cw fix` reviews once per round;
  - re-reviews run on `reviewer_followup`;
  - small comment-only fixes skip the review;
  - fix passes never write notes.
- `CHANGES_AND_ISSUES.md`: an entry, B11 closed.

---

## 2. Step 1: ground truth, evaluation and the token benchmark

### 2.1 `Ticket.changed_files`

- Add `changed_files: list[str] = []` to `Ticket`.
- Fill it at the start of `pipeline.finish` when `abandon` is false, **before** `_merge_locally` (after a local merge, the merge-base becomes the branch tip and the diff would be empty). The ticket branch exists on both paths at that point.
- Only the ticket's own changes, nothing that came in from the base through `_sync_base`: `git diff --name-only --relative <merge-base(base_ref, ticket.branch)> ticket.branch`. Here `base_ref` is the ref `_sync_base` merges from: the remote base when a remote exists, the local target otherwise. Add `merge_base(cwd, first, second) -> str` to `cw/git.py` and reuse `changed_files`.

**Test:** a ticket changes `a.py`; the base gets a commit touching `b.py`, merged into the ticket branch; the ticket finishes. Expected: `changed_files == ["a.py"]`.

### 2.2 Ground truth for old tickets

Tickets finished before 2.1 have no `changed_files`. The eval uses the union of `work_log[*].files` instead, and marks them `approximate`.

### 2.3 `cw context eval`

```
cw context eval <project> [--from-commits N] [--json] [--method <name> ...] [--yes]
```

**Two sources of samples:**

| Source | Request | Base | Truth |
|---|---|---|---|
| Tickets (default) | Non-answer prompts, joined | `ticket.base_sha` | `changed_files`, or the 2.2 fallback |
| `--from-commits N` | The commit message (subject and body) | The commit's first parent | The files the commit changed |

`--from-commits` takes the last `N` non-merge commits of the project's base branch. It skips commits whose truth has more than `eval_max_truth_files` files (mass renames and formatting commits say nothing about retrieval). There are only 10 finished tickets today, so commits are what make the numbers meaningful on a large repository. Commit messages are terser than ticket requests: report the two sources separately.

**For every sample:**
- truth keeps only files that existed at the base (`git cat-file -e <base>:<path>`), because no search can find a file that didn't exist; report how many were dropped;
- the history channel only uses ticket records finished before the sample's time (a ticket's `created_at`, or the commit's author date): the leakage rule of §4.2.

**Metrics per method:**
- recall@k for each `k` in `eval_k_values`;
- MRR of the first true file;
- samples scored, and how many are `approximate`.

Output is a plain table, or JSON with `--json`. Print the elapsed time.

**Methods, registered as the steps land:**

| Method | Step | Channels |
|---|---|---|
| `planner` | 1 | The planner's own `files` (ticket samples only; a reference line) |
| `lexical` | 2 | Lexical + path |
| `lexical+graph` | 2 | + PageRank channel |
| `+history` | 3 | + history |
| `+dense` | 4 | + semantic |
| `full` | 5 | + HyDE |

**HyDE in eval:**
- `full` costs one Haiku run per sample. Print the number of runs before starting and ask for confirmation unless `--yes` is passed.
- Eval runs go to no real ticket: `run_agent` gets the event file id `eval-<project>`, so nothing is written into a real ticket's activity log, and their cost is printed in the eval output only.

### 2.4 The token benchmark

The eval measures retrieval. The goal is cost and usage, so cw also gets a repeatable cost comparison.

- `BENCHMARK.md` in the repository lists a fixed sequence of prompts on a fresh project: the nine use cases of the 2026-11-19 manual test (new project, small fix, flat feature, planned CSV export with feedback, PR comments with `cw fix`, park and continue, layer refactor with PostgreSQL, split, a planned request on the grown repository). It also lists the commands that run them.
- Run it with the same models on `main` (the baseline above: $9.04, reviewer $4.21, worker $2.60, planner $2.23) and after steps 0, 2, 5 and 7.
- **Report:** cost per role from `cw stats` (the headline number), the reviewer run count by model, and planner and worker turns per ticket. Same prompts, same models; differences in what the agents decide make it noisy, so say so next to the numbers.

---

## 3. Step 2: the index, lexical and path channels, graph and seed

### 3.1 Settings

A `ContextSettings` group in `cw/config.py`. Every field has a default; the group defaults as a whole. Later steps add their fields to it.

| Setting | Type | Default |
|---|---|---|
| `include_repo_map` | `bool` | `false`: the seed replaces the repo map; the map is used only when the seed is empty |
| `seed_max_tokens` | `PositiveInt` | `2500`: at most what the repo map costs today, so the planner's first prompt does not grow |
| `max_seed_files` | `PositiveInt` | `15` |
| `max_symbols_per_file` | `PositiveInt` | `6` |
| `lexical_top_k` | `PositiveInt` | `60` |
| `fusion_k` | `PositiveInt` | `60` |
| `graph_weight` | `float > 0` | `1.0`: weight of the PageRank channel in the fusion |
| `pagerank_damping` | `0 < float < 1` | `0.85` |
| `pagerank_iterations` | `PositiveInt` | `30` |
| `import_edge_weight` | `float > 0` | `2.0` |
| `max_file_bytes` | `PositiveInt` | `400000` |
| `max_chunk_chars` | `PositiveInt` | `6000` |
| `eval_k_values` | `list[PositiveInt]` | `[5, 10, 20]` |
| `eval_max_truth_files` | `PositiveInt` | `20` |

Module constants:
- `CHARS_PER_TOKEN = 4`;
- `PAGERANK_TOLERANCE`;
- `MIN_TERM_LENGTH`;
- `STOP_WORDS`;
- `SCHEMA_VERSION`;
- `CONTEXT_DB_FILE_NAME = "context.sqlite3"` next to `REPO_MAP_FILE_NAME`, with `context_db_path(project_name)`.

### 3.2 Modules

| Module | Responsibility |
|---|---|
| `cw/code_chunks.py` | One file's text → chunks, definitions, references, imports. Pure functions. |
| `cw/context_index.py` | The SQLite store: schema, blob ingestion, snapshot update, ticket records, embeddings storage, queries. Owns all SQL. |
| `cw/context_search.py` | Ranking: query terms, channels, graph, PageRank, fusion, seed rendering. |
| `cw/embeddings.py` | Ollama `/api/embed` calls (step 4). |

Import `repo_map.CODE_EXTENSIONS` and `repo_map.IGNORED_DIRECTORIES`; don't copy them.

### 3.3 Chunking (`code_chunks.py`)

Input: `path: str`, `text: str`. Output: a `ParsedFile` (Pydantic) with:
- `language`;
- `docstring` (the first line of the module docstring);
- `chunks`: each a `Chunk` with `kind` (`file`, `class`, `function`, `method`), `qualified_name`, `signature`, `start_line`, `end_line`, `docstring` (first line) and `text` (the source lines, cut to `max_chunk_chars`);
- `definitions` and `references`, both **distinct** names;
- `imports`, as dotted modules.

**Python, with `ast`:**
- One `file` chunk per file: the module docstring plus the top-level names, spanning the whole file.
- One chunk per top-level function, async function and class, and one per method (`ClassName.method`).
- `signature` is rebuilt from `ast` with positional-only, positional, keyword-only, `*args` and `**kwargs` parameters. `repo_map._python_outline` drops keyword-only parameters; don't copy it.
- `references`: every `ast.Name.id`, `ast.Attribute.attr` and imported name, distinct, without dunders or names shorter than `MIN_TERM_LENGTH`.
- `imports` come from `Import` and `ImportFrom`; relative imports are resolved against the file's package path.
- On `SyntaxError` or `UnicodeDecodeError`, return only a `file` chunk and log at debug level.

**Other code files** (the rest of `CODE_EXTENSIONS`):
- one `file` chunk with the first `max_chunk_chars` characters;
- references from an identifier regex;
- no definitions, imports or graph edges.

Write that limitation in `EXPLANATION.md`.

**`split_terms(text) -> list[str]`**, used for indexing and querying:
- split on non-alphanumerics, snake_case and camelCase boundaries, and lowercase;
- drop terms shorter than `MIN_TERM_LENGTH` and stop words;
- keep the whole identifier too (`transfer_funds` → `transfer_funds`, `transfer`, `funds`).

### 3.4 Storage (`context_index.py`)

One SQLite file per project, `~/.cw/projects/<name>/context.sqlite3`, never inside the checkout.

- **FTS5 is required.** Check it once on opening (a temporary FTS5 table in a rolled-back transaction); if it is missing, raise `INVALID_CONFIGURATION` naming FTS5.
- **The FTS5 tables use `tokenize = "unicode61 tokenchars '_'"`.** The default tokenizer splits on `_`: checked on 2026-11-19, `transfer_funds` matched `transfer`. With `_` as a token character, the whole identifier is its own term, and `split_terms` adds the parts.

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE blobs (blob TEXT PRIMARY KEY, language TEXT NOT NULL, docstring TEXT NOT NULL);
CREATE TABLE chunks (
    blob TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL, qualified_name TEXT NOT NULL,
    signature TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
    docstring TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY (blob, ordinal)
);
CREATE VIRTUAL TABLE chunk_terms USING fts5(terms, blob UNINDEXED, ordinal UNINDEXED, tokenize = "unicode61 tokenchars '_'");
CREATE TABLE definitions (blob TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (blob, name));
CREATE TABLE refs (blob TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (blob, name));
CREATE TABLE imports (blob TEXT NOT NULL, module TEXT NOT NULL, PRIMARY KEY (blob, module));
CREATE TABLE snapshot (path TEXT PRIMARY KEY, blob TEXT NOT NULL);
CREATE INDEX definitions_by_name ON definitions (name);
```

- `meta` holds `schema_version`, `indexed_commit`, and (step 4) the embedding model key and dimensions. On a version mismatch the file is deleted and rebuilt: it is a cache, never migrated.
- `chunk_terms.terms` = `split_terms(qualified_name + signature + docstring + text)`, joined by spaces.
- Path terms are not stored per blob (one blob can sit at two paths); path matching runs per snapshot in Python.

### 3.5 Reading git

Index the **commit**, never the working tree.

- **Run `git ls-tree -r -z <commit>` in the project folder, without `--full-tree`.** Paths are then relative to the project, like `git.changed_files` (`--relative`), which matters for a project in a subfolder of its repository. Keep only entries under the project folder.
- **Filter the entries:**
  - keep `blob` entries whose suffix is in `CODE_EXTENSIONS`;
  - drop anything with a part in `IGNORED_DIRECTORIES`;
  - drop anything matching the project's `protected_paths`. Match them the way `guard.py` does (the pattern on the path, on the path without a leading `**/`, and on the file name), in one cw helper tested against the same cases;
  - skip blobs larger than `max_file_bytes` (`git cat-file --batch-check`).
- Read only blobs not yet in `blobs`, through one `git cat-file --batch` process. Add a helper in `cw/git.py` that yields `(sha, bytes)`, and decode as UTF-8 with `errors="replace"`.

### 3.6 Update operations

**`update_snapshot(project, checkout, commit, snapshot_table) -> UpdateResult`:**
- ingests unseen blobs and embeds chunks without vectors (step 4, capped by `max_incremental_files`);
- replaces `snapshot_table` with the new `path → blob` mapping and, for the real table, sets `indexed_commit`;
- returns counts.

Blob ingestion and embedding commit on their own, so the eval keeps them (§3.9). Call it only while holding `project_lock`.

**`build(project, checkout, prune)`:**
- deletes the file if `prune`;
- updates at `HEAD` with no embedding cap;
- writes records for all `DONE` tickets (step 3) with their vectors (step 4).

**Call sites:**
- `_create_ticket`, where `repo_map.write_project_repo_map` runs: update at `start_point`. If the database doesn't exist yet, build it here **without** embeddings and add the warning `Context: semantic search is incomplete; run "cw context build <project>" once`.
- `finish` (not abandon), where the repo map is rewritten: update at the tip of `_target(project, ticket)`, not at `HEAD` (`finish` switches back to `previous_branch`), then write the ticket record (step 3).

Wrap both in `try/except` for `sqlite3.Error`, `OSError` and `AppException`: log, add the `Context:` warning, continue.

### 3.7 Ranking (`context_search.py`)

`build_seed(project, ticket, request_text, channels) -> ContextSeed`, with `ContextSeed` holding the ranked files with their symbols, the similar tickets (step 3), and the rendered text. `channels` is the set to use: the eval passes subsets, and tickets pass all.

1. **Query terms:** `split_terms(request_text)`, deduplicated, at most `lexical_top_k`, each quoted (so user text cannot inject FTS syntax) and joined with `OR`.
2. **Lexical:** `SELECT blob, ordinal FROM chunk_terms WHERE chunk_terms MATCH ? ORDER BY rank LIMIT ?`, restricted to snapshot blobs. A file ranks by its best chunk.
3. **Path:** files whose path terms overlap the query terms, by overlap count.
4. **Fusion of the search channels:** `fused[file] += weight(channel) / (fusion_k + rank)`, rank from 1. Every weight is 1 except history (step 3).
5. **Graph:**
   - nodes are snapshot paths;
   - for each file A and each name in A's `refs` defined in files B₁…Bₙ (`definitions` joined to the snapshot), add `1/n` to the edge A→Bᵢ;
   - for each resolved import of A to B, add `import_edge_weight`;
   - drop self-edges.
6. **Personalized PageRank:**
   - personalisation = `fused` from 4, normalised; uniform when empty;
   - power iteration `r = (1 - d) * p + d * propagate(r)`, with out-edges normalised per node and dangling mass redistributed by `p`;
   - stop after `pagerank_iterations`, or when the L1 change is below `PAGERANK_TOLERANCE`;
   - plain dictionaries, no networkx.
7. **The graph channel is fused like any other.** Rank the files by PageRank score and add `graph_weight / (fusion_k + rank)` to `fused`. The final order is the total fused score, ties broken by path. PageRank is not the final order, because files everyone imports (`errors.py`, `config.py`) would rise above the files that actually matched.
8. **Symbols per file:** chunks that matched in the lexical or dense channels first, by rank, then the file's other top-level chunks in line order, up to `max_symbols_per_file`.
9. **Render** within `seed_max_tokens × CHARS_PER_TOKEN`, whole file blocks only, in rank order:

```
src/app/services/accounts.py — Account operations that move money.
  42-88    def transfer(source_id, target_id, amount_minor) — Moves money between two accounts in one transaction.
  90-120   class TransferLimits
```

The seed is deterministic for the same index, request and HyDE sketch. Test that.

### 3.8 Planner prompt

- Build the seed in `_run_planner` when the ticket has none yet, and store it as `Ticket.context_seed: str = ""` and its file list as `Ticket.seed_files: list[str] = []`. A fresh prompt after a lost session reuses the stored seed; it is never rebuilt, so HyDE never runs twice.
- `prompts.planner_prompt` gets the seed as a parameter. In the fresh branch, when it is not empty, add `## Code likely relevant to this request`, then one sentence (*"cw ranked these by keyword, dependency, embedding and past-ticket signals. They are a starting point, not a verdict: open what you need, skip what does not fit, and search further only where the plan needs it."*), then the seed in a code block.
- **The repo map goes in only when the seed is empty, or `include_repo_map` is true.**
- A resumed planner (`fresh=False`) never gets the seed again.
- `planner.md` gets one sentence: the prompt may contain a ranked list of likely relevant code to verify, not trust, and `context_pack` must still point to lines the planner checked itself.

### 3.9 CLI

A `context` group in `cli.py`:

| Command | What it does |
|---|---|
| `cw context build <project> [--prune]` | Full build at `HEAD` with all embeddings and records; counts, a progress line while embedding, the elapsed time. |
| `cw context status <project> [--json]` | Indexed commit and whether it equals `HEAD`; file, blob, chunk and record counts; size; embedding model key and chunks with vectors. |
| `cw context search <project> "<text>" [--json] [--no-hyde]` | The seed exactly as the planner would get it. |
| `cw context eval …` | §2.3. |

**Eval per sample:**
1. `update_snapshot` at the sample's base into a **TEMP** snapshot table. Never a rolled-back transaction: it would also roll back the blobs and vectors just computed, which are kept as a cache.
2. `build_seed` per method, on the TEMP snapshot.
3. Score the order against the truth.

The real snapshot never changes. Test that.

### 3.10 Tests (step 2)

Use `conftest.py`'s fixtures; no network.

- `split_terms`: snake_case, camelCase, digits, stop words.
- **Chunking:**
  - a class with methods, async functions, keyword-only parameters;
  - a syntax error gives one `file` chunk;
  - relative imports resolve.
- **FTS5:** `transfer_funds` is matched as a whole term, and a query for `transfer` still finds it through the split parts.
- **Blob reuse:**
  - update at commit A, then at B with one changed file: exactly one blob is parsed;
  - a renamed file parses nothing.
- A project in a subfolder of its repository indexes project-relative paths that match `changed_files`.
- Protected paths and ignored directories never enter the snapshot.
- **Lexical ranking:** a request naming `transfer_funds` ranks its defining file first.
- **PageRank:**
  - a hand-built three-file graph matches a hand-computed value;
  - an empty personalisation is uniform;
  - a hub file imported by every file does not outrank the file that matched the request.
- **Seed:**
  - within budget;
  - deterministic;
  - never cuts a block.
- **Planner prompt:**
  - has the seed section and no repo map when the index exists;
  - the first ticket on a project with no database builds it and gets a seed;
  - a corrupt database (garbage written to the file) gives a `Context:` warning, and the ticket proceeds with the repo map.
- `finish` updates the index to the tip of the base, also when you were on another branch before.
- **`cw context eval`:**
  - recall is computed;
  - files a ticket created are excluded;
  - `--from-commits` uses parents as bases;
  - the real snapshot is unchanged afterwards.

---

## 4. Step 3: ticket records and the history channel

### 4.1 Ticket records

When a ticket finishes (not abandoned), cw writes a record, built from the ticket with no LLM:

| Field | Source |
|---|---|
| `ticket_id`, `title`, `finished_at` | Ticket |
| `request` | Non-answer prompts, joined |
| `plan_excerpt` | First `record_plan_chars` characters of `ticket.plan` |
| `changed_files` | 2.1 |
| `open_findings` | `ticket.open_findings`: path, line, severity, first line of the body |
| `notes` | All `work_log[*].project_notes`, deduplicated |

```sql
CREATE TABLE ticket_records (
    ticket_id TEXT PRIMARY KEY, title TEXT NOT NULL, finished_at TEXT NOT NULL, request TEXT NOT NULL,
    plan_excerpt TEXT NOT NULL, changed_files TEXT NOT NULL, open_findings TEXT NOT NULL, notes TEXT NOT NULL
);
CREATE VIRTUAL TABLE ticket_terms USING fts5(terms, ticket_id UNINDEXED, tokenize = "unicode61 tokenchars '_'");
```

- List columns hold JSON from a `TicketRecord` model.
- Records are inserted once; re-finishing a ticket id replaces its own row only.
- `ticket_terms.terms` = `split_terms(title + request + plan_excerpt + notes)`.
- `cw context build` writes records for every `DONE` ticket, with the 2.2 fallback.

**Settings (with defaults):**
- `max_similar_tickets: PositiveInt = 3`;
- `record_plan_chars: PositiveInt = 1200`;
- `history_weight: float = 1.0` (greater than 0).

**Records are per person.** `~/.cw` lives on the machine that ran the ticket, so a teammate's cw doesn't see your records, and after step 6 your notes either. Tudor chose this over keeping notes in `CLAUDE.md`. Write it in `EXPLANATION.md`.

### 4.2 History channel

- Search `ticket_terms` with the query terms, top `max_similar_tickets`, fused by RRF with the dense ticket search (step 4). Exclude the current ticket.
- **Leakage rule:** only records with `finished_at` before the sample's time (the ticket's `created_at`, or a commit's date in eval). It is a parameter of the history query, used live and in eval, and it is tested.
- Every changed file of the similar tickets that is still in the snapshot enters the fusion as the history channel, weighted by `history_weight`, ranked by the ticket's rank, then by order within it.

### 4.3 Seed additions

After the files block, a `## Similar finished tickets` section:

```
T-261104-2 "Add CSV export to statements" — changed: src/app/services/statements.py, src/app/routes/statements.py
  note: statement exports go through StatementExporter, never straight from the route
  open finding left: src/app/services/statements.py:88 minor — export ignores the currency column
```

- **Notes:** at most three per ticket.
- **Open findings:** only when their path is in the seed's files.
- **Budget:** the section counts toward `seed_max_tokens`. Files are rendered first, then tickets, and whatever doesn't fit is dropped.

### 4.4 Tests

- A record is written on finish, not on abandon.
- The leakage rule holds, and the current ticket is excluded.
- An open finding appears only when its file is in the seed.
- Rebuilding does not duplicate records.
- History raises the right file in a synthetic case.

---

## 5. Step 4: semantic channel

### 5.1 Ollama, model and settings

Embeddings come from a local **Ollama** at `http://localhost:11434`. The model is **Qwen3-Embedding-8B**, `qwen3-embedding:8b-q8_0`:
- Ollama's library has 0.6b, 4b and 8b; the 8B has a 4096-value embedding;
- Apache 2.0, no remote code;
- the Mac has 32 GB, so the 8B fits next to Docker and the IDE.

Queries carry a task instruction; documents don't. Take the exact query template from the official model card (`Qwen/Qwen3-Embedding-8B`) and put it in settings; don't invent it.

**Setup** (in `OVERVIEW.md` and checked by `cw doctor`):

```
brew install ollama
ollama serve
ollama pull qwen3-embedding:8b-q8_0
```

Add `numpy` with `uv add numpy` and record the version.

```python
class EmbeddingProvider(StrEnum):
    """Where embeddings are computed."""

    OLLAMA = "ollama"


class OllamaEmbeddingSettings(StrictModel):
    """Embeddings from a local Ollama server."""

    provider: Literal[EmbeddingProvider.OLLAMA] = EmbeddingProvider.OLLAMA
    base_url: str = Field(default=..., description="Ollama server URL.")
    model: str = Field(default=..., description="Ollama model tag, including the quantisation.")
    query_template: str = Field(default=..., description="Format of a query; must contain {instruction} and {query}.")
    query_instruction: str = Field(default=..., description="Task instruction placed in every query.")
    dimensions: PositiveInt = Field(default=..., description="Leading vector values kept; the rest are cut, then renormalised.")
    context_length: PositiveInt = Field(default=..., description="Tokens Ollama reads per text (options.num_ctx).")
    batch_size: PositiveInt = Field(default=..., description="Texts per /api/embed request.")
    request_timeout_seconds: PositiveInt = Field(default=..., description="Longest one request may take.")
    keep_alive: str = Field(default=..., description="How long Ollama keeps the model loaded, e.g. 10m.")
    dense_top_k: PositiveInt = Field(default=..., description="Chunks taken from the embedding search.")
    max_incremental_files: PositiveInt = Field(
        default=..., description="Above this many changed files, an automatic update skips embedding and asks for a build."
    )
```

`default=...` stands for the defaults in the table below; every field has one, and `embeddings` defaults as a whole in `ContextSettings`. Validate that `query_template` contains both placeholders; raise `INVALID_CONFIGURATION` (logging the value first) if not. When a second provider appears, turn this into a discriminated union; don't build one for one member now.

| Setting | Default |
|---|---|
| `base_url` | `http://localhost:11434` |
| `model` | `qwen3-embedding:8b-q8_0` |
| `query_template` | Copied from the official model card |
| `query_instruction` | `Given a request to change a code repository, retrieve the code that must be read or changed to do it` |
| `dimensions` | `1024` |
| `context_length` | `8192` |
| `batch_size` | `32` |
| `request_timeout_seconds` | `300` |
| `keep_alive` | `10m` |
| `dense_top_k` | `60` |
| `max_incremental_files` | `200` |

**About `dimensions`:** Qwen3-Embedding supports shorter outputs, so keeping the leading 1024 of 4096 values and renormalising is a supported use, at a quarter of the storage. The model tag and `dimensions` are part of the cache key, so changing either re-embeds. Report recall at 1024 and 4096 in the step 4 hand-over.

**About `context_length`:** a chunk is up to 6000 characters plus its header, about 1.5–2.5k tokens of code. Ollama's default context can be shorter, and `/api/embed` truncates by default without saying so. Send `options.num_ctx = context_length`, check in the docs what Ollama 0.40.16 does with longer input and whether `/api/embed` accepts `dimensions`, and report it. cw truncates to `dimensions` itself either way.

**Fallback if the build is too slow:** `qwen3-embedding:4b` is a settings change only.

### 5.2 `cw/embeddings.py`

- `encode_documents(settings, texts) -> numpy.ndarray` and `encode_query(settings, text) -> numpy.ndarray`. Queries are formatted with `query_template` and `query_instruction`; documents are sent as they are. Requests are batched by `batch_size`.
- **One private function makes the HTTP call:**
  - `POST {base_url}/api/embed` with `model`, `input` (a list), `keep_alive` and `options.num_ctx`;
  - `urllib.request` with the `request_timeout_seconds` timeout;
  - reads `embeddings` from the response.
- **After the call:** keep the first `dimensions` values, L2-normalise, convert to `float32`. Fewer values than `dimensions` raises `INVALID_CONFIGURATION`.
- **`check_embeddings(settings)`**, used by `cw doctor` and at the start of `cw context build`: `GET {base_url}/api/tags` confirms the model is pulled, then one short text is embedded to confirm the vector length. Every failure has a clear detail.
- **Errors:** unreachable, HTTP errors, timeouts and malformed responses raise `CONTEXT_INDEX_FAILED`, with the URL and model in the detail, never the embedded text. At ticket time that becomes a `Context:` warning; in `cw context build` it stops the build.
- No cache inside the module (the `embeddings` table is the cache), and no retries.
- **Manual check, reported in the hand-over:** two related texts and one unrelated through Ollama; the related pair must be clearly more similar. If a reference implementation is available elsewhere (the model card's `sentence-transformers` snippet), compare one query vector with Ollama's: a cosine near 1 means the query format and pooling match.

### 5.3 What gets embedded

- **Code:** every chunk of the snapshot, as a header plus the chunk:

  ```
  path: <path>
  symbol: <qualified_name><signature>
  doc: <docstring first line>
  <chunk text>
  ```

  The cache key is the `sha256` of that text. One function builds it, and both embedding and gc use it.
- **Tickets:** `title + request + plan_excerpt + notes`, one vector per record.
- **Never:** protected paths, ignored directories, oversized files and non-code files. Tests are embedded like code.

```sql
CREATE TABLE embeddings (
    model_key TEXT NOT NULL, text_hash TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (model_key, text_hash)
);
```

`model_key` is `"<model>@<dimensions>"`. Vectors are stored as `float32` bytes.

### 5.4 Dense channel

- At search time, load the snapshot chunks' vectors into one matrix with a parallel `(path, chunk)` list, compute `matrix @ query_vector`, and take `dense_top_k`. Brute force: 20k chunks × 1024 values is 80 MB. Loading dominates the time; report it.
- The dense ticket search works the same way over ticket vectors and is fused into the history channel.
- **Embedding on update:**
  - automatic updates (ticket start, finish) embed the missing chunk texts in batches, unless more than `max_incremental_files` files changed since `indexed_commit`. Then they skip embedding and add the `Context:` warning to run `cw context build`;
  - `cw context build` embeds everything missing, prints progress (chunks done, chunks per second, time left), and stops cleanly on Ctrl+C: committed batches stay, and the next build continues.

### 5.5 Tests

Tests never call Ollama: monkeypatch the private HTTP function to return deterministic vectors derived from a hash of each input.

- The query template applies to queries only.
- Batching keeps order, and `options.num_ctx` is sent.
- Vectors are cut to `dimensions` and have unit length.
- A second update embeds nothing; changing `model` or `dimensions` embeds again.
- Ollama unreachable at ticket time gives a warning and a seed from the other channels; in `cw context build` it exits with `CONTEXT_INDEX_FAILED`.
- `check_embeddings` reports a model that isn't pulled.
- The `max_incremental_files` cap skips embedding and warns.
- The dense channel ranks a file with no shared keywords (the fake encoder gives two specific texts one vector).

**Manual, in the hand-over:** first build time, chunks per second, database size, and `build_seed` time on the largest project.

---

## 6. Step 5: HyDE query expansion

Before the dense search, Haiku writes a short sketch of the code the change would touch: likely paths, class and function names, and 10–20 lines of plausible code. The sketch is embedded as a **document** (no query instruction). Its dense results are fused as their own channel, next to the raw request's, never instead of them: CORE-Bench found rewritten queries can lose the request's exact names and error strings.

### 6.1 A run that stays cheap

The sketch runs through `claude_runner` as a new role, `AgentRole.CONTEXT`, with the agent file `toolkit/plugins/core/agents/context-sketcher.md`. A plain agent run would cost about 9k tokens of pinned rules and could reach every tool, so this role is different:

- **Tools:** the agent file says `tools: StructuredOutput`. With an empty list, `claude_runner.py:276` would send no restriction, and the agent would get every tool.
- **No rules, no plugins:** for `AgentRole.CONTEXT`, `run_agent` appends no rules file and passes no `--plugin-dir`. So no pinned skills, no topics, no hooks.
- **No repository:** it runs in a new temporary folder, so it loads no project `CLAUDE.md` or settings and cannot read the code.
- **Input:** the request, the project's stack name, and the project's folder names to two levels, read from the snapshot without an LLM, at most a named constant of them. Then its guessed paths follow your layout.
- **Output:** `HydeSketch` in `models.py`: `paths: list[str]`, `symbols: list[str]`, `code: str`, cut to `hyde_max_chars`.
- **Model and turns:** `RoleModels.context: str = "haiku"`, `RoleTurns.context: PositiveInt = 2`. With `--json-schema` the answer comes through the StructuredOutput tool; test once with the real CLI whether 1 turn is enough and lower the default if so.
- **Cost:** recorded in `ticket.runs` with the label `context`. The run is built once per ticket, and the stored `context_seed` is reused by the planner, by a replacement session and by an escalation. Eval runs go to the `eval-<project>` event file (§2.3).

**Agent prompt (short):** from the request and folder names only, sketch where the change would land and what the code would look like; never claim certainty; no prose.

### 6.2 Use

- `paths` and `symbols` are added to the lexical query terms, so exact guessed names that exist in the index match.
- `code` is embedded and searched as its own dense channel.
- **Failure:** any error or timeout gives a `Context:` warning, and the seed is built without HyDE.
- **Settings:** `ContextSettings.hyde_max_chars: PositiveInt = 2000`.
- **FRIDAY:** a `context` run appears in the ticket's Cost tab and in Usage; `RoleModels.context` and `RoleTurns.context` go into `Models.swift` and the model settings.

### 6.3 Tests

- With `FakeAgents` extended for the role, the sketch's symbols reach the lexical query, and its code is embedded as a document.
- A failing sketch still produces a seed.
- The run is in `ticket.runs` with the label `context`, and a lost planner session does not run it again.
- The run for `AgentRole.CONTEXT` has only the `StructuredOutput` tool, no `--append-system-prompt-file`, no `--plugin-dir`, and a working directory outside the checkout.

---

## 7. Step 6: notes go to ticket records, not `CLAUDE.md`

Tudor's decision: workers' notes stop going into `CLAUDE.md`, which every agent loads on every run. They live in ticket records and come back only when the history channel finds the ticket relevant.

- `pipeline` stops calling `project_memory.add_notes`. Notes stay in `work_log[*].project_notes` and go into the record at finish (4.1).
- The existing `## Notes kept by cw` section in projects' `CLAUDE.md` is left alone. `cw context migrate-notes <project>` copies its lines into a synthetic record (`ticket_id` `notes-import`, `finished_at` now). Running it twice imports nothing new. Removing the section is the user's choice.
- `worker.md`: notes are kept with the ticket and shown to later tickets on a similar request. The field stays optional with today's caps: at most two per run, five per ticket.
- The reviewer prompt's sentence about the "Notes kept by cw" section, and the check that adds it, are removed.
- `project_memory.py` keeps only what other code still uses; dead functions are deleted.
- **In the hand-over:** planner and worker input tokens per run before and after, from the token benchmark.

**Tests:**
- `CLAUDE.md` is unchanged after a worker pass;
- the notes are in the record;
- `migrate-notes` imports the section once.

---

## 8. Step 7: seed for direct tickets

- When a direct ticket's first worker run starts fresh (no `worker_sessions` entry and no `work_log`), build the seed and add the same section to its first prompt, passed in as a parameter.
- Fill `context_seed` and `seed_files`. If the worker escalates with `needs_plan`, the planner reuses the stored seed: no second `context` run.

**Tests:**
- a direct ticket's first prompt has the seed;
- a follow-up in the same session does not;
- an escalation adds no second `context` run.

**In the hand-over:** the token benchmark's small-fix tickets before and after. If they cost more, say so plainly.

---

## 9. Step 8: storage hygiene

The index keeps every old version of a changed file and its vectors until something removes them, and ticket event logs grow. This step bounds both. Nothing touches the project's repository.

### 9.1 Settings

A top-level group `storage: StorageSettings = Field(default_factory=StorageSettings)`:
- `max_index_mb: PositiveInt = 1024`;
- `event_retention_days: PositiveInt = 30`.

### 9.2 What is obsolete

| Data | Obsolete when | Rule |
|---|---|---|
| Blobs and everything parsed from them | In none of the **retained trees** | Delete |
| Code vectors | Their text hash matches no chunk of a retained tree | Delete |
| Ticket record vectors | The record no longer exists | Delete |
| Vectors under another `model_key` | Model or dimensions changed | Delete |
| Ticket records | Never, while the project is registered | Keep |
| A closed ticket's `events.jsonl` | `done`/`abandoned` and `updated_at` older than `event_retention_days` | Delete the events file only |

**Retained trees:**
- the current `snapshot`;
- the tip of the project's `base_branch`, local and remote;
- the tip of every branch an **open** ticket targets (`_target`), local and remote.

Each is read with `git ls-tree -r -z` and the §3.5 filters. A missing ref is skipped with a debug log. Searching still uses the snapshot set at ticket start.

**The eval trade-off:** eval recomputes old blobs and vectors, and a later gc removes them again. Disk stays bounded; eval is occasional. The elapsed time is printed.

### 9.3 `cw gc`

`cw gc [<project>] [--dry-run] [--json]`; without a project, it runs for every registered project.

1. **Index:**
   - take `project_lock`;
   - delete what 9.2 marks obsolete in one transaction;
   - commit, then `VACUUM` outside it.
2. **Events:** delete the project's obsolete event logs.
3. **Report:** size before and after, rows and files deleted. `--dry-run` changes nothing.

The live vector hashes are rebuilt with the same header function as embedding (5.3).

**When gc runs on its own:**
- the index part after `cw context build` and `cw context eval`;
- the events part when a ticket of the project finishes;
- never at ticket start (`VACUUM` can take a while).

**Otherwise it warns:** when the index passes `max_index_mb` at ticket start, a `Context:` warning suggests `cw gc <project>`, and `cw context status` and `cw doctor` show the same.

### 9.4 `cw project remove`

- `cw project remove <name>` also deletes the rebuildable caches: `context.sqlite3` and `repo_map.txt`.
- `--purge` also deletes the rest of `~/.cw/projects/<name>/` (including `conventions.md`) and `~/.cw/tickets/<name>/`, after printing what it will delete and asking, unless `--yes` is passed.

### 9.5 Visibility

- `cw context status` shows the size and what gc would free.
- `cw doctor` shows the size of `~/.cw` and, per project, the index, the tickets and the events, flagging indexes over `max_index_mb`.
- **FRIDAY:** a closed ticket with no events shows "Activity log removed after N days" in its Activity tab, reading `event_retention_days` (in `Models.swift`, optional).

### 9.6 Tests

- **gc keeps and deletes the right data:**
  - it deletes blobs and their rows in no retained tree, and keeps those in one;
  - two bases: an open `--base develop` ticket while the snapshot is on `main` keeps both; after the ticket closes, the next gc removes what only `develop` had;
  - index `main`, then `develop`, then gc, then `main` again: the last update embeds nothing;
  - a missing retained ref is skipped;
  - old texts' vectors and other `model_key`s are deleted; live code and record vectors are kept; records survive.
- **The file shrinks:** `VACUUM` makes it smaller after a deletion, and `--dry-run` reports what a real run deletes.
- **Event logs:** deleted only for closed tickets past retention; never an open ticket's; `cw events` on a removed log returns an empty list.
- **After `cw context eval` and gc:** only the retained trees' blobs remain.
- **The size warning** appears at ticket start (`max_index_mb` set to 1).
- **`cw project remove`:** the default keeps `conventions.md` and the tickets, `--purge --yes` deletes them, and `--purge` with a "no" deletes nothing.
- **The index is never inside the checkout:** no path under `project.path`.

---

## 10. Step 9: docs, doctor, final report

- **`EXPLANATION.md`:**
  - a "Context engine" section: channels, HyDE and its cheap run, fusion, the graph channel, blob keys, the best-effort rule, what is and isn't indexed, the Python-only graph, the seed replacing the repo map, and records being per person;
  - the planner, worker, memory, review and pull request sections updated.
- **`OVERVIEW.md`:** a paragraph, the Ollama setup and the new commands.
- **The repo's `CLAUDE.md`:** the new modules in the layout.
- **`CHANGES_AND_ISSUES.md`:**
  - no config edits are needed (everything has defaults); install Ollama, `ollama pull qwen3-embedding:8b-q8_0`, then `cw context build` per project;
  - each step's eval table and token benchmark;
  - the "not verified" list.
- **`config.example.json`:** every new setting with its default.
- **`cw doctor`:**
  - FTS5;
  - Ollama reachable at `base_url`, the model pulled, the vector length right (`check_embeddings`);
  - per project, whether a context database exists and is current;
  - the storage report.
- **Docs state** that the index lives in `~/.cw`, is never committed to a project's repository, and is bounded by `cw gc`.
- If `PROJECT_GUIDE.md` is added to the repository later, its §7 follows this plan: the seed replaces the map, the graph is a channel, HyDE runs without rules or tools, re-reviews run on Sonnet, and comment batches are gone.

**Final report:**
- `cw context eval` with every method on the largest project, from tickets and from commits;
- the token benchmark against the $9.04 baseline;
- build time, database size before and after `cw gc`;
- `build_seed` time with and without HyDE.

---

## 11. Definition of done

- [ ] All new behaviour has tests; `uv run pytest` is green, and no test touches the network, Ollama or `claude`.
- [ ] `uv run ruff check src tests` and `uv run ruff format --check src tests` are clean; FRIDAY builds and `./test.sh` passes.
- [ ] No comments in code; one-line docstrings; no magic values; every tunable value lives in settings, and every new field has a default.
- [ ] An existing `config.json` from `main` loads unchanged, and saving it from FRIDAY drops no key.
- [ ] A ticket still starts, plans, works and finishes with the context database missing, corrupt or locked; with Ollama down or the model not pulled; and with the HyDE run failing. Each is tested.
- [ ] The token benchmark after step 7 costs less than the $9.04 baseline, or the hand-over says plainly which part costs more and why.
- [ ] Eval tables (tickets and commits) in each hand-over, with sample counts and the `approximate` count.
- [ ] After `cw gc`, the database holds only the retained trees' blobs and vectors and the ticket records; nothing cw writes is inside a project's checkout.
- [ ] Docs updated (§10).
- [ ] Each hand-over says what was not verified (for example: retrieval on Swift or Java, build time on repositories larger than the one tested, the Bitbucket thread replies).

---

## 12. Changes from the first version of this plan

| First version | Now | Why |
|---|---|---|
| New settings without defaults; edit `config.json` by hand | Every field and group has a default; FRIDAY saves only what it edits | Required fields broke every existing config and `cw doctor`; FRIDAY dropped unknown keys on save |
| Comment batches | Removed | With sequential workers, batches mean more passes; per-comment outcomes already show a skipped comment |
| `cw fix` reran the full review loop each round | One review per round | The loops multiplied: 7 reviewer runs on one ticket |
| Every review on Opus | Re-reviews on `reviewer_followup` (Sonnet) | Reviews are 47% of the cost |
| Review skip ignored blocking findings posted as threads | Those count as blocking | Otherwise the finding stays open forever and `cw merge` never passes |
| Declined threads resent every round | Skipped until a person replies | Wasted rounds |
| Scope warning on any file a fix touched | Only `README.md`/`CLAUDE.md` | Tests for a comment always warned |
| Seed added next to the repo map | The seed replaces the map | Fewer tokens is the goal |
| PageRank was the final order | PageRank is one fused channel | Hub files outranked the actual matches |
| FTS5 default tokenizer | `tokenchars '_'` | `_` split identifiers, so "keep the whole name" did nothing |
| `git ls-tree --full-tree` | Project-relative paths | Paths mismatched for projects in a subfolder |
| Eval snapshot in a rolled-back transaction (one option) | TEMP table only | A rollback discarded the cached blobs and vectors |
| Eval on finished tickets only | Also `--from-commits`; a token benchmark | 10 tickets exist; the goal is tokens |
| HyDE as a plain agent run | No tools but StructuredOutput, no rules, no plugins, a temporary folder | It would have had every tool and about 9k tokens of rules |
| No Ollama context setting | `context_length` → `options.num_ctx` | Long chunks could be cut silently |
| Notes: up to three per pass | Two per run, five per ticket | Matches the code |
| "Only the current snapshot after gc" | Only the retained trees | Matched §9.2.1 |
