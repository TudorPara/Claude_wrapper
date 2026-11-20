# How this project works

This document explains the whole project as it is today. It covers the idea, the concepts, the architecture, the workflow step by step, every agent, skill and hook, how the agents communicate, how one agent run is put together, the configuration, FRIDAY, the context engine (§19), and what is and is not verified. §17 tells, in order, how the project got here: every refinement, fix and optimization, and why it was made.

Setup and daily commands are in [FIRST_RUN.md](FIRST_RUN.md) and [README.md](README.md). The detailed log of each change, with the bugs behind it, is [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md).

---

## 1. The idea

A small team works on several projects with Claude Code. The goals:

1. **Centralize.** One shared toolkit of agents, skills, rules and hooks, so every teammate and every project works the same way and the code looks the same.
2. **Simple and cheap.** Each job goes to the right model with only the context it needs, in a fixed and predictable flow. The project is judged by three things: results at least as good as plain Claude Code, a multi-agent flow, and a lower cost and use of the 5-hour usage window (the same tokens on a cheaper model, or read from the cache, count as a saving).
3. **Keep quality.** Deterministic checks (lint and tests), a strict review before every pull request, and a human who approves plans and merges pull requests.
4. **From an idea to a project.** `cw new` starts a repository from one sentence and plans the whole thing, so a project can be built without touching the code.

The repository has three parts:

| Part | What it is |
|---|---|
| `toolkit/` | A Claude Code **plugin marketplace**: the team's shared agents, skills, rules and hooks. It is meant to live on GitHub. |
| `backend/` → `cw` | A small Python CLI that runs the toolkit's agents on **tickets**, one branch per ticket, in the project's own checkout. |
| `friday/` → FRIDAY | A macOS SwiftUI app over cw: projects, tickets, every agent of a ticket and what it is doing, the commands as buttons, and a box you can type or talk into. It only runs `cw` and reads its `--json` output. |

There is no server, database, daemon or custom agent framework. Claude Code does the agent work; cw decides which agent runs, with what context, and what happens next.

---

## 2. Concepts

### Claude Code concepts used here

| Concept | What it is | Where it appears |
|---|---|---|
| **Session / context window** | One conversation with Claude. Everything in it is sent again on every turn, so it costs tokens on every turn. Parts that did not change are read from the prompt cache at about a tenth of the price. | The planner has one session per ticket; each worker has one session for all its tasks. |
| **Resuming a session** | `claude -p --resume <id>` continues a session. Its history is still cached for a while, so continuing is cheap, and the agent keeps what it has read. | A worker's next task, a follow-up, a fix, an answer; the planner's next feedback round. |
| **CLAUDE.md** | Instructions loaded into every session started in that folder. | A project's `CLAUDE.md` reaches every cw agent; cw no longer writes into it (workers' notes live in ticket records, §6). |
| **Skill** | A folder with `SKILL.md`. Interactively, only its description is in context until it is needed. | `engineering`, `python-rules`, `pr-review`, `rule-precedence`, `ponytail`, `clean-code`, `bootstrap-project`. |
| **Agent (subagent)** | A markdown file whose front matter sets `name`, `description`, `tools` and `model`, with the instructions in the body. `claude --agent <name>` runs a whole session as that agent. | `planner`, `worker`, `reviewer`, `onboarder`, `context-sketcher`. |
| **Hook** | A command Claude Code runs on an event (`PreToolUse`, `PostToolUse`, …). Exit code 2 sends its message to Claude; before a tool call it also blocks the call. It runs every time, unlike a skill, which is only advice. | `guard.py`, `rules_on_touch.py`, `ruff_on_edit.py`. |
| **MCP server** | An external tool server. Each server's tool definitions cost context on every turn. | Only the project's own `.mcp.json`, when it has one (§9). |
| **Plugin / marketplace** | A plugin bundles agents, skills, commands and hooks. A marketplace is a git repository listing plugins. | `toolkit/` with the `core` and `python` plugins. |
| **Headless mode** | `claude -p` runs one prompt without the interactive UI; `--output-format stream-json` prints every step as a JSON line; `--json-schema` forces a structured answer. | Every cw agent run. |
| **Agent SDK** | A Python/TypeScript library that runs the Claude Code binary from your own code. | Not used, because of subscription login (§13). |

### cw's own words

| Word | Meaning |
|---|---|
| **Project** | A registered git repository, with its base branch, remote, stack, lint and test commands, protected paths and a profile of models and limits (`~/.cw/config.json`). |
| **Ticket** | One unit of work: one branch (`cw/<ticket id>`), at most one pull request. Its state is `~/.cw/tickets/<project>/<id>.json`, with a readable twin `<id>.md`. Ids look like `261118-add-discount-codes-3fa2`. |
| **Route** | `direct`: the request goes straight to a worker. `planned`: the planner writes a plan and a task list first (`--plan`, or when a worker escalates). |
| **Task** (`S1`, `S2`, …) | One step of the plan, done by one worker, in order. |
| **Worker** (`W1`, `W2`, …) | A worker session. Related tasks share a worker so it keeps what it has read; another worker is for an unrelated area. They take turns in one checkout. |
| **Check** | The project's lint or test command, run by cw itself. |
| **Baseline** | The checks run once on the untouched base when the ticket starts. Only checks that pass there are enforced later. |
| **Fix round** | One more worker run to fix failing checks, review findings, PR comments or merge conflicts. Every kind has a cap. |
| **Finding** (`F1`, `F2`, …) | A review comment, `blocking` or `minor`. Blocking ones stay on the ticket's open list until a review says they are fixed. |
| **Step** | The resumable unit of the flow: `plan`, `work`, `review`, `pr`. It is recorded before it runs, so `cw resume` can always run it again. |
| **Status** | Where the ticket is (§4.2). |
| **Toolkit** | The local clone of the marketplace that cw loads into every run with `--plugin-dir`. |
| **Context index** | A per-project SQLite file, `~/.cw/projects/<project>/context.sqlite3`: a searchable catalogue of the code at one commit, keyed by git blob, with the embeddings of its code and the records of finished tickets (§19). |
| **Context seed** | The short list handed to the planner (or a direct ticket's worker) for one request: the most relevant files and definitions with line numbers, the similar finished tickets, and a folder outline, within a token budget. It replaces the repo map. |
| **Ticket record** | What a finished ticket leaves for later tickets: its request, plan excerpt, changed files, open findings and workers' notes. Built without a model, kept in the index. |
| **Channel** | One way the context engine finds code: keywords, paths, history, meaning (embeddings), the HyDE sketch, and the dependency graph. |

---

## 3. Architecture and where things live

```
            toolkit (one git repo, shared by the team)
            ├─ core:   agents planner · worker · reviewer · onboarder
            │          skills engineering · pr-review · rule-precedence · ponytail · clean-code · /core:bootstrap-project
            │          hook guard.py (blocks dangerous shell commands)
            └─ python: skill python-rules (index + topics/: the nexttech-claude-kit rules, without reviewers' names)
                       hooks rules_on_touch.py (shows a topic) · ruff_on_edit.py
                 ▲ loaded into every cw run (--plugin-dir)        ▲ installed by each teammate's Claude Code
                 │                                                │ when a repo is wired (.claude/settings.json)
FRIDAY ──runs──► cw ──claude -p --agent …──► Claude Code ──edits──► the project checkout, on branch cw/<ticket>
  ▲ reads --json  │
  └───────────────┤ ~/.cw/config.json                       projects, models, limits, stacks
                  ├ ~/.cw/tickets/<project>/<id>.json + .md  tickets = state + the readable record
                  ├ ~/.cw/tickets/<project>/<id>.events.jsonl every agent start, tool call, end and check
                  ├ ~/.cw/projects/<project>/repo_map.txt    the planner's map of the repository (fallback)
                  ├ ~/.cw/projects/<project>/context.sqlite3 the context index: code, vectors, ticket records
                  ├ ~/.cw/projects/<project>/conventions.md  optional: onboarding's findings and decisions
                  ├ Ollama at localhost:11434 ──► embeddings (qwen3-embedding), on your Mac
                  └ gh / Bitbucket REST ──► pull request, review comments, CI, merge
```

Change the toolkit once, push it, and every teammate and every cw run uses the new version.

**What cw writes into a project's repository:** only the agents' code changes, in the ticket's diff, reviewed and committed once. `.claude/settings.json` is written only when you ask (`cw project wire <name>`). The context index, the ticket records and the event logs live in `~/.cw` and are never committed.

**The backend, file by file** (`backend/src/cw/`):

| File | What it does |
|---|---|
| `cli.py` | Every `cw` command; each view has a `--json` form for FRIDAY. |
| `pipeline.py` | The ticket flow: branch, baseline, planner, workers, checks, review, commit, merge of the base, PR, `cw fix`, `cw merge`, resume, park, split, done. |
| `prompts.py` | The one place that builds what each agent is told. |
| `claude_runner.py` | One `claude -p` run: the command line, the rules for the system prompt, permissions, the environment, reading the stream, the structured answer and the usage. |
| `process.py` | Child processes in their own process group, with a time limit; Stop and Ctrl+C kill everything they started. |
| `tickets.py` | Ticket files, the `.md` twin, the events file, the per-project lock. |
| `models.py` | The ticket and the structured answers of each agent. |
| `config.py` | `~/.cw/config.json`, validated on every run. |
| `git.py` | The git commands cw needs. |
| `git_hosts/` | GitHub (through `gh`) and Bitbucket Cloud (REST): PRs, reviews, threads, CI, merge. |
| `project_memory.py` | Reads the old "Notes kept by cw" section of a project's `CLAUDE.md`, for `cw context migrate-notes`. |
| `code_chunks.py` | One file's text → chunks, definitions, references and imports (Python's `ast`; other languages as a card). |
| `context_index.py` | The SQLite context index: schema, blob ingestion, snapshots, embeddings, ticket records. Owns all its SQL. |
| `context_search.py` | Ranking: the channels, rank fusion, the dependency graph and PageRank, and the rendered seed. |
| `embeddings.py` | Ollama's `/api/embed`: queries with the task instruction, documents without; vectors cut and normalised. |
| `context_engine.py` | The context engine at ticket time: refresh at start and finish, the seed (with the HyDE sketch), ticket records. |
| `context_eval.py` | `cw context eval`: recall and MRR from finished tickets or from git history. |
| `storage.py` | `cw gc`, event-log retention, project caches. |
| `benchmark.py` | `cw audit` and `cw benchmark`: quality and cost per ticket, for comparing versions of cw. |
| `repo_map.py` | The planner's map of a repository, made without an LLM. |
| `onboarding.py` | `cw project add`, `cw new`, the optional onboarder, `cw project wire`. |
| `toolkit.py` | Reads agents, skills and topics from the toolkit. |
| `reports.py` | `cw stats`, `cw project list`, `cw toolkit`, `cw doctor`. |

