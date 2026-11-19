# How this project works

This document explains the whole project as it is today. It covers the idea, the concepts, the architecture, the workflow step by step, every agent, skill and hook, how the agents communicate, how one agent run is put together, the configuration, FRIDAY, and what is and is not verified. §17 tells, in order, how the project got here: every refinement, fix and optimization, and why it was made.

Setup and daily commands are in [FIRST_RUN.md](FIRST_RUN.md) and [README.md](README.md). The detailed log of each change, with the bugs behind it, is [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md).

---

## 1. The idea

A small team works on several projects with Claude Code. The goals:

1. **Centralize.** One shared toolkit of agents, skills, rules and hooks, so every teammate and every project works the same way and the code looks the same.
2. **Simple and cheap.** Each job goes to the right model with only the context it needs, in a fixed and predictable flow. The project is judged by three things: results at least as good as plain Claude Code, a multi-agent flow, and fewer tokens.
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
| **CLAUDE.md** | Instructions loaded into every session started in that folder. | A project's `CLAUDE.md` reaches every cw agent for free; cw keeps a short "Notes kept by cw" section in it (§6). |
| **Skill** | A folder with `SKILL.md`. Interactively, only its description is in context until it is needed. | `engineering`, `python-rules`, `pr-review`, `rule-precedence`, `ponytail`, `clean-code`, `bootstrap-project`. |
| **Agent (subagent)** | A markdown file whose front matter sets `name`, `description`, `tools` and `model`, with the instructions in the body. `claude --agent <name>` runs a whole session as that agent. | `planner`, `worker`, `reviewer`, `onboarder`. |
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
                  ├ ~/.cw/projects/<project>/repo_map.txt    the planner's map of the repository
                  ├ ~/.cw/projects/<project>/conventions.md  optional: onboarding's findings and decisions
                  └ gh / Bitbucket REST ──► pull request, review comments, CI, merge
```

Change the toolkit once, push it, and every teammate and every cw run uses the new version.

**What cw writes into a project's repository:** only the agents' code changes and the "Notes kept by cw" section of its `CLAUDE.md`. Both are in the ticket's diff, reviewed and committed once. `.claude/settings.json` is written only when you ask (`cw project wire <name>`).

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
| `project_memory.py` | The "Notes kept by cw" section of a project's `CLAUDE.md`. |
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
                                                                      │
                                       S1 (W1) ─► S2 (W1, same session) ─► S3 (W2, new session) ─► S4 (W1) …
                                       a worker's question ─► planner answers ─► or you do
                                       after every task: checks ─ broke? ─► one fix round by that worker
                                                         ─ still broken? ─► the ticket stops (BLOCKED)
 │
 ▼  READY: changes are uncommitted in your checkout · reply to adjust · cw review · or Park
you: cw pr
 │
 ▼
checks ─► reviewer (read-only, always) ─ blocking findings open? ─► fix round on every finding,
 │        re-review only the new diff (up to 2 rounds); cw decides from the severities
 ▼
cw: one commit → merge base (conflicts → worker → checks → review of those files) → push → open PR
 │        → post the review on it; no remote: stops after the commit; cw done merges it into the base
 ▼
you or teammates comment on the PR → cw fix: the last worker addresses every open line comment and every
 │        open blocking finding → checks → review → new commit → push → summary posted → threads resolved
 │        → again while blocking findings stay open (up to 3 rounds)
 ▼
you: cw merge → refused while CI has not passed on your commit or a blocking finding is open
         → squash-merge pinned to that commit → branch deleted there and locally → local base fast-forwarded
         → you are back on your previous branch     (or merge on the host yourself, then cw done)
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
- It refreshes the planner's repo map.
- If the containers do not start, the ticket is not created and the checkout goes back to where it was.

**2a. The direct route** (default)
- Worker `W1` gets the whole ticket and "Do now: <your prompt>". On the first pass it is told: if this needs more than `escalate_file_threshold` (5) files or a plan, return `needs_plan` before editing. Then the ticket moves to the planned route, with the worker's reason given to the planner.
- After the worker, cw runs the checks. A check the ticket broke gets up to `max_fix_rounds` (2) fix rounds; if it still fails, the ticket is `ready` with a `Checks: now failing` warning, so you decide.

**2b. The planned route** (`--plan`, or after an escalation)
- The planner reads the request, the repo map and the code it needs, then answers with one of:
  - **split**: the request is several independent changes that each need their own branch or PR. The ticket waits; `cw split <id>` creates one paused ticket per change and abandons the original, without running an agent. `cw resume <new id>` plans each one.
  - **questions** (at most 3, at most `max_question_rounds` rounds): the ticket waits for your answer.
  - **a plan**: what changes and why, the options it rejected, an ordered task list (each task with its worker, complete instructions, its files and a `Done when:` line), and a **context pack** of 5–15 `path:lines` the workers should read first.
- A planner that still asks after its last round is told once to plan with stated assumptions; if it still gives no plan, the ticket waits for you. An empty plan never reaches approval.
- Feedback on a plan (`cw run <id> "…"`) continues the planner's session and sends only the feedback, not the whole request again.
- `cw approve <id>` starts the tasks.

**3. Doing the tasks** (`cw approve`)
- Tasks run in order. Each goes to its worker: a worker with a session continues it and gets only what other workers did since its last turn, plus its task; a worker without one gets the whole ticket.
- **After every task cw runs the checks**, with the fix rounds above. In a task list, a check still failing after them stops the ticket (`blocked`), so the next task never builds on broken code. The cause is often outside the code (a missing setting, a database that is not running); the worker's summary says so. Fix it and `cw resume`: the task runs again, then the checks.
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
- Findings still open after the last round stay on the ticket and go on the PR; you decide.

**7. Opening the PR** (`cw pr`)
- It refuses a ticket with no changes.
- Checks, then the review (step 6). Nothing skips it.
- **One commit**: everything since the base, WIP commits from parking included, is squashed into one commit with the ticket's message. Once the branch is pushed, later changes (`cw fix`, a second `cw pr`) are added as new commits; the squash merge folds them into one again.
- cw merges the latest base into the branch. Conflicts go to the worker (`max_conflict_rounds`, 1); cw checks that no conflict markers are left, commits the merge, then runs the checks and reviews the resolved files before anything is pushed. A clean merge brings only the base's own reviewed work, so the review moves past it.
- No remote: it stops after the commit (`pr_open`, with a warning); `cw done` merges the branch into the base.
- Otherwise: push, open the PR (the description is built from the ticket: request, plan, what each pass did, the review), and post the review as line comments.

**8. Working off PR comments** (`cw fix`)
- It reads the unresolved comment threads on lines of the PR (the reviewer agent's and people's) and the ticket's open blocking findings, and gives them all to the last worker in one instruction.
- Then it runs the same steps as `cw pr`: checks, review, a new commit, push. It posts the worker's summary (which says why a comment needed no change) and resolves the threads it sent.
- Only the first review of a PR is posted as line threads. Later reviews are posted as one comment, so cw's own re-reviews never feed the next round; their blocking findings stay open on the ticket instead.
- It goes round again while blocking findings stay open, up to `max_pull_request_fix_rounds` (3), and stops after a round in which the worker changed nothing. Comments in the PR's general conversation are not read.

**9. Merging** (`cw merge`)
- It refuses while a blocking finding is open, while the ticket's branch has uncommitted changes, or while CI has not passed **on your local branch's commit**. A PR with no checks reported counts as passed only when the branch has no CI config (`.github/workflows`, `bitbucket-pipelines.yml`), so a merge right after a push waits for CI. On GitHub the PR's head must also be that commit.
- It squash-merges on the host with the ticket's commit message, **pinned to that commit**: GitHub refuses the merge if the head moved; on Bitbucket cw compares the head just before merging.
- It deletes the branch on the host, fast-forwards your local base branch (a warning if it has diverged), switches you back to your previous branch, deletes the local branch, refreshes the repo map and closes the ticket.
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
- **Project notes are capped**: at most two per worker run and five per ticket.

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

---

## 5. Agents

All live in `toolkit/plugins/core/agents/*.md`. The `model` in each file is only a default for interactive use; **cw always uses the model from the project's config**.

| Agent | Default model | Tools | Job | Answers with |
|---|---|---|---|---|
| planner | opus | Read, Grep, Glob | Turns a bigger request into a plan and a clear, ordered task list, each task given to a worker; splits multi-branch requests; asks at most 3 questions; answers the workers' questions | questions · split · plan · options considered · tasks · files · context pack · answers |
| worker | sonnet | Read, Edit, Write, Bash, Grep, Glob | Implements one request, task or fix with the smallest change. Never touches tooling it wasn't asked about. Asks instead of guessing. | status (done / question / needs_plan / blocked) · summary · files · handoff · questions · project notes · commit message · reason |
| reviewer | opus | Read, Grep, Glob | Applies `pr-review` and every rule that covers the changed files, strictly, to the diff only | summary · line comments (blocking / minor) · ids of open findings now fixed |
| onboarder | sonnet | Read, Grep, Glob | **Optional.** Maps a repository: lint and test commands, architecture, tests, differences from the team rules | stack · commands · architecture · tests · differences · warnings |

Every answer is forced into a JSON schema built from the pydantic models in `models.py` (`--json-schema`), so cw never parses free text.

**What each agent is told** (`prompts.py`):

| Agent | First prompt of a session | Resumed session |
|---|---|---|
| Worker | The ticket without status lines or reviews (request, answers, plan, tasks with what each finished one left behind), the files changed so far, the instruction, the context pack, the checks and which are its job, what is off limits, its limits | What other workers did since its last turn, then the instruction |
| Planner (planning) | The request, the repo map, the "new project" brief when there is no code yet, your feedback, and the constraints (questions allowed or not, how many workers, one branch per ticket) | Your feedback and whether it may still ask |
| Planner (answering a worker) | The ticket, then the worker's questions | The worker's questions |
| Reviewer | Always a new session: the request, the plan, the workers' notes, the check results, the open findings, a note about cw's `CLAUDE.md` section when the diff has it, and the diff | — |

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
- **The planner hands over what it read**: the context pack points every new worker session at the right lines.
- **The planner gets a repo map**: `~/.cw/projects/<project>/repo_map.txt`, made without an LLM from `git ls-files` (so `.gitignore` is respected): module docstrings, classes and function signatures for Python and Swift, at most 250 lines. When the full outline is too long it falls back to one line per file, then one line per folder, so no folder is dropped. It is refreshed at every ticket start, when a ticket is done or merged, and by `cw project map <name>`.
- **A resumed agent gets only what is new** (§5).

### Memory across tickets: the project's CLAUDE.md

Claude Code loads a project's `CLAUDE.md` into every session in that folder, cw's agents included (checked with cw's isolation flags). So it is cw's memory across tickets, and it is cached.

- A worker may return `project_notes`: a lasting fact a future session needs and cannot see quickly in the code (a command, where a kind of code lives, a decision and why, a trap). Usually it is empty; at most two per run.
- cw adds new ones, without duplicates, to `## Notes kept by cw` in that file (at most five per ticket). The change is part of the ticket's diff, so the reviewer sees it (and is told it ships on purpose) and it is committed with the code. Past 40 notes the ticket warns you to prune the section.
- A knowledge graph was considered and rejected: an agent pays tokens to walk it, and it saves nothing over a short map plus reading the right files.

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

**Never loaded:** your user settings (`~/.claude/settings.json`), your personal per-repo `.claude/settings.local.json`, your personal plugins, and your personal or claude.ai MCP servers. Measured on the first real run: a run that only said "hi" cost 36.9k tokens with the personal setup and 5.9k without. The toolkit's own plugins are switched off in `enabledPlugins` so a wired repository does not load them a second time from the marketplace.

**Permissions:**
- **Protected paths** (default `**/*.key`, `**/*.pem`, `**/secrets/**`) become `Read/Edit/Write` deny rules in every run, plus the guard for shell commands. `.env` is not on the list: agents may update it when a task adds or changes a setting. What is in it is then sent to the model, so keep production secrets out of it, or add `.env` to a project's protected paths.
- **Worker shell commands.** The worker runs with `acceptEdits`. Bash is allowed only for the project's lint and test commands (also narrowed: cw drops a command's trailing folder arguments, so `ruff check src tests` also allows `ruff check <any path>`; a trailing file, which may be the script itself, is never dropped), `head` and `tail` for piping their output, read-only git (`status`, `diff`, `log`, `show`) and `extra_allowed_bash`. Anything else is denied in headless mode. The worker's prompt says so, and the planner writes `Done when:` with only those commands.
- **Read-only agents.** Planner, reviewer and onboarder have no write tools.
- **What the rules do not stop.** They see the command an agent runs, not what that command runs in turn. The worker may run the tests, and the tests run code the worker can write (a `conftest.py`), which could commit, read a protected path or reach the network. The rules keep a cooperative model on its path and stop mistakes; they are not a sandbox against hostile text in the repository or the prompt. If that matters, run the checks in a container with a read-only `.git` and no network.
- **Secrets.** The Bitbucket token comes only from the environment and is removed from the environment agents run in.
- **Your work is protected.** cw refuses to start or switch tickets when your checkout has uncommitted changes, and Abandon is only offered for that ticket's own branch.
- **Two human checkpoints:** you approve plans, and you merge pull requests. cw merges only when you run `cw merge`.

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
cwd = the project checkout   env = yours without cw's secrets + project env + CW_RUN=1 + CW_PROTECTED_PATHS + CW_SHOWN_TOPICS
```

- cw reads the stream line by line and logs every tool call (`worker · Edit src/app/routes/items.py`), which is what you see live in the terminal. It also appends each start, tool call, end and failure, labelled with the worker and task (`W2 · S3`), to `<id>.events.jsonl`, together with every check cw runs. `cw events <id> --json` prints the latest; FRIDAY follows a ticket through it, wherever the run was started.
- The last `result` line carries the structured answer and the usage. The tokens of a failed run are recorded too, and each run carries its label, so cost can be shown per worker.
- The process runs in its own process group with a time limit (`agent_timeout_minutes`). Ctrl+C, FRIDAY's Stop and the time limit kill the agent and everything it started.
- **Errors.** Claude Code's own error text is kept. HTTP 429 or "usage/rate limit" makes the ticket `paused`. "No conversation found with session ID" means the session to resume is gone: cw starts a new one that gets the whole ticket. Any other failure makes the ticket `blocked` and keeps the session, so `cw resume` continues it instead of paying for a fresh one.

---

## 11. Tickets, events and history

- One JSON file, one markdown file and one events file per ticket in `~/.cw/tickets/<project>/`. The JSON is written through a temporary file, so FRIDAY never reads half of it.
- A ticket holds: status, route, branch, the branch it started from and goes back into, every prompt, questions and answers, plan, tasks with their worker, what each worker pass left behind, reviews, open findings, the baseline, warnings, PR, the recorded step and error, and every run's token usage, label and `session_id`.
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

**Shipped defaults** (a project keeps the profile it was added with; FRIDAY's **Apply models & limits to all projects** updates them):

| Limit | Default | What it caps |
|---|---|---|
| models | planner opus, worker sonnet, reviewer opus, onboarder sonnet | |
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
| `max_turns` | planner 40, worker 70, reviewer 30, onboarder 60 | turns per run |

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

**Measured:**

| Measurement | Before | After |
|---|---|---|
| Context of a run that only says "hi" | 36.9k tokens | 5.9k (2026-11-11) |
| Worker context per turn | 35–80k | 7.8–11.9k |
| Planner context per turn | ~19k | ~4.4k |
| Agent runs for "two small features" | 1 planner + 17 workers + 2 reviewers | split into 2 tickets, about 1 worker + 1 reviewer each |
| Commits per ticket | 14 | 1 |

Real tickets since (each in `CHANGES_AND_ISSUES.md`): a one-file test on Haiku, $0.015; an 11-file, 2-task change, about $1.47; tic-tac-toe from nothing (6 tasks, 4 workers), $2.49; a FastAPI expense tracker from nothing (17 tasks, 3 workers, 65 tests), about $8.06.

**Not measured yet:** the cost of the larger pinned rules since 2026-11-16 (about 9k tokens per agent, cached after the first turn), and every change of 2026-11-18. Measure with `cw stats` and `cw show <id> --json` (`runs`: tokens and turns per run). A run's `cost_usd` is what Claude Code reports, which for a resumed session is the session's total so far; `cw stats` subtracts the session's previous run, so each run is counted once.

---

## 15. FRIDAY

A window over cw, redesigned on 2026-11-18 around the agents. It only runs `cw` (through a login shell, so it sees the same `PATH` as your terminal) and reads its `--json` output: `config`, `project list`, `status`, `show`, `events`, `stats`, `toolkit` and `doctor`. `cw … --json` is its contract: a field added in `models.py` or `config.py` needs a default, and `Models.swift` if the app shows it.

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
| Settings | Health (`cw doctor --json`), the `cw` path, voice and look, the toolkit, and the models, turn caps and limits new projects copy |

Polling: the ticket list every 3 seconds, the open ticket and its events every 2 seconds while it runs and every 8 otherwise. `friday/test.sh` runs tests that decode real `cw` output (`friday/Tests/FridayTests/Fixtures`, made by `make_fixtures.py`) and check the stages, agents, costs and actions; `Friday --snapshot <fixtures> <folder>` draws the main screens into PNG files.

---

## 16. Verified, and not verified

**Verified by tests that run the real cw code with a stand-in for `claude`** (`cd backend && uv run pytest`, 106 tests):
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
- a run loads only the project's settings and its `.mcp.json`; the guard, the rules hook, the topic index and the ruff hook; time limits kill a command and everything it started; `cw stats` counts a resumed session once; project notes are capped.

**Verified on real projects with the real Claude Code and GitHub, on 2026-11-16 and 17:** three tickets on an existing repository and two projects from nothing (tic-tac-toe, an expense tracker); resumed worker and planner sessions; PRs opened and reviewed on GitHub; `cw fix` and `cw merge` end to end. On 2026-11-18 the real CLI confirmed the lost-session message, and that a project's `CLAUDE.md` still reaches an agent with `--setting-sources project`.

**Not verified:**
- anything changed on 2026-11-18 on a real ticket: the outside review's fixes, B9, B10, B13, B14, the isolation change, the new FRIDAY;
- that the rules hook's message reaches the agent as intended after a Read;
- FRIDAY by hand against a live run, dictation, speech and Stop;
- all of the Bitbucket side against a real server (`POST …/comments/{id}/resolve` comes from Atlassian's community answers);
- usage-limit detection;
- the links in §18 that start with `anthropic.com/research/` may have moved to `anthropic.com/engineering/`.

**Known bugs, not fixed yet:**
- cw's snapshots stage every file in git's real index, so a file the worker later adds to `.gitignore` is still committed. Check the PR's file list for IDE folders and stray files;
- `cw review` on a ticket whose PR is open sets it back to `ready`, and `cw merge` then refuses. Run `cw pr` again, then `cw merge`;
- a planned ticket's commit and squash message is the first task's message, not one for the whole change.

**Open from the outside review of 2026-11-18** (B1–B10 and B13–B15 are fixed):
- B11: `cw fix` resolves every thread it sent, also people's threads the worker declined;
- B12: onboarding again overwrites the decisions in `conventions.md`;
- B16–B21: Bitbucket review posting, task ids on re-planning, a non-atomic project lock, `tail -f` allowed for workers, `cw done` without a remote, and a stray `skills/` file in the repository.

**Known limits:** one open ticket per project; no Java stack plugin; the toolkit isn't published.

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