**What is not centralized yet:** the toolkit isn't pushed to GitHub, so teammates' interactive Claude Code only picks it up after that and after a repository is wired; each person has their own `~/.cw/config.json`, so models and limits are personal.

---

## 4. The workflow

### 4.1 The whole flow

```
you: prompt (terminal, FRIDAY or voice)        cw new <name> "<idea>" also creates the repository first
 │
 ├─ checkout must be clean ─► cw: git switch -c cw/<ticket> <base, or --base branch>
 │                            ─► containers up (if any) ─► baseline: run lint + tests once
 │
 ├─ default ─► worker W1 ──"needs a plan / several branches"──┐
 │                                                            ▼
 └─ --plan ─────────────────────────────────────────────► planner
                                                           ├─ several changes? → cw split (one branch + PR each)
                                                           ├─ questions? → you answer
                                                           └─ plan + ordered tasks, each given to a worker → you approve
                                       (the planner, or a direct ticket's first worker run, starts from the context seed)
                                                                      │
                                       S1 (W1) ─► S2 (W1, same session) ─► S3 (W2, new session) ─► S4 (W1) …
                                       a worker's question ─► planner answers ─► or you do
                                       after every task: checks ─ broke? ─► up to 2 fix rounds by that worker
                                                         ─ still broken? ─► one fresh session from its reflection
                                                         ─ still broken? ─► the ticket stops (BLOCKED)
 │
 ▼  READY: changes are uncommitted in your checkout · reply to adjust · cw review · or Park
you: cw pr
 │
 ▼
checks ─► reviewer (Opus, read-only, always) ─ blocking findings open? ─► fix round on every finding,
 │        re-review (Sonnet) only the new diff (up to 2 rounds); cw decides from the severities
 ▼
cw: one commit → merge base (conflicts → worker → checks → review of those files) → push → open PR
 │        → post the review on it; no remote: stops after the commit; cw done merges it into the base
 ▼
you or teammates comment on the PR → cw fix: the last worker answers each open thread (fixed or declined)
 │        and every open blocking finding → checks → one review (skipped for a small comment-only fix)
 │        → new commit → push → summary posted → fixed threads resolved, declined ones get a reply
 │        → again while blocking findings stay open (up to 3 rounds)
 ▼
you: cw merge → refused while CI has not passed on your commit, a blocking finding is open,
 │       or a PR thread is unresolved
         → squash-merge pinned to that commit → branch deleted there and locally → local base fast-forwarded
         → you are back on your previous branch     (or merge on the host yourself, then cw done)
         → the index moves to the new base and the ticket's record is stored for later tickets
```

### 4.2 Ticket statuses

| Status | Meaning | What you can do |
|---|---|---|
| `in_progress` | An agent or a check is working (or a killed process left it here). | Wait; Stop; `cw resume` if nothing is running. |
| `needs_input` | The planner or a worker has questions, or the planner proposes a split. | Answer with `cw run <id> "…"`, or `cw split <id>`. |
| `awaiting_approval` | A plan is ready. | `cw approve <id>`, or give feedback with `cw run <id> "…"`. |
| `ready` | The work is done and the checks ran. Changes are uncommitted in your checkout. | Follow up, `cw review`, `cw pr`, or Park. |
| `in_review` | The checks and the reviewer are running. | Wait. |
| `blocked` | A real error stopped a step (git, the host, an agent failure, checks still failing in a task list, Ctrl+C). | Fix the cause, then `cw resume <id>`. |
| `paused` | The usage limit was reached, or a split ticket waits to be started. | `cw resume <id>` later. |
| `pr_open` | Committed; the PR is open (or, without a remote, the commit waits on the branch). | `cw fix`, `cw merge`, or `cw done`. |
| `done` / `abandoned` | Closed. | Read it in history (`cw show <id>`). |

### 4.3 Step by step

**1. Starting a ticket** (`cw run <project> "<prompt>" [--plan] [--base <branch>]`)
- cw refuses when the checkout has uncommitted changes, and says how to park the ticket that is on it.
- The title is the prompt's first line; the id is the date, a slug and four random characters.
- cw fetches the base branch (a failed fetch is a warning on the ticket: "pushing will fail the same way") and runs `git switch -c cw/<id> <base>`. It remembers the branch you were on, to return there later.
- It starts the project's containers when it has a compose file (§4.4), then runs the **baseline**: each check once on the untouched base. A check that already fails is reported and never enforced, so no worker is sent to fix someone else's problem. Files the checks leave behind (a new lockfile, a report) are named in a warning, because they would otherwise end up in the commit.
- It refreshes the planner's repo map, and brings the context index to the commit the ticket starts from (only files that changed since the last update are parsed). On the first ticket of a project it builds the index without embeddings and warns you to run `cw context build` once.
- If the containers do not start, the ticket is not created and the checkout goes back to where it was.

**2a. The direct route** (default)
- Worker `W1` gets the whole ticket, the context seed for the request (§19), and "Do now: <your prompt>". On the first pass it is told: if this needs more than `escalate_file_threshold` (5) files or a plan, return `needs_plan` before editing. Then the ticket moves to the planned route, with the worker's reason given to the planner.
- After the worker, cw runs the checks. A check the ticket broke gets up to `max_fix_rounds` (2) fix rounds; if it still fails, the ticket is `ready` with a `Checks: now failing` warning, so you decide.

**2b. The planned route** (`--plan`, or after an escalation)
- The planner gets the request and the context seed (§19) in place of the repo map: the files and definitions most likely to matter, similar finished tickets, and a folder outline. It is told to verify them, then reads the code it needs and answers with one of:
  - **split**: the request is several independent changes that each need their own branch or PR. The ticket waits; `cw split <id>` creates one paused ticket per change and abandons the original, without running an agent. `cw resume <new id>` plans each one.
  - **questions** (at most 3, at most `max_question_rounds` rounds): the ticket waits for your answer.
  - **a plan**: what changes and why, the options it rejected, an ordered task list (each task with its worker, complete instructions, its files and a `Done when:` line), and a **context pack** of 5–15 `path:lines` the workers should read first.
- A planner that still asks after its last round is told once to plan with stated assumptions; if it still gives no plan, the ticket waits for you. An empty plan never reaches approval.
- Feedback on a plan (`cw run <id> "…"`) continues the planner's session and sends only the feedback, not the whole request again.
- `cw approve <id>` starts the tasks.

**3. Doing the tasks** (`cw approve`)
- Tasks run in order. Each goes to its worker: a worker with a session continues it and gets only what other workers did since its last turn, plus its task; a worker without one gets the whole ticket.
- **After every task cw runs the checks**, with the fix rounds above. If a check still fails, the task's worker gets one fresh session that starts from its last attempt's reflection (`max_backoff_rounds`, 1), with fix rounds of its own. A check still failing after that stops the ticket (`blocked`), so the next task never builds on broken code. The cause is often outside the code (a missing setting, a database that is not running); the worker's summary says so. Fix it and `cw resume`: the task runs again, then the checks.
- When the last task passes, the ticket is `ready`.

**4. Questions on the way**
- A worker that lacks something returns `question`. cw asks the planner in the planner's own session, where the plan still is. What the planner cannot answer (a product choice, a secret) goes to you, and your answer reaches the worker in its session.
- A direct ticket has no planner, so its worker asks you.
- During a fix (checks, findings, comments, conflicts) workers may not ask: they choose the simplest option and say what they assumed.
- After `max_question_rounds` a worker is told nobody can answer further and to decide.

**5. Following up** (`cw run <id> "…"` on a `ready`, `blocked`, `paused` or `pr_open` ticket)
- The prompt goes to the last worker, in its session, as "Do now". Then any unfinished tasks run, then the checks.

**6. Reviewing** (inside `cw pr`, or alone with `cw review`, which commits nothing)
- cw runs the checks with their fix rounds, then the reviewer gets the request, the plan, the workers' notes (marked as claims to verify), how the checks ended, the open blocking findings and the diff since the last review (the first time, since the base).
- The reviewer returns findings, each `blocking` or `minor`, and the ids of open findings the code now fixes. **cw decides from the severities**, not from a verdict.
- Findings on files that do not exist are dropped; a line past the end of a file moves to its last line. Each finding gets a ticket-wide id.
- While blocking findings are open and rounds are left (`max_review_rounds`, 2), the last worker gets every open blocking finding plus the round's minor ones, the checks run again, and the reviewer sees only the new diff.
- **Every review after a ticket's first runs on `reviewer_followup`** (Sonnet by default): a re-review only checks a small fix against listed findings. The first review stays on the reviewer model (Opus).
- Fix passes keep no project notes, and editing `README.md` or `CLAUDE.md` without a finding or comment naming them gets a `Scope: fix:` warning.
- Findings still open after the last round stay on the ticket and go on the PR; you decide.

**7. Opening the PR** (`cw pr`)
- It refuses a ticket with no changes.
- Checks, then the review (step 6). Nothing skips it.
- **One commit**: everything since the base, WIP commits from parking included, is squashed into one commit with the ticket's message. Once the branch is pushed, later changes (`cw fix`, a second `cw pr`) are added as new commits; the squash merge folds them into one again.
- cw merges the latest base into the branch. Conflicts go to the worker (`max_conflict_rounds`, 1); cw checks that no conflict markers are left, commits the merge, then runs the checks and reviews the resolved files before anything is pushed. A clean merge brings only the base's own reviewed work, so the review moves past it.
- No remote: it stops after the commit (`pr_open`, with a warning); `cw done` merges the branch into the base.
- Otherwise: push, open the PR (the description is built from the ticket: request, plan, what each pass did, the review), and post the review as line comments. Each finding carries its id (`**blocking** [F3] …`), so a thread can be matched to its finding even when the text is reformatted.

**8. Working off PR comments** (`cw fix`)
- It reads the unresolved comment threads on lines of the PR (the reviewer agent's and people's) and the ticket's open blocking findings, and gives them all to the last worker in one instruction. Every thread is labelled with its id, and the worker answers each one: `fixed`, or `declined` with the reason.
- Then it runs the same steps as `cw pr`, with **one** review per round (no review-and-fix loop inside it): checks, review, a new commit, push. It posts the worker's summary, resolves the `fixed` threads, and replies in each `declined` thread with the reason (starting with `cw · declined:`), leaving it open for a person. A thread with no answer stays open. A declined thread is not sent again until someone replies after cw.
- **A small comment-only round skips the review**: when it addressed no open blocking finding (also none posted as a thread), the checks pass, and it changed at most `review_skip_max_changed_lines` (20) lines and no protected path. The summary says the review was skipped and why. `cw pr` never skips.
- Only the first review of a PR is posted as line threads. Later reviews are posted as one comment, so cw's own re-reviews never feed the next round; their blocking findings stay open on the ticket instead.
- It goes round again while blocking findings stay open, up to `max_pull_request_fix_rounds` (3), and stops after a round in which the worker changed nothing. Comments in the PR's general conversation are not read.

**9. Merging** (`cw merge`)
- It refuses while a blocking finding is open, while **any PR thread is unresolved** (the error lists which were declined by the worker and which were not addressed), while the ticket's branch has uncommitted changes, or while CI has not passed **on your local branch's commit**. The first review posts minor findings as threads too, so they also need `cw fix` or a person before the merge. A PR with no checks reported counts as passed only when the branch has no CI config (`.github/workflows`, `bitbucket-pipelines.yml`), so a merge right after a push waits for CI. On GitHub the PR's head must also be that commit.
- It squash-merges on the host with the ticket's commit message, **pinned to that commit**: GitHub refuses the merge if the head moved; on Bitbucket cw compares the head just before merging.
- It deletes the branch on the host, fast-forwards your local base branch (a warning if it has diverged), switches you back to your previous branch, deletes the local branch, refreshes the repo map and closes the ticket.
- When a ticket finishes (`cw merge` or `cw done`), cw records `changed_files` (the ticket's own changes, not what it merged in from the base) and the merge commit, brings the context index to the tip of the base, and stores the ticket's record for later tickets. Event logs of tickets closed longer than `event_retention_days` (30) are removed.
- Or merge on the host yourself and run `cw done`.

**10. Park, resume, done, abandon**
- `cw park <id>` saves the work as a WIP commit and switches back; it is folded into the one commit at PR time.
- `cw resume <id>` runs the recorded step again. A task list continues at its first unfinished task. A ticket left `in_progress` or `in_review` by a killed process resumes the step it was in; a killed `cw review` resumes as a review and never opens a PR.
- `cw done <id>` closes the ticket and switches back (merging locally first when there is no remote). `--abandon` discards the changes and deletes the branch.

### 4.4 Rules that always hold

- **One branch per ticket, made by cw, in your normal checkout.** Agents never create branches, commit or push: the guard hook and the worker's permissions block it. You see the changes in your IDE, and your `.venv` and `node_modules` work.
- **One ticket per project at a time.** A per-project lock stops two cw processes from using one checkout, and cw refuses to start while the checkout has changes.
- **Workers take turns; nothing runs in parallel.** All of them edit the same checkout, one after another, so there are no conflicts between them.
- **A small request is one worker run.** There is no cheaper unit: cw has no model of its own.
- **Nothing is committed or pushed until `cw pr`.** Then exactly one commit is made.
- **The review cannot be skipped**, and a blocking finding stays open until a review says it is fixed.
- **cw starts the project's containers.** With a `compose_file` that exists, cw runs `docker compose --file <file> up --detach --wait` before the baseline, every worker run and every check, because workers may run only lint and tests. It returns at once when they already run. A failure (Docker not running, a port taken) stops the ticket with Docker's reason. `cw new` sets `compose_file` to `dev/docker-compose.yml`, and the planner's skeleton task writes it, with a healthcheck per service and host ports picked for the project.
- **A new project's agents write its `.env`.** `cw new` leaves `.env` off the protected paths, so the skeleton task writes working local values (random values for local secrets, never real ones). `.env` is gitignored; tests use their own database.
- **Every repeat is capped** in `Limits`: fix and review rounds 2, question and conflict rounds 1, `cw fix` rounds 3; every agent has a turn cap and a time limit. Nothing loops by itself.
- **A stop is never final.** Ctrl+C, FRIDAY's Stop, an error, a time limit or the usage limit leave the ticket `blocked` or `paused` with the step recorded, and `cw resume` continues.
- **Project notes are capped and stay with the ticket**: at most two per worker run and five per ticket. They go into the ticket's record, never into `CLAUDE.md`.
- **The context engine never stops a ticket.** A missing, damaged or locked index, Ollama not running, or a failing HyDE sketch each adds a `Context:` warning; the ticket goes on with what still works, down to the repo map.

### 4.5 An example, end to end

`cw run shop "let customers apply a discount code at checkout" --plan`

1. cw checks `shop` is clean, fetches `main`, switches to `cw/261118-let-customers-apply-a-discount-3fa2`, starts the database container, runs `uv run ruff check .` and `uv run pytest` once (both pass, so both are enforced), and refreshes the repo map.
2. The planner (opus) reads the map and a few files and returns a plan: S1 "Add the discount code table and repository" and S2 "Apply a code at checkout", both for W1 since they are one feature, with a context pack pointing at the checkout service and the order model. Status: `awaiting_approval`.
3. You reply "codes expire, add an expiry date". The planner's session continues with only that line and returns the revised plan. You run `cw approve`.
4. W1 (sonnet) gets the ticket and S1, writes the migration, model, repository and tests, and answers with a summary, a handoff ("`DiscountRepository.get_valid(code, now)` returns …") and a commit message. cw records git's list of changed files, runs lint and tests: they pass. S1 is checked off.
5. W1's session continues with S2 only. It asks: "should an expired code be a 400 or a 422?" The planner answers from the plan in its own session; the answer reaches W1, which finishes S2. Tests fail once; one fix round fixes them. Status: `ready`.
6. `cw pr`: the checks pass, the reviewer (opus) finds one blocking issue (F1: a code can be applied twice) and one minor naming issue. W1 fixes both, the checks pass, the second review sees only that fix and marks F1 resolved. cw makes one commit, merges `main` cleanly, pushes, opens the PR and posts the first review on it.
7. A teammate comments on a line. `cw fix` gives the comment to W1, checks, reviews, pushes a second commit, posts W1's summary and resolves the thread.
8. CI passes. `cw merge` squash-merges the PR at that commit, deletes the branch, fast-forwards your local `main`, and puts you back on the branch you started from.

### 4.6 All commands

| Command | What it does |
|---|---|
| `cw init --toolkit <path> --github-repo <owner/name>` | Writes `~/.cw/config.json` from the shipped example. |
| `cw project add <path> [--name] [--host] [--onboard]` | Registers a repository in about a second, with no agent; `--onboard` also maps it. |
| `cw project onboard / remove / list / wire / map <name>` | Map with the onboarder; unregister; list state as JSON; write the toolkit into the repo's `.claude/settings.json`; print or refresh the repo map. |
| `cw new <name> "<idea>" [--path] [--stack] [--github private\|public]` | Creates a repository (README, `.gitignore`), optionally on GitHub, registers it and starts a planned ticket. |
| `cw run <project> "<prompt>" [--plan] [--base <branch>]` | Starts a ticket. |
| `cw run <ticket> "<prompt>"` | Answers, gives plan feedback, or asks for more work. |
| `cw approve / resume / review / pr / fix / merge <ticket>` | The steps of §4.3. |
| `cw split / park / ci / done [--abandon] <ticket>` | Split into tickets; park; show CI; close. |
| `cw status / show / events / stats / toolkit / config / doctor` | Views, each with `--json`. `cw config set` reads validated JSON on stdin. |
| `cw project remove <name> [--purge] [--yes]` | Unregisters a project and deletes its rebuildable caches (index, repo map); `--purge` also deletes its decisions and tickets, after asking. |
| `cw context build <project> [--prune]` | Builds the whole index at `HEAD`: every chunk embedded, every finished ticket recorded. |
| `cw context status <project> [--json]` | Indexed commit, counts, size, model, embedded chunks, what `cw gc` would free. |
| `cw context search <project> "<text>" [--no-hyde] [--json]` | Prints the seed the planner would get. |
| `cw context eval <project> [--from-commits N] [--method …] [--yes] [--json]` | Recall and MRR of the seed, from finished tickets or git history (§19). |
| `cw context migrate-notes <project>` | Copies the old "Notes kept by cw" section of `CLAUDE.md` into a ticket record. |
| `cw gc [<project>] [--dry-run] [--json]` | Removes old index data and expired event logs, then shrinks the index file. |
| `cw audit <ticket> [--json]` | One extra review of a ticket's final diff, for benchmarks; kept out of the ticket's cost. |
| `cw benchmark <project> [--json]` | Cost per role, reviews per model, fix rounds, failing checks and audit results per ticket. |

---

## 5. Agents

All live in `toolkit/plugins/core/agents/*.md`. The `model` in each file is only a default for interactive use; **cw always uses the model from the project's config**.

| Agent | Default model | Tools | Job | Answers with |
|---|---|---|---|---|
| planner | opus | Read, Grep, Glob | Turns a bigger request into a plan and a clear, ordered task list, each task given to a worker; splits multi-branch requests; asks at most 3 questions; answers the workers' questions | questions · split · plan · options considered · tasks · files · context pack · answers |
| worker | sonnet | Read, Edit, Write, Bash, Grep, Glob | Implements one request, task or fix with the smallest change. Never touches tooling it wasn't asked about. Asks instead of guessing. | status (done / question / needs_plan / blocked) · summary · files · handoff · questions · project notes · commit message · reason |
| reviewer | opus | Read, Grep, Glob | Applies `pr-review` and every rule that covers the changed files, strictly, to the diff only | summary · line comments (blocking / minor) · ids of open findings now fixed |
| onboarder | sonnet | Read, Grep, Glob | **Optional.** Maps a repository: lint and test commands, architecture, tests, differences from the team rules | stack · commands · architecture · tests · differences · warnings |
| context-sketcher | haiku | StructuredOutput only | Sketches, from the request, the stack and the folder names alone, where the change would land: the HyDE sketch (§19). Runs bare: no rules, plugins, hooks or repository | likely paths · likely names · 10–20 lines of plausible code |

Every answer is forced into a JSON schema built from the pydantic models in `models.py` (`--json-schema`), so cw never parses free text.

**What each agent is told** (`prompts.py`):

| Agent | First prompt of a session | Resumed session |
|---|---|---|
| Worker | The ticket without status lines or reviews (request, answers, plan, tasks with what each finished one left behind), the files changed so far, the instruction, on a direct ticket's first run the context seed, the context pack, the checks and which are its job, what is off limits, its limits | What other workers did since its last turn, then the instruction |
| Planner (planning) | The request, the context seed (or the repo map when there is no seed), the "new project" brief when there is no code yet, your feedback, and the constraints (questions allowed or not, how many workers, one branch per ticket) | Your feedback and whether it may still ask |
| Planner (answering a worker) | The ticket, then the worker's questions | The worker's questions |
| Reviewer | Always a new session: the request, the plan, the workers' notes, the check results, the open findings and the diff | — |
| Context sketcher | The request, the project's stack and its folder names two levels deep; nothing from the repository | — |

---

## 6. How agents communicate: the ticket

Agents never talk to each other directly. Everything goes through the ticket, which cw keeps as `<id>.json` (the state) and writes next to it as `<id>.md` (the same content to read).

The `.md` holds, in order: the request (all your prompts), questions and answers, the plan, the task list, other work (follow-ups and fixes) and the reviews. Under each finished task is what its worker left:

```
### [x] S1 · Add the reminders table and repository (worker W1)
- Solved by W1: <how it was solved, a few lines>
- Files: src/app/schemas/reminder.py, src/app/repositories/reminder_repository.py
- For the next worker: ReminderRepository.list_due(now) returns ...; the migration is 0002
```

- **Agents write to the ticket only through their structured answer**, which cw records. They never edit the files, so no agent can corrupt them or forget to update them.
- **Questions go up one level at a time**: worker → planner → you (§4.3, step 4).
- **The reviewer** gets the workers' notes marked as claims; it trusts the diff and the files.

### What cw checks instead of trusting

- **git decides what a worker changed.** cw snapshots the tree before and after every worker run (`git write-tree`, nothing committed). The ticket's `Files:` line is git's list. A worker that says `done` but changed nothing gets a `No changes:` warning; a task that changed files outside its planned list and not named in its instructions gets a `Scope: S<n>:` warning. These are warnings, not blocks: a plan's file list is a guess, and refusing a correct change on a guess would do more harm.
- **The checks run after every task**, so a broken step is fixed before the next task builds on it.
- **Paths are checked.** Context-pack entries and review findings on files that do not exist are dropped.
- **The review's outcome comes from the severities**, never from the model's own verdict.

### What keeps context small

- **Worker sessions have a size limit.** When a run's average turn passes 45k tokens (cached and uncached together), that worker starts a fresh session next time, seeded from the ticket.
- **Older tasks are shortened in a worker's view**: their summary is dropped, but their files and handoff stay, because later tasks build on them. The `.md` keeps everything.
- **The planner hands over what it read**: the context pack points every new worker session at the right lines, and must hold only lines the planner checked itself, not lines copied from the seed.
- **The planner starts from a context seed made for the request** (§19), not from the whole repo map, and the seed's budget (2,500 tokens) is no bigger than the map it replaces.
- **The repo map is the fallback**: `~/.cw/projects/<project>/repo_map.txt`, made without an LLM from `git ls-files` (so `.gitignore` is respected): module docstrings, classes and function signatures for Python and Swift, at most 250 lines. When the full outline is too long it falls back to one line per file, then one line per folder, so no folder is dropped. The planner gets it only when there is no seed (or `include_repo_map` is on). It is refreshed at every ticket start, when a ticket is done or merged, and by `cw project map <name>`.
- **A resumed agent gets only what is new** (§5).

### Memory across tickets: ticket records

- A worker may return `project_notes`: a lasting fact a future session needs and cannot see quickly in the code (a command, where a kind of code lives, a decision and why, a trap). Usually it is empty; at most two per run and five per ticket. Fix passes keep none.
- **cw no longer writes them into `CLAUDE.md`.** That file is loaded into every run of every agent, related or not, and the *Evaluating AGENTS.md* study found such context files raise cost without improving results. Notes now go into the ticket's **record** in the context index when it finishes, and come back only when the history channel finds the ticket similar to a new request (§19).
- **Records are per person**: `~/.cw` lives on the machine that ran the ticket, so a teammate's cw does not see your records. That was the choice; a team that wants shared notes keeps them in the project's `CLAUDE.md` by hand.
- An existing "Notes kept by cw" section is left alone; `cw context migrate-notes <project>` copies its lines into a record, once.
- A knowledge graph to walk was considered and rejected: an agent pays tokens to walk it. The context engine's graph is different: cw uses it internally to rank files and never shows it to an agent.

---

## 7. Skills and how the rules reach an agent

| Skill | Plugin | What it is | Reaches cw agents |
|---|---|---|---|
| `engineering` | core | The team's language-independent rules: generic configuration, no over-engineering, how to work, naming | pinned for every agent (`pin: true`) |
| `python-rules` | python | An index of `topics/*.md`, which are the `nexttech-claude-kit` rules with reviewers' and people's names removed: `core-rules`, `python-style`, `python-stack`, `service-architecture`, `configuration`, `errors-and-logging`, `data-and-migrations`, `agents`, `git-and-pr`, `pr-review-procedure`. Two are adapted to a cw run: `core-rules` (lines naming commands no cw agent can run) and `review-checks`, the review procedure's checks without its own workflow and answer format | the index is pinned; `core-rules` and `python-style` always; other topics by hook or for a review |
| `pr-review` | core | How to review: what to check in which order, name the rule each finding breaks, and what is `blocking` (a defect, a security problem, a failing check, a missing test, unrequested work) or `minor` (style, naming, dead code, over-engineering, questions) | pinned for the reviewer |
| `ponytail` | core | The laziest solution that works: the ladder to run before any code. The toolkit's copy has no levels or interactive commands | pinned for planner, worker and reviewer |
| `rule-precedence` | core | Which rule wins when two disagree, and what the Nexttech rules mean inside a cw run (cw commits, no skills to invoke, no GitHub or Linear, cw's answer format) | pinned for planner, worker and reviewer |
| `clean-code` | core | [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills), MIT licence kept | interactive sessions only |
| `bootstrap-project` | core | The command `/core:bootstrap-project` | interactive sessions only |

cw agents cannot load skills on demand (no agent has the Skill tool), by design: nothing depends on the model deciding to look. Each rule arrives when it applies:

1. **Pinned** into the system prompt (about 390 lines, about 9k tokens, cached after a session's first turn): `engineering`, `ponytail`, `rule-precedence`, the `python-rules` index, and the topics `topics.json` lists under `always` (`core-rules`, `python-style`).
2. **By topic, when a file is touched.** `topics/topics.json` maps file names and folders to topics. The `rules_on_touch.py` hook shows a topic once per session, the first time the agent reads or edits a matching file. Before an edit it stops the edit so the rules are read first.
3. **For a review.** cw knows which files changed, so it pins exactly the topics that cover them, plus `git-and-pr` and `review-checks`, into the reviewer's system prompt (about 19k tokens for a change that touches a route and a service).
4. **For a new project.** Every agent of a ticket made by `cw new` also gets `python-stack` and `service-architecture`.
5. **Mechanically.** What a script can check is a hook, not advice (§8).

The topics of one session never change, so a resumed session's system prompt stays the same and its cache stays valid.

**Precedence, highest first:** the project's `CLAUDE.md` and its onboarding decisions; the Nexttech rules for the stack, then `engineering`; ponytail; clean-code. ponytail ranks third only where the Nexttech rules say otherwise (no comments, tests only where the project has a test command); otherwise it is the first thing an agent applies when it writes code. The kit's files are edited in the kit, then copied again with the names removed.

---

## 8. Hooks

| Hook | Event | What it does |
|---|---|---|
| `core/scripts/guard.py` | `PreToolUse` on Bash | Reads the command the way a shell splits it, so `git -C . commit` is caught and a grep for the text "git commit" is not. **Always:** blocks force pushes, `rm -rf /` or `~`, `git reset --hard origin…` and `--no-verify`. **In cw runs only** (`CW_RUN=1`): also blocks `git commit/push/merge/rebase/checkout/switch/reset/stash/worktree/restore/clean` and `branch -d`, also behind `sh -c "…"`, and commands that name a protected path. A guard rail; the permission rules (§9) are the wall. |
| `python/scripts/rules_on_touch.py` | `PreToolUse` on Edit/Write, `PostToolUse` on Read | Shows the team's rules for the area of the file, once per session, skipping topics cw already pinned (§7). |
| `python/scripts/ruff_on_edit.py` | `PostToolUse` on Edit/Write | Formats and lints the edited `.py` file with the project's own ruff (or `uvx ruff`). A file that was unformatted or had lint errors in the last commit is left alone, so the agent is never sent to fix problems it did not cause. Unused imports and variables (F401, F841) are left to the lint check, since an import added in one edit is usually used in the next. When ruff changed the file, the agent is told to read it again. |

---

## 9. Isolation, permissions and safety

**What a cw run loads, and what it never loads.** A run gets only the project's own setup and the team's toolkit:

| Loaded | How |
|---|---|
| The repository's committed `.claude/settings.json` | `--setting-sources project` |
| The repository's `CLAUDE.md` | Claude Code loads it from the folder |
| The repository's own MCP servers, when it has a `.mcp.json` | `--strict-mcp-config --mcp-config <checkout>/.mcp.json` |
| The toolkit plugins of the project's stack | `--plugin-dir <toolkit>/plugins/<plugin>` |
| The external plugins the config names (`ponytail@ponytail` always, `pyright-lsp` for Python) | `enabledPlugins` in the run's own settings; they must be installed (`cw doctor` checks) |
| The run's permissions | `--settings <tmp>/settings.json` |

**The `context` role runs bare.** The HyDE sketch gets no rules file, no toolkit plugins (so no hooks), no external plugins, no MCP servers and only the `StructuredOutput` tool, and it runs in a temporary folder, so it loads no project `CLAUDE.md` and cannot read the code. That keeps it at about 1.8k input tokens (measured).

**Never loaded:** your user settings (`~/.claude/settings.json`), your personal per-repo `.claude/settings.local.json`, your personal plugins, and your personal or claude.ai MCP servers. Measured on the first real run: a run that only said "hi" cost 36.9k tokens with the personal setup and 5.9k without. The toolkit's own plugins are switched off in `enabledPlugins` so a wired repository does not load them a second time from the marketplace.

**Permissions:**
- **Protected paths** (default `**/*.key`, `**/*.pem`, `**/secrets/**`) become `Read/Edit/Write` deny rules in every run, plus the guard for shell commands. `.env` is not on the list: agents may update it when a task adds or changes a setting. What is in it is then sent to the model, so keep production secrets out of it, or add `.env` to a project's protected paths.
- **Worker shell commands.** The worker runs with `acceptEdits`. Bash is allowed only for the project's lint and test commands (also narrowed: cw drops a command's trailing folder arguments, so `ruff check src tests` also allows `ruff check <any path>`; a trailing file, which may be the script itself, is never dropped), `head` and `tail` for piping their output, read-only git (`status`, `diff`, `log`, `show`) and `extra_allowed_bash`. Anything else is denied in headless mode. The worker's prompt says so, and the planner writes `Done when:` with only those commands.
- **Read-only agents.** Planner, reviewer and onboarder have no write tools; the context sketcher has no tools at all.
- **What the rules do not stop.** They see the command an agent runs, not what that command runs in turn. The worker may run the tests, and the tests run code the worker can write (a `conftest.py`), which could commit, read a protected path or reach the network. The rules keep a cooperative model on its path and stop mistakes; they are not a sandbox against hostile text in the repository or the prompt. If that matters, run the checks in a container with a read-only `.git` and no network.
- **Secrets.** The Bitbucket token comes only from the environment and is removed from the environment agents run in.
- **Your work is protected.** cw refuses to start or switch tickets when your checkout has uncommitted changes, and Abandon is only offered for that ticket's own branch.
- **Two human checkpoints:** you approve plans, and you merge pull requests. cw merges only when you run `cw merge`, and never while a PR thread is unresolved.
- **The context index stays on your Mac.** Embeddings are computed by your local Ollama; no code is sent anywhere for them. The index and records live in `~/.cw`, are never committed, and are rebuilt from git with `cw context build`.

To give agents a team MCP server, put it in the project's `.mcp.json`. Every server costs context on every turn, so add only what the agents use.

---

## 10. Anatomy of one agent run

`claude_runner.py` runs:

```
claude -p --output-format stream-json --verbose
  --agents <tmp>/agents.json          {name: {description, prompt: <agent file body>, model: <config>,
                                              maxTurns: <config>, tools: [... + StructuredOutput]}}
  --agent <name>                      the whole session is that agent
  --settings <tmp>/settings.json      allow/deny rules + which plugins are enabled
  --append-system-prompt-file <tmp>   pinned skills + the topics this run needs + the project's decisions
  --json-schema '<pydantic schema>'   forces the structured answer
  --permission-mode acceptEdits|default
  --max-turns <config>
  --strict-mcp-config --setting-sources project      ← nothing personal
  [--mcp-config <checkout>/.mcp.json]                the project's own MCP servers, when it has the file
  --plugin-dir <toolkit>/plugins/core --plugin-dir <toolkit>/plugins/<stack>
  [--resume <session>]                the planner's, or the worker's, own earlier session
  < the prompt on stdin
the context role: no --append-system-prompt-file, no --plugin-dir, no --mcp-config, cwd = a temporary folder
cwd = the project checkout   env = yours without cw's secrets + project env + CW_RUN=1 + CW_PROTECTED_PATHS + CW_SHOWN_TOPICS
```

- cw reads the stream line by line and logs every tool call (`worker · Edit src/app/routes/items.py`), which is what you see live in the terminal. It also appends each start, tool call, end and failure, labelled with the worker and task (`W2 · S3`), to `<id>.events.jsonl`, together with every check cw runs. `cw events <id> --json` prints the latest; FRIDAY follows a ticket through it, wherever the run was started.
- `run_agent` takes an optional model that replaces the role's: follow-up reviews use it for `reviewer_followup`, and `cw audit` for the reviewer model.
- The last `result` line carries the structured answer and the usage. The tokens of a failed run are recorded too, and each run carries its label, so cost can be shown per worker.
- The process runs in its own process group with a time limit (`agent_timeout_minutes`). Ctrl+C, FRIDAY's Stop and the time limit kill the agent and everything it started.
- **Errors.** Claude Code's own error text is kept. HTTP 429 or "usage/rate limit" makes the ticket `paused`. "No conversation found with session ID" means the session to resume is gone: cw starts a new one that gets the whole ticket. Any other failure makes the ticket `blocked` and keeps the session, so `cw resume` continues it instead of paying for a fresh one.

---

## 11. Tickets, events and history

- One JSON file, one markdown file and one events file per ticket in `~/.cw/tickets/<project>/`. The JSON is written through a temporary file, so FRIDAY never reads half of it.
- A ticket holds: status, route, branch, the branch it started from and goes back into, every prompt, questions and answers, plan, tasks with their worker, what each worker pass left behind, reviews, open findings, the baseline, warnings, PR, the recorded step and error, every run's token usage, label and `session_id`, and for the context engine its stored seed (`context_seed`, `seed_files`), `changed_files`, `merge_commit`, the `cw fix` round count and an optional `audit`.
- **Event logs expire**: a closed ticket's `.events.jsonl` is deleted after `storage.event_retention_days` (30); its `.json` and `.md` stay. FRIDAY shows "Activity log removed" for such a ticket.
- **History:** closed tickets stay there. FRIDAY lists them under Closed; `cw show <id>` prints the markdown.
- **Agent transcripts:** Claude Code keeps its own session logs, so `claude --resume <session_id>` in the project reopens any run.

---

## 12. Configuration (`~/.cw/config.json`)

Validated on every run; unknown keys are rejected. FRIDAY saves through `cw config set`, so the same validation applies. `cw init` writes it from `backend/src/cw/config.example.json`. `CW_HOME` moves the whole `~/.cw` folder.

| Key | Meaning |
|---|---|
| `toolkit` | local path, marketplace name, GitHub repo, always-on toolkit plugins (`core`) and external plugins (`ponytail@ponytail`) |
| `external_marketplaces` | marketplace name → GitHub repository |
| `stacks.<name>` | marker files (detection), toolkit plugins, extra pinned skills, external plugins, starting `commands` (Python: `uv run ruff check .`, `uv run pytest`) |
| `default_protected_paths` | copied into each new project |
| `defaults` / `projects[].profile` | model per agent, `worker_permission_mode`, `reviewers`, and `limits` |
| `limits` | below |
| `projects[]` | name, path, base branch, remote, stack, git host, lint and test commands, protected paths, extra allowed shell prefixes, environment (`{ticket}` is replaced by the ticket id), `compose_file` |
| `context` | the context engine's settings and `context.embeddings` for Ollama (§19) |
| `storage` | `max_index_mb` (1024: above it cw warns to run `cw gc`) and `event_retention_days` (30) |

**Every key added since 2026-11-19 has a default**, so an older `config.json` loads unchanged, and FRIDAY saves by writing only the keys it edits into the JSON it read, so it never drops a key it does not know.

**Shipped defaults** (a project keeps the profile it was added with; FRIDAY's **Apply models & limits to all projects** updates them):

| Limit | Default | What it caps |
|---|---|---|
| models | planner opus, worker sonnet, reviewer opus, onboarder sonnet, `reviewer_followup` sonnet, `context` haiku | |
| `review_skip_max_changed_lines` | 20 | lines a comment-only `cw fix` round may change and skip its review (0 never skips) |
| `max_backoff_rounds` | 1 | fresh worker sessions, from the last attempt's reflection, for a task whose checks still fail |
| `max_review_rounds` | 2 | fix-and-re-review rounds after blocking findings |
| `max_fix_rounds` | 2 | fix rounds after a check breaks |
| `max_pull_request_fix_rounds` | 3 | `cw fix` passes |
| `max_question_rounds` | 1 | planner questions to you; worker questions per instruction |
| `max_conflict_rounds` | 1 | attempts to resolve merge conflicts |
| `max_workers` | 4 | workers a plan may use |
| `escalate_file_threshold` | 5 | files a direct request may touch before it needs a plan |
| `summary_max_lines` | 15 | a worker's summary |
| `max_diff_chars` | 60000 | the diff sent to the reviewer (it reads the files for the rest) |
| `max_check_output_chars` | 4000 | check output sent back to a worker |
| `agent_timeout_minutes` / `check_timeout_minutes` | 45 / 15 | one agent run / one check |
| `max_turns` | planner 40, worker 70, reviewer 30, onboarder 60, context 1 | turns per run |

Which skills are pinned is decided in the toolkit (`pin: true`), not here, so the whole team gets the same rules. Secrets come only from the environment (`BITBUCKET__EMAIL`, `BITBUCKET__API_TOKEN`).

---

## 13. Why the headless CLI and not the Agent SDK

The Agent SDK docs say:

> "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK."

Everyone on the team uses their own subscription, so cw runs the official CLI headless. Nothing is lost: the SDK itself runs the Claude Code binary and reads the same `stream-json` lines cw reads. If the team moves to API keys, `claude_runner.py` is the only file that would change.

That holds only between the CLI and the SDK, which run the same harness. A local model is a different matter: the tool loop, `--json-schema`, `--resume`, prompt caching, plugins, skills, hooks and permission rules are all Claude Code. Moving to a local model means another harness that offers them. What would carry over almost unchanged is `pipeline.py`, `prompts.py`, `tickets.py`, the git layer and the git hosts.

---

## 14. Token efficiency

**Where the savings come from**, roughly in order of size:

1. **Isolation.** No personal MCP servers, plugins or settings in a run (36.9k → 5.9k tokens for "hi").
2. **No pasted rulebooks.** A short pinned core, topic files only when a file is touched, exact topics for the reviewer.
3. **Sessions that continue.** A worker keeps its session for related tasks, follow-ups, fixes and answers; the planner keeps its own for feedback and the workers' questions. A continued session is mostly cached tokens at a tenth of the price, and the agent does not re-read the project.
4. **Only what is new.** A resumed worker gets what others did since its last turn; a resumed planner gets only your feedback.
5. **Guided reading.** The repo map and the context pack point agents at the right files instead of letting them explore.
6. **Deterministic work stays out of the model.** cw runs the checks, commits, merges, pushes and decides from severities; failures that already exist on the base are ignored.
7. **One reviewer, re-reviewing only the new diff**, with open findings carried by id.
8. **Caps on every loop, no router agent, and workers that take turns.**
9. **Sessions that grow too big start fresh** (45k tokens a turn), and lost sessions are replaced only when they really are gone.
10. **Cheaper reviews** (2026-11-19). Reviews were 47% of the cost of the 13-ticket test ($4.21 of $9.04): `cw fix` now reviews once per round instead of running a review loop inside each, re-reviews run on Sonnet, and a small comment-only round skips its review.
11. **A seed instead of the repo map** (§19): the planner's first prompt does not grow, and it should explore less on a large repository. HyDE costs about $0.0005 a ticket; embeddings run locally and cost no Claude usage.

**Measured:**

| Measurement | Before | After |
|---|---|---|
| Context of a run that only says "hi" | 36.9k tokens | 5.9k (2026-11-11) |
| Worker context per turn | 35–80k | 7.8–11.9k |
| Planner context per turn | ~19k | ~4.4k |
| Agent runs for "two small features" | 1 planner + 17 workers + 2 reviewers | split into 2 tickets, about 1 worker + 1 reviewer each |
| Commits per ticket | 14 | 1 |

Real tickets since (each in `CHANGES_AND_ISSUES.md`): a one-file test on Haiku, $0.015; an 11-file, 2-task change, about $1.47; tic-tac-toe from nothing (6 tasks, 4 workers), $2.49; a FastAPI expense tracker from nothing (17 tasks, 3 workers, 65 tests), about $8.06.

**Not measured yet:** the cost and quality effect of the 2026-11-19 changes on real tickets (run the benchmark, `BENCHMARK.md`), the cost of the larger pinned rules since 2026-11-16 (about 9k tokens per agent, cached after the first turn), and every change of 2026-11-18. Measure with `cw stats` and `cw show <id> --json` (`runs`: tokens and turns per run). A run's `cost_usd` is what Claude Code reports, which for a resumed session is the session's total so far; `cw stats` subtracts the session's previous run, so each run is counted once.

---

## 15. FRIDAY

A window over cw, redesigned on 2026-11-18 around the agents. It only runs `cw` (through a login shell, so it sees the same `PATH` as your terminal) and reads its `--json` output: `config`, `project list`, `status`, `show`, `events`, `stats`, `toolkit` and `doctor`. `cw … --json` is its contract: a field added in `models.py` or `config.py` needs a default, and `Models.swift` if the app shows it. Saving settings merges FRIDAY's edits into the config JSON it read, so keys it does not model survive.

| Area | What it does |
|---|---|
| Sidebar | Your projects with their branch, uncommitted changes, and a spinner or a count of tickets waiting for you; **Add a repository** and **New project from an idea**. Right-click a project for onboarding, the code map, wiring the toolkit, settings and unregistering. Then Activity, Usage, Toolkit and Settings |
| Ticket list | The project's tickets: Running, Needs you, Pull requests, In progress and Closed, with search |
| Project home | The checkout's state, what the tickets are doing, a box to start a ticket (plan first, base branch, the exact `cw run` it will run), and the project's tools |
| Ticket | A header (status, branch, route, PR, cost, Stop, More: Park, Done, Abandon), the pipeline Plan → Work n/m → Checks → Review → PR → Merge, and one card per agent (planner, W1, W2, …, reviewer) with its model, runs and state. The agent working now is highlighted; a card opens its tasks, handoffs, questions and runs |
| Ticket tabs | Overview, Plan, Work, Review (open findings, every round), Activity (every event), Cost (per run and per agent) |
| Next step | One sentence on what happens next and the buttons the status allows |
| Composer | Answers, plan feedback and follow-ups, typed or dictated |
| Activity, Usage | Every command FRIDAY ran, with output and Stop; tokens and cost per project and role |
| Settings | Health (`cw doctor --json`, including FTS5, Ollama and each project's index), the `cw` path, voice and look, the toolkit, and the models (follow-up reviewer and HyDE sketch included), turn caps and limits new projects copy |

Polling: the ticket list every 3 seconds, the open ticket and its events every 2 seconds while it runs and every 8 otherwise. `friday/test.sh` runs tests that decode real `cw` output (`friday/Tests/FridayTests/Fixtures`, made by `make_fixtures.py`) and check the stages, agents, costs and actions; `Friday --snapshot <fixtures> <folder>` draws the main screens into PNG files.

---

## 16. Verified, and not verified

**Verified by tests that run the real cw code with a stand-in for `claude`** (`cd backend && uv run pytest`, 167 tests):
- a project created from an idea, planned, approved, built in two tasks, reviewed, committed once and merged by `cw done`, through the command line;
- related tasks continue one worker session, another worker gets a new one and is told what happened; plan feedback resumes the planner with only the feedback;
- a worker's question is answered by the planner; one the planner cannot answer goes to you and your reply reaches the worker;
- only a session Claude Code reports as lost is replaced by a fresh one with the whole ticket; any other failed run keeps its session;
- Ctrl+C in a task list leaves the ticket blocked and resume continues at the next task; a ticket left in progress by a killed process resumes; a killed `cw review` resumes as a review;
- merge conflicts are resolved, committed, checked and reviewed before the push; a later review does not include the base's changes;
- the PR is opened after the review even when findings are left; `cw review` commits nothing; a ticket from `--base develop` targets `develop`;
- a broken check gets its fix rounds and is then reported; in a task list it stops the ticket, which resumes at that task; containers start before the baseline, the worker and the checks; a ticket whose containers do not start is not created;
- `cw fix` sends the open threads and open blocking findings to the last worker, pushes, posts the summary, resolves the threads, goes round again while blocking findings stay open, and stops after a round with no change;
- a blocking finding stays open until a review names it fixed; findings on missing files are dropped;
- `cw merge` refuses while a blocking finding is open or CI has not passed, expects CI checks when the branch has CI config, passes the checked commit to the host, and otherwise merges, fast-forwards the local base, closes the ticket and deletes the branch; with `gh` and the Bitbucket API stubbed, both hosts count "no checks" as passed only without CI config, and refuse a head that moved;
- a run loads only the project's settings and its `.mcp.json`; the guard, the rules hook, the topic index and the ruff hook; time limits kill a command and everything it started; `cw stats` counts a resumed session once; project notes are capped and never reach `CLAUDE.md`;
- **pull requests (2026-11-19):** `cw merge` refuses with an unresolved thread and names the declined ones; a declined comment gets a reply, stays open and is not sent again until a person answers; a fixed one is resolved, one without an answer stays open; a `cw fix` round reviews once; every review after the first runs on `reviewer_followup`; a small comment-only fix skips its review, a big one, one with failing checks, one with skipping off, or one that fixes a blocking finding posted as a thread (matched by its `[F…]` label) does not; a fix pass keeps no notes and warns about an unasked `README.md` edit but not a test file; `gh` and Bitbucket thread replies and last comments, with the hosts stubbed;
- **context engine:** term splitting, chunking (every parameter kind, relative imports, syntax errors), FTS5 keeping `transfer_funds` whole, only changed blobs parsed and a rename parsing nothing, ignored folders and protected paths left out, a project in a subfolder indexed with paths relative to it, ranking a named function's file first, PageRank against hand-computed values, a hub not outranking the matched file, the seed's budget and determinism, the planner getting the seed and layout instead of the repo map, a corrupt index giving a warning and the repo map, `finish` indexing the base's tip and storing a record (not on abandon), `changed_files` leaving out what the base brought in, the history leakage rule, the current ticket excluded and open findings shown only on seed files, the query instruction on queries only, batching, `num_ctx`, unit vectors, the vector cache and re-embedding on a model or dimension change, the `max_incremental_files` cap, the dense channel finding a file without shared words, Ollama down giving a seed from the other channels, a model not pulled, the HyDE sketch feeding both channels and running once, a failing sketch still giving a seed, the context role running bare, a direct ticket's seed once and its reuse on escalation, eval from tickets (created files left out, real snapshot untouched) and from commits (parents as bases), gc keeping retained trees and an open ticket's base and freeing the rest, `--dry-run`, VACUUM shrinking the file, event-log retention, the size warning, the index never inside the checkout, `migrate-notes` once, `project remove` with and without `--purge`, and the audit kept out of a ticket's runs. Tests never call Ollama or `claude`: both are stubbed, and the test config points `claude_binary` at a path that does not exist.

**Checked for real on 2026-11-19, on this Mac with Ollama and `qwen3-embedding:8b-q8_0`:** related code embeds at 0.96 against 0.40 for unrelated code, and a money-transfer query scores 0.74–0.75 on the two money functions and 0.20 on an unrelated one, so the query format and pooling work; a full `cw context build` of this repository (58 files, 492 chunks) took 330 s, **about 1.5 chunks a second**, for a 4.8 MB index; `cw context search` takes about half a second; `cw context eval --from-commits 40` on this repository scored 12 commits (table in §19); one real HyDE sketch on Haiku ran bare in about 1.8k input tokens for about $0.0005, also with one turn.

**Verified on real projects with the real Claude Code and GitHub, on 2026-11-16 and 17:** three tickets on an existing repository and two projects from nothing (tic-tac-toe, an expense tracker); resumed worker and planner sessions; PRs opened and reviewed on GitHub; `cw fix` and `cw merge` end to end. On 2026-11-18 the real CLI confirmed the lost-session message, and that a project's `CLAUDE.md` still reaches an agent with `--setting-sources project`.

**Not verified:**
- anything changed on 2026-11-18 or 19 on a real ticket: the outside review's fixes, B9–B14, the isolation change, the new FRIDAY, the pull request changes and the context engine; the token benchmark has not been run (`BENCHMARK.md`);
- the GitHub GraphQL `addPullRequestReviewThreadReply` call and the `lastComment` field against a real pull request;
- retrieval quality on Swift, Java or other languages without a graph, and on repositories larger than this one;
- that the rules hook's message reaches the agent as intended after a Read;
- FRIDAY by hand against a live run, dictation, speech and Stop;
- all of the Bitbucket side against a real server (`POST …/comments/{id}/resolve` comes from Atlassian's community answers);
- usage-limit detection;
- the links in §18 that start with `anthropic.com/research/` may have moved to `anthropic.com/engineering/`.

**Known bugs, not fixed yet:**
- cw's snapshots stage every file in git's real index, so a file the worker later adds to `.gitignore` is still committed. Check the PR's file list for IDE folders and stray files;
- `cw review` on a ticket whose PR is open sets it back to `ready`, and `cw merge` then refuses. Run `cw pr` again, then `cw merge`;
- a planned ticket's commit and squash message is the first task's message, not one for the whole change.

**Open from the outside review of 2026-11-18** (B1–B11 and B13–B15 are fixed):
- B12: onboarding again overwrites the decisions in `conventions.md`;
- B16–B21: Bitbucket review posting, task ids on re-planning, a non-atomic project lock, `tail -f` allowed for workers, `cw done` without a remote, and a stray `skills/` file in the repository.

**Known limits:** one open ticket per project; no Java stack plugin; the toolkit isn't published; the dependency graph is Python-only; ticket records are per person; the first embedding build of a large repository is slow on this Mac (§19).

---

## 17. How it got here: refinements, changes and optimizations

Each change below came from a real run or a review. The details, with every bug's root cause, are in `CHANGES_AND_ISSUES.md`.

### 2026-11-11 · The first real run, and the shape that stayed

**What happened:** one prompt asking for two small features on separate branches took 1 planner, 17 worker and 2 reviewer runs, about 35% of a 5-hour usage window, made 14 commits, and opened no PR.

| Change | Why |
|---|---|
| **Isolated runs**: no personal MCP servers, plugins or user settings; an explicit tool list | Every turn re-sent 35–80k tokens of personal setup (one MCP connector alone had 200+ tools). A "hi" went from 36.9k to 5.9k tokens |
| **Baseline checks**: only checks that pass on the base are enforced | Workers "fixed" a whole repository whose lint already failed on `main` |
| **Workers do not touch tooling or fix the environment** | Workers added pytest and changed `pyproject.toml` because a test command was broken |
| **No commits while working; one commit at the end** | 14 commits, with the ticket file in each |
| **Tickets left the repository** (`~/.cw/tickets/`) | They were committed into every PR |
| **Onboarding writes nothing into the repository** | It wrote `.cw/` and `.claude/settings.json` unasked |
| **Splitting a request** into one ticket per branch | One ticket is one branch; agents tried to make two and gave up |
| **No worktrees, no parallel workers**: work happens in your checkout, one ticket per project, Park to switch | Worktrees lacked `.venv` and `.env` and hid changes from the IDE; parallel workers needed file ownership checks for no gain |
| **Onboarding became optional** | `cw project add` registers in a second with no agent |

### 2026-11-13 · A full review: sessions, rules that arrive, projects from nothing

| Change | Why |
|---|---|
| **Workers keep their session** (W1, W2 …); a resumed worker gets only what is new | A fresh worker per task re-read the project every time |
| **The ticket is the only channel** between agents, written also as `.md`; each task leaves a summary, files and a handoff | Workers started without knowing what earlier tasks did |
| **Agents can ask**: worker → planner → you | Workers guessed |
| **The review is mandatory and strict** (`pr-review`); `--skip-review` removed | Quality before every PR |
| **`cw new`** builds a project from an idea; **`--base`** starts from another branch | The "idea to project" goal |
| **Rules that arrive**: a short pinned core, topics shown by a hook, exact topics for the reviewer | The big rulebook had been unpinned to save tokens, and no agent could load it: agents were told to follow rules they never saw |
| **The guard reads commands like a shell** | `git -C . commit` passed; a grep for "git commit" was blocked |
| **Every stop records its step; process groups and time limits** | Ctrl+C left tickets stuck; Stop killed the shell, not the agent; agents could hang |
| **Merge conflicts fixed**, and the reviewed marker moves with the merge | Conflicts could never resolve; the next review included everyone's changes from `main` |
| **The ruff hook holds a file only to the standard of its last commit** | It reformatted whole files and reported old errors |
| **Live view** of every tool call; tokens of failed runs kept | You could not see what an agent was doing or what a failure cost |
| **git decides which files a worker changed**; `No changes` and `Scope` warnings | cw trusted the worker's own list |
| **Context pack** from the planner, **repo map** without an LLM | Workers re-explored what the planner had already read; every ticket started cold |
| **Session size ceiling** (45k a turn, cached tokens counted) and **shortened older tasks** | Long sessions grew expensive; the first ceiling counted only uncached tokens and never fired |
| **Checks after every task** | A broken task became the base of the next one |
| **The project's `CLAUDE.md` as memory** across tickets, fed by the workers' notes | A knowledge graph costs tokens to walk; `CLAUDE.md` is loaded and cached for free |

### 2026-11-16 · First real tickets, PR comments and merging

| Change | Why |
|---|---|
| **`cw fix`** reads the PR's open threads back to the worker | Comments could only be fixed by hand |
| **`cw merge`** squash-merges once CI passed and no blocking finding is left, and fast-forwards your local base | Merging needed the host's page; the local `main` stayed behind |
| **The reviewer is told cw's `CLAUDE.md` notes ship on purpose** | It flagged them as "outside the task" and blocked the PR |
| **The fix round sends minor findings too** | The second review called them "still not fixed" and made them blocking |
| **`cw stats` counts a resumed session once** | Claude Code reports a session's running total; the worker's cost showed 3× too high |
| **Allowed commands narrowed to a path, `head` and `tail` allowed**; a file named in a task's instructions is in scope | Workers lost turns to refused commands; false scope warnings |
| **The Nexttech kit's rules replace the old Python rulebook**, with `always` topics and `rule-precedence`; **ponytail pinned** | The old rules prescribed `src.` imports the team considers wrong; ponytail was demanded but never in the prompt |

### 2026-11-17 · A 17-task project from nothing

| Change | Why |
|---|---|
| **A task list stops when a check still fails after its fix rounds** | Five tasks were built on code whose database tests all errored |
| **`cw fix` loops** while the review still has findings | It had to be run again by hand |
| **cw starts the project's containers**; a new project's agents write its local `.env`; services use project-specific ports; README commands one per line | Workers cannot run Docker; port 5432 was taken by another container; zsh broke on commented commands |

### 2026-11-18 · An outside review, FRIDAY around the agents, and today's fixes

| Change | Why |
|---|---|
| **cw decides from severities; open findings carried by id** until a review says they are fixed | "approve" with a blocking comment passed; a finding vanished when a later round saw only new work |
| **Conflict resolutions are checked and reviewed** before the push | They were pushed unchecked and marked reviewed |
| **Containers before the baseline** | Database tests were recorded as "already failing" and never enforced |
| **A planner must plan after its last question round**; **the step is recorded before it runs**; **`cw split` runs no agent** | Empty plans reached approval; a killed review resumed as `cw pr`; one blocked split stopped the rest |
| **Rules rewritten to match what cw does**; a 115-line `review-checks` topic replaces the 281-line procedure in reviews (the reviewer's rules about 13% smaller) | Rules told agents to run commands they cannot run and to answer in another format |
| **`cw fix` posts re-reviews as one comment**, not new threads; loops only on blocking findings, up to 3 | Its own re-reviews fed the next round, minor ones included, so the loop rarely ended |
| **Paths checked**; **project notes optional** (≤2 a run, ≤5 a ticket) | Findings on invented files; `CLAUDE.md` grew without a gate |
| **Event log** (`cw events`), run labels per worker and task, `cw doctor --json`, **FRIDAY redesigned** around the agents | You could not see which agent was working, on what, or what it cost |
| **Defaults of 2 review and 2 fix rounds** | One round often left what a second would have fixed |
| **B9: no CI checks reported is "passed" only when the branch has no CI config** | `cw merge` could merge right after a push, before CI started |
| **B10: the squash merge is pinned to the checked commit** | A push after the CI check could be merged unchecked |
| **B13: a resumed planner gets only the feedback** | It got the whole request and repo map again on every round |
| **B14: only a really lost session starts fresh** (checked against the real CLI) | Any run that printed no result threw the session away and paid full price; a session that was really gone blocked the ticket instead |
| **Runs load only the project's settings and `.mcp.json`** | `.claude/settings.local.json` (personal) was still read, and the project's own MCP servers were blocked |

### 2026-11-19 · Review cost, a pull request merge gate, and the context engine

| Change | Why |
|---|---|
| **One review per `cw fix` round**, **re-reviews on Sonnet**, **small comment-only fixes skip the review** | Reviews were 47% of the 13-ticket test's $9.04, and one ticket made 7 reviewer runs because each `cw fix` round ran a review loop |
| **`cw merge` refuses with an unresolved PR thread**; the worker answers each comment `fixed` or `declined`; declined ones get a reply and stay open (B11) | Unaddressed comments could be merged; `cw fix` resolved even the comments the worker refused |
| **Findings carry their id in the PR** (`[F3]`) and are matched by it | Matching by text broke when a comment was reformatted, which could let a review be skipped on a blocking finding |
| **Fix passes keep no notes and leave `README.md` / `CLAUDE.md` alone unless asked**; the README review rule fires only when the diff changes something the README documents | Every fix round edited those files |
| **The context engine** (§19): a per-project index, keyword, path, history, embedding and HyDE channels, a graph channel, a seed instead of the repo map, ticket records | Planners explored large repositories on their own; notes in `CLAUDE.md` were sent to every run |
| **Notes move to ticket records** | Context files raise cost without improving results (*Evaluating AGENTS.md*) |
| **`cw gc`, event-log retention, project caches removed with the project** | Blob-keyed caches grow without a limit |
| **`cw audit` and `cw benchmark`**, and the eval's `--from-commits` | Cost alone cannot show whether quality dropped; 10 finished tickets are too few to measure retrieval |
| **Every new setting has a default; FRIDAY saves by merging** | Required new fields would have broken every existing config, and FRIDAY dropped keys it did not know |

### Research branches

`main` stays as it is. Each idea from a research paper gets its own `feature/<paper>` branch, judged by the goal in §1. `feature/react` (pushed, not tried on a real project) adds one back-off step: when a task's checks still fail after its fix rounds, a fresh worker session starts from the last attempt's reflection.

---

## 18. Further reading

**Agentic workflows and design**
- Anthropic, [Building effective agents](https://www.anthropic.com/research/building-effective-agents) (Dec 2024): workflows vs agents, the five patterns, and "find the simplest solution possible". The basis for cw's fixed pipeline.
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system): agents use about 4× the tokens of chat and multi-agent systems about 15×; "most coding tasks involve fewer truly parallelizable tasks than research". The reason workers take turns.
- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents): keeping context small and relevant, the idea behind short hand-offs.
- Anthropic, [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) and [Harness design for long-running application development](https://www.anthropic.com/research/harness-design-long-running-apps): task lists and progress files instead of one long session.
- Anthropic, [Writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents) and [Code execution with MCP](https://www.anthropic.com/research/code-execution-with-mcp): why tool definitions cost context.
- Anthropic, [Claude Code: Best practices for agentic coding](https://www.anthropic.com/research/claude-code-best-practices).
- Anthropic, [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills): progressive disclosure of skills.
- Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents): how to measure whether a workflow change helped.

**Papers**
- Xia et al., *Agentless: Demystifying LLM-based Software Engineering Agents* ([arXiv:2407.01489](https://arxiv.org/abs/2407.01489), FSE 2025): a fixed localize → repair → validate pipeline matched or beat complex agents on SWE-bench Lite. The strongest evidence for a simple pipeline.
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): 14 failure modes, mostly coordination and organisation rather than the individual agents.
- Yao et al., *ReAct* ([arXiv:2210.03629](https://arxiv.org/abs/2210.03629)): the reason → act → observe loop inside every agent run.
- Madaan et al., *Self-Refine* ([arXiv:2303.17651](https://arxiv.org/abs/2303.17651)) and Shinn et al., *Reflexion* ([arXiv:2303.11366](https://arxiv.org/abs/2303.11366)): feedback loops, the idea behind the reviewer → worker fix round, and why it's capped.
- Jimenez et al., *SWE-bench* ([arXiv:2310.06770](https://arxiv.org/abs/2310.06770)) and Yang et al., *SWE-agent* ([arXiv:2405.15793](https://arxiv.org/abs/2405.15793)): how coding agents are evaluated.
- Lindenbauer et al., *The Complexity Trap* ([arXiv:2508.21433](https://arxiv.org/abs/2508.21433)): dropping old tool outputs roughly halves an agent's cost and solves as many tasks as LLM summarization; the reason the back-off starts a fresh session.
- Liu et al., *Lost in the Middle* ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)): long contexts are used less reliably, another reason for small hand-offs.

**Reference docs**
- Claude Code: [subagents](https://code.claude.com/docs/en/sub-agents), [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview), [headless mode](https://code.claude.com/docs/en/headless), plus the pages on hooks, plugins, plugin marketplaces, settings and permissions, skills and MCP.
- [anthropics/skills](https://github.com/anthropics/skills): Anthropic's own skills, for interactive sessions; cw runs do not load them.
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official), [obra/superpowers](https://github.com/obra/superpowers), [wshobson/agents](https://github.com/wshobson/agents), [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code): public marketplaces to borrow from.
- Stack: [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [uv](https://docs.astral.sh/uv/), [git switch](https://git-scm.com/docs/git-switch), [SwiftUI](https://developer.apple.com/documentation/swiftui), [Speech](https://developer.apple.com/documentation/speech), [GitHub CLI](https://cli.github.com/manual/), [Bitbucket Cloud REST API](https://developer.atlassian.com/cloud/bitbucket/rest/).

---

## 19. The context engine

**What it is for.** On a large repository the planner used to explore on its own with Grep and Read, starting from a repo map that degrades to one line per folder. The context engine gives it, for each request, a short ranked list of the code most likely to matter, plus similar finished tickets, so it reads the right files first. A direct ticket's first worker run gets the same list. It never blocks a ticket, never sends code off your Mac, and never writes into the project's repository.

### 19.1 The index

One SQLite file per project, `~/.cw/projects/<project>/context.sqlite3`. It describes the code **at a commit** (the ticket's base), never your uncommitted files.

1. **List the files**: `git ls-tree` at the commit, run in the project folder so paths are relative to it, gives each file's **blob SHA** (git's fingerprint of its exact content). Only code files are kept (`.py`, `.swift`, `.js`, `.ts`, `.go`, `.rs`, `.java`, …), without ignored folders (`.venv`, `node_modules`, …), protected paths or files over `max_file_bytes`.
2. **Skip what is known**: a blob already in the index was parsed before, at any path or commit. After the first build, only files a ticket changed are read.
3. **Parse**: Python through its syntax tree (`ast`): a card for the file (docstring and top-level names), a chunk per class, function and method with its full signature, line range, first docstring line and code; the names it defines, the names it uses, and the modules it imports (relative imports resolved). Other languages get only a card, so no graph edges.
4. **Keywords**: each chunk's names and text split into terms (`transfer_funds` → `transfer_funds`, `transfer`, `funds`), stored in SQLite FTS5 with `_` kept inside a term.
5. **Embed**: each chunk, as a header (path, name and signature, docstring) plus its code, becomes a vector through Ollama (`qwen3-embedding:8b-q8_0`, the leading 1024 of 4096 values, renormalised). Vectors are cached by a hash of that text and by the model and dimensions, so nothing is embedded twice and a model change never mixes vectors.
6. **Records**: each finished ticket's record (request, plan excerpt, changed files, open findings, notes) with its own terms and vector.

**When it updates:** at every ticket start (to the base commit; the first time without embeddings, with a warning to run `cw context build` once), and when a ticket finishes (to the tip of its base, then the record is stored). An automatic update embeds only when at most `max_incremental_files` (20) files lack vectors, so a ticket waits at most a minute or two; above that it skips embedding and warns you to run `cw context build`, and keywords, paths, history and the graph still work. If Ollama is down, the update skips embedding with Ollama's reason, and the ticket's record is still stored. `cw context build` embeds everything, shows progress, and keeps what it finished if you stop it.

### 19.2 Building a seed

For one request, at ticket start:

1. **HyDE sketch.** The `context-sketcher` agent (Haiku, bare, one turn) guesses, from the request, the stack and the folder names, the likely paths, names and 10–20 lines of code. It is only used to search, never shown.
2. **Six ranked lists**, one per channel:

| Channel | Compares | Finds best |
|---|---|---|
| Keywords | The request's terms, plus the sketch's guessed names, against every chunk's terms (BM25) | Code using the request's words |
| Paths | The request's terms against the words in file paths | `services/statements.py` for "statements" |
| History | The request against finished tickets that ended **before** this ticket started, by keywords and by meaning; then the files they changed | Files that changed together before |
| Meaning: request | The request's vector (with Qwen3's task instruction) against every chunk's vector | Code that does what is described, in other words |
| Meaning: sketch | The sketch's vector against every chunk's vector | Code that looks like the change |
| Graph | Personalized PageRank over "this file uses that file" (names used and imports), each edge also running backwards at half its weight, starting from the other channels' results | Files the found files depend on, and the files that call them |

3. **Fusion.** Each list gives each file `weight / (60 + its place)`; the totals rank the files. Agreement between channels beats one channel's favourite, and raw scores never need comparing. The graph is one channel among the others, not the final order, so files everyone imports do not crowd out the files that matched.
4. **The seed**: a folder outline (two levels, with code-file counts), then the top files with their most relevant definitions and line ranges, then the similar tickets with their notes and the open findings they left on files in the seed, all within `seed_max_tokens` (2,500), whole blocks only. When there are similar tickets, `seed_ticket_tokens` (500) of the budget is kept for them, so file blocks can never squeeze out the notes they carry. It is built once per ticket and stored on it, so a fresh planner session after a lost session, or a planner after an escalation, reuses it without a second sketch. Its file list is kept as `seed_files`.

The planner is told the list is a starting point to verify, not a verdict, and its `context_pack` must point only to lines it checked itself.

### 19.3 When something fails

| Failure | What you see | What still works |
|---|---|---|
| No index yet | `Context:` warning to run `cw context build` | Keywords, paths, history, graph |
| Ollama not running, model not pulled, timeout | `Context:` warning | Everything except the meaning channels |
| HyDE sketch fails | `Context:` warning | Everything except the sketch |
| Index corrupt or locked | `Context:` warning | The repo map |
| `cw context build` with Ollama down | The build stops with Ollama's reason | Nothing changes |

### 19.4 Measuring it

`cw context eval <project>` replays changes and checks whether the files they really changed are near the top of the seed (recall at 5, 10 and 20, and MRR), for each method: `planner` (what the planner picked, tickets only), `lexical`, `lexical+graph`, `+history`, `+dense` and `full` (with HyDE, which asks first since it runs a sketch per sample).

- **From tickets** (the default): each finished ticket's request, scored against the code at its base. Tickets from before `changed_files` existed use their workers' file lists and count as approximate.
- **From history** (`--from-commits N`): the last `N` commits of the base branch, each scored at its parent with its message as the request. Commits cw tickets produced are skipped, since their records would give the answer.
- Files a change created are left out (no search can find them), only tickets that had finished earlier are used, and the real snapshot is never touched.

First real numbers (this repository, 12 commits, terse commit messages as requests):

| Method | R@5 | R@10 | R@20 | MRR |
|---|---|---|---|---|
| lexical | 0.42 | 0.52 | 0.61 | 0.58 |
| lexical+graph, forward edges only | 0.33 | 0.54 | 0.65 | 0.53 |
| lexical+graph, reverse edges at 0.5 (default) | 0.44 | 0.56 | 0.65 | 0.57 |
| +dense (forward edges only) | 0.45 | 0.55 | 0.66 | 0.61 |

Twelve samples are too few to tune on. Re-run it on a larger repository before changing `graph_weight`, `pagerank_damping` or the model.

**Quality and cost together**: `cw audit <ticket>` gives a merged ticket's final diff one more review with the reviewer model and stores the blocking count on the ticket (not in its runs or cost); `cw benchmark <project>` lists per ticket the cost per role, reviews per model, `cw fix` rounds, failing checks, findings left and the audit. `BENCHMARK.md` is the procedure.

### 19.5 Disk and cleanup

Keying by blob keeps every old version and its vectors until something removes them. `cw gc [<project>] [--dry-run]` (a dry run takes no lock, so it works while a ticket runs) deletes what no **retained tree** needs (the current snapshot, the base branch local and remote, and the branch every open ticket targets), vectors of another model or dimension, and event logs of tickets closed longer than `event_retention_days`; ticket records are kept. Then it shrinks the file (`VACUUM`). It runs on its own after `cw context build` and `cw context eval` (index part) and when a ticket finishes (event logs), never at ticket start. Past `max_index_mb` the ticket, `cw context status` and `cw doctor` warn. `cw project remove` deletes the index and repo map too; `--purge` also deletes the decisions and tickets. `cw context build --prune` and a rebuild after a format change keep the ticket records, which nothing else could rebuild.

### 19.6 Settings (`context` in `config.json`)

| Setting | Default | What it controls |
|---|---|---|
| `include_repo_map` | `false` | Also send the repo map when a seed exists |
| `seed_max_tokens` · `seed_ticket_tokens` · `max_seed_files` · `max_symbols_per_file` · `layout_max_folders` | 2500 · 500 · 15 · 6 · 60 | The seed's size, and the part kept for similar tickets |
| `lexical_top_k` · `embeddings.dense_top_k` · `max_query_terms` | 60 · 60 · 60 | Chunks taken from each search; terms taken from a request and its sketch |
| `fusion_k` · `graph_weight` · `history_weight` | 60 · 1.0 · 1.0 | Fusion |
| `pagerank_damping` · `pagerank_iterations` · `import_edge_weight` · `reverse_edge_weight` | 0.85 · 30 · 2.0 · 0.5 | The graph; the reverse weight lets callers of found code rise (0 turns it off) |
| `max_file_bytes` · `max_chunk_chars` | 400000 · 6000 | What is indexed |
| `max_similar_tickets` · `record_plan_chars` · `hyde_max_chars` | 3 · 1200 · 2000 | History and the sketch |
| `eval_k_values` · `eval_max_truth_files` | [5, 10, 20] · 20 | The eval |
| `embeddings.model` · `dimensions` · `context_length` | `qwen3-embedding:8b-q8_0` · 1024 · 8192 | The embedding model; `context_length` is Ollama's `num_ctx`, so long chunks are not cut silently |
| `embeddings.query_template` · `query_instruction` | from the Qwen3-Embedding model card · a code-search instruction | How queries are phrased |
| `embeddings.batch_size` · `request_timeout_seconds` · `keep_alive` · `max_incremental_files` | 32 · 300 · 10m · 20 | Ollama calls; above 20 files without vectors, ticket time leaves embedding to `cw context build` |

**Speed on this Mac**: the 8B model embeds about 1.5 chunks a second, so a first build of a repository with 30,000 chunks would take hours. If that is too slow, `qwen3-embedding:4b` or `:0.6b` is a settings change (`embeddings.model`); the next build re-embeds, and the eval shows what quality it costs.

