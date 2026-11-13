# Changes, bugs and open issues

A running log of what went wrong in real use, why, and what was changed. Newest first.

---

## 2026-11-13 (later) · Architecture scaling: worker verification, context packs, repo maps vs. graphs, token hygiene

**Why:** analyzing and solving five core failure modes regarding worker reliability, redundant codebase exploration, project knowledge representations across tickets, and prompt bloat.

### Challenges encountered and how they were resolved

| # | Challenge | Symptom & Root cause | Resolution |
|---|---|---|---|
| 1 | Worker accountability & ghost runs | Workers claim `status: "done"`, miss files they touched in `files`, or modify files outside their assigned task without alarm. cw trusted LLM claims directly. | Deterministic `git.snapshot()` captured before and after `_worker()`. `git.changed_files()` is now the source of truth, overriding LLM claims. Scope creep outside `task.files` attaches a `Scope:` warning to the ticket. |
| 2 | Redundant code re-reading (Intra-ticket) | Planner reads code with Grep/Glob, builds a plan, but that reading is discarded. The worker starts cold and re-greps/re-reads the same files from scratch. | **Planner Context Pack**: `PlannerResult` returns 5–15 `ContextAnchor` locations (`path:lines — why it matters`). `prompts.py` renders these line anchors directly to the worker, guiding Sonnet immediately to target code. |
| 3 | Cold starts & Knowledge Graphs vs. Maps (Inter-ticket) | Every ticket starts cold and re-discovers architecture. A full Knowledge Graph (`graphify`) was investigated. While visual graphs (`graph.html`) are great for human inspection, machine graph traversal burns excessive LLM tokens. | **Deterministic AST Repo Map (`cw.repo_map`)**: A zero-LLM local AST scanner extracts module docstrings, classes, and function signatures in <0.05s. Saves to `~/.cw/projects/<p>/repo_map.txt` on branch fetch/ticket finish and passes to Planner on turn 1. Also added `cw project map <name>`. |
| 4 | Task dependencies vs. Sequential execution | Proposals to add a DAG `depends_on` tree to tasks were rejected: LLMs misdeclare task dependencies, creating brittle stalls and race conditions. | Maintained strict sequential execution in one checkout (`S1` → `S2` → `...`). Solved late-task prompt bloat via progressive compaction of finished task notes rather than complex dependency graphs. |
| 5 | Reused worker session & ticket prompt bloat | Long worker sessions accumulate conversational history, ballooning context costs and degrading focus ("Lost in the Middle"). Full ticket markdown repeated stale handoffs. | **Progressive Compaction & Token Ceiling**: Completed tasks older than the latest finished task omit obsolete `For the next worker:` handoffs. Added `WORKER_SESSION_TOKEN_CEILING = 45000`: sessions reaching this ceiling refresh cleanly on the next turn seeded with compact state and `git diff --stat`. |
| 6 | Per-ticket monolithic memory file | Proposal to append ticket histories into a shared memory file was rejected: memory files quickly fill with noise across dozens of tickets. | Tickets stay isolated in `~/.cw/tickets/<project>/`. Permanent conventions live in `conventions.md`, code structure lives in `repo_map.txt`. |

### What changed in the codebase

1. **`backend/src/cw/repo_map.py`**: Local AST and regex scanner for Python and Swift.
2. **`backend/src/cw/pipeline.py`**: Before/after tree snapshots, scope creep checks, session token ceiling (`45,000`), auto-updating repo map.
3. **`backend/src/cw/models.py` & `friday/Sources/Friday/Models.swift`**: `ContextAnchor` schema and `context_pack` on `PlannerResult` and `Ticket`.
4. **`backend/src/cw/prompts.py`**: Injected `_context_pack` into `fresh_worker_prompt` and `_repo_map` into `planner_prompt`.
5. **`backend/src/cw/tickets.py`**: Compacted historical task handoffs.
6. **`backend/src/cw/cli.py`**: Added `cw project map <name> [--json]`.
7. **`toolkit/plugins/core/agents/`**: `planner.md` outputs context pack; `worker.md` reads context pack anchors first.
8. **Interactive Graph Tooling**: Created interactive visualizer `codebase_graph.html` (48 nodes, 141 edges) mapping `Claude_wrapper_v2` for human exploration.

### Verified
63 backend tests pass in `backend/tests/` (including `test_repo_map.py` and `test_cli.py`); `ruff check` and `ruff format` are clean.

---

## 2026-11-13 · Review of the whole project: bug fixes, worker sessions, rules that arrive, projects from zero

**Why:** a read-through of all the code and documents, with the bugs below confirmed by small tests, and a clearer goal: tell cw an idea and get a reviewed project, without touching the code and without wasting tokens.

### Bugs found and fixed

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Merge conflicts with the base could never be resolved: the worker fixed the files, then cw aborted the merge | git keeps a file "unmerged" until it is staged, and cw checked before staging | cw checks the files for conflict markers, stages them and commits the merge |
| 2 | After cw merged the base, the next review included everyone else's changes from `main` | the "already reviewed" marker was the tree before the merge | the marker moves with the merge |
| 3 | `git -C . commit`, `git branch --delete`, `git restore .`, `git clean -fd`, `cat secrets/key.txt` passed the guard; a grep for the text "git commit" was blocked | the guard matched text | the guard reads the command the way a shell splits it |
| 4 | Ctrl+C, FRIDAY's Stop or a crash left the ticket "in progress" for good; a reply then skipped the remaining tasks | only cw's own errors were recorded | every stop records the step; `cw resume` continues; a reply runs first, then the remaining tasks |
| 5 | Stop in FRIDAY stopped the shell, not the agent | the agent was a grandchild process | cw replaces the shell (`exec`), and runs agents and checks in a process group it kills on stop or timeout |
| 6 | An agent or a test command could hang forever | no time limit, and the turn cap was only passed inside the agent JSON | `agent_timeout_minutes`, `check_timeout_minutes`, and `--max-turns` on the command line |
| 7 | Agents were told to follow "the stack rules you were given" but got none | the rulebook was unpinned to save tokens, and no agent has the Skill tool to load it | a short pinned core, topic files shown by a hook, exact topics pinned for the reviewer (EXPLANATION §7) |
| 8 | The ruff hook reformatted whole files and reported old lint errors | it treated every problem in an edited file as the agent's | a file is held only to the standard it met in the last commit |
| 9 | The worker did not know the lint and test commands, or which checks already failed | the prompt never said | a "Checks" section in the worker's prompt |
| 10 | The reviewer never saw the worker's notes or whether the checks passed | the review prompt left them out | both are in the review prompt |
| 11 | Tokens of failed runs were lost; a run that hit the turn limit failed without saying why | the usage was dropped when the run failed | recorded on the ticket, with the reason |
| 12 | Opening a PR with no changes blocked the ticket; a PR reopened by `cw pr` got the same review posted twice | missing checks | refused up front; a review is posted once |
| 13 | FRIDAY could hang reading a large `cw … --json` answer | it read the output only after the command ended, and a pipe holds about 64 KB | it reads while the command runs |
| 14 | The Bitbucket token was visible to agents | they inherited cw's whole environment | removed from the agent's environment |
| 15 | Files a check command creates (a new `uv.lock`, a report) silently became part of the ticket's commit | cw stages everything in the checkout | the ticket now warns right after the baseline, naming the files |

### What changed in the design

1. **Workers keep their session.** The planner gives each task to a worker (`W1`…, at most `max_workers`, default 4). Related tasks, follow-ups, fixes and answers continue that worker's session, so nothing is re-read; a resumed worker is sent only what is new. Workers still take turns in one checkout.
2. **The ticket is the only channel between agents.** Each finished task records how it was solved, its files and a note for the next worker. The ticket is also written as `<id>.md`.
3. **Agents can ask.** Worker → planner → you. Capped by `max_question_rounds`.
4. **The review is mandatory and strict.** `cw pr` always reviews, then opens the PR with the findings on it; `--skip-review` is gone. `cw review` reviews without committing. The reviewer follows the new `pr-review` skill.
5. **Projects from zero.** `cw new <name> "<idea>"` creates the repository, registers it and plans it. Without a remote, `cw pr` commits on the branch and `cw done` merges it.
6. **`--base <branch>`** starts a ticket from another branch; its PR goes back there.
7. **Live view.** cw reads the agent's `stream-json` output and prints each tool call.
8. **The planner's task rules** are explicit: one outcome per task, complete instructions, a `Done when:` line, exact files, no file in two tasks, small tasks.
9. **FRIDAY:** dictation and spoken status, chat and look preferences, Resume for stopped tickets, Review only.
10. **Documents:** a `CLAUDE.md` for this repository; stale text removed.

### Verified

58 tests run the real cw code with a stand-in for `claude` (EXPLANATION §16 lists what they cover); backend lint and format are clean.

### Not verified yet

- Any real agent run after these changes. Start with a throwaway project: `cw new` with Haiku as every model.
- The Swift changes were written without a compiler. Run `./friday/build-app.sh` and fix or report what it prints.
- Real PRs on GitHub and Bitbucket, `cw new --github`, usage-limit detection.
- What the new design costs in tokens (EXPLANATION §14).

### Still open

- Decided: `.env` is no longer a default protected path, so agents can update it and `.env.example` when a task changes a setting. The engineering rule now reads "never print or commit `.env`" instead of "never read". Projects registered earlier keep their own list until you change it.
- The older items below (ComIntern SSH access, its old ticket, its commands).

---

## 2026-11-11 (later) · One worker, one branch, in your checkout

**Why:** after the first round of fixes, the remaining complexity was in parallel workers and worktrees, which this team doesn't need.
- **Worktrees:** a separate folder per ticket meant a missing `.venv` and `node_modules`, copying `.env`, a per-ticket setup command, and changes your IDE didn't show.
- **Parallel workers** on one branch needed file-ownership checks. Anthropic's own write-up notes that "most coding tasks involve fewer truly parallelizable tasks than research".

**What changed**

1. **Work happens in the project checkout.** cw checks it is clean, runs `git switch -c cw/<ticket> <base>`, and the agent edits there. Worktrees, `copy_files`, the per-ticket `setup` command and the Agent's copy / Your copy switch are gone.
2. **One ticket per project at a time.**
   - cw refuses to start another while the checkout has changes.
   - `cw park <id>` (FRIDAY: More → Park) saves the work as a WIP commit and switches back.
   - WIP commits are folded into the single commit at Open PR, unless the branch was already pushed.
   - `cw done` switches back to your previous branch; `--abandon` discards the ticket's changes and deletes its branch.
   - A per-project lock stops two cw processes from using the same checkout.
3. **Sequential task list instead of parallel workers.**
   - The planner returns an ordered list, usually one task.
   - Each task gets a fresh worker, which receives the request, plan, checklist, summaries of finished tasks and the files changed so far, then reads the code itself.
   - Resume continues from the first unfinished task.
   - `max_workers_per_ticket`, `depends_on` and the wave scheduler were removed.
4. **Onboarding is optional.** `cw project add` registers a repo in about 0.3 s with no agent and nothing written into the repo; the stack's lint and test commands come from `config.json`. **Map this project** (`cw project onboard`) runs the onboarder when you want it.
5. **Worker turn cap** lowered from 80 to 40.
6. **FRIDAY:**
   - the card shows "local on branch X", "parked" or "pushed";
   - the task list has checkmarks;
   - Park is in the More menu;
   - first-run guidance, a "waiting for you" badge, and clear New ticket / Reply modes in the chat.
7. **Bug found while testing:** `delete_branch` had been removed together with the worktree helpers, so Abandon failed. Restored, and every git helper the code calls was checked to exist.

**Verified** (throwaway repo, Haiku):
- a 2-task plan ran as 2 sequential workers, with 0 commits while working;
- a second ticket was refused until the first was parked;
- park returned to `main`, clean;
- Open PR made 1 commit and pushed it;
- Done and Abandon switched back, and Abandon deleted the branch.

Measured context per turn: planner about 4.4k, workers about 7.8k–11.9k.

---

## 2026-11-11 · First real run on ComIntern

### The prompt

> what features are there ? Add small features, like a calculator for taxes and a text displayer in a different branch each, and add a pr only for those features.

### What happened

- **Agent runs:** 1 planner, 17 worker and 2 reviewer runs for two small features. That used about 35% of a 5-hour usage window.
- **Tokens:** about 0.97M input, 63k output and 13M cache-read tokens.
- **Commits:** 14 of them on one branch (`feat`, `fix`, `style`, `chore: no change`, `chore: strip tooling…`), each also committing the ticket file.
- **Resume:** pressed by hand several times.
- **Result:** nothing pushed and no PR. The worker ended by asking you to cut the two branches yourself.

### Bugs and their root causes

| # | Symptom | Root cause |
|---|---|---|
| 1 | Every agent turn re-sent 35–80k tokens of context | `claude -p` loaded your **user-level** setup into every run: all claude.ai MCP connectors (Vercel alone has 200+ tool definitions), user plugins and settings. The worker also had *every* tool. On top of that, about 10k tokens of rules (`python-rules`, `clean-code`) were pasted into every run. Measured: 36.9k tokens for a run that only says "hi". |
| 2 | Workers reformatted and "fixed" the whole repo, then the reviewer asked to undo it | The onboarder's lint command (`ruff check . && ruff format --check .`) **already failed on `main`** (41 errors, 28 unformatted files). cw treated every failure as caused by the change and sent a worker to fix it. |
| 3 | Workers added pytest and streamlit, and changed `pyproject.toml` and `pytest.ini` | `uv run pytest` failed with "Failed to spawn pytest" because pytest isn't installed (setup `uv sync` failed: no `pyproject.toml` at the root it ran in). That is an environment problem, but cw sent it to the worker as a code problem. |
| 4 | 14 commits, ticket file in every commit, hard to see what changed | cw committed after every worker pass and stored the ticket inside the worktree (`.cw/tickets/…md`). |
| 5 | Had to press Resume after every failure | "Checks still failing" and "reviewer still asks for changes" marked the ticket **blocked**, even though these are normal outcomes, not errors. |
| 6 | 3 workers for 2 small features | The default `max_workers_per_ticket` was 3, and the planner was allowed to use it. |
| 7 | The agents tried to create two branches, the guard blocked them, and they gave up | The request asked for **two branches and two PRs**, but one cw ticket is exactly one branch and one PR. Nothing in the flow could split a request into several tickets. |
| 8 | `.cw/` folder in the project, and `.claude/settings.json` written | Onboarding wrote its report to `<repo>/.cw/conventions.md` and wired `.claude/settings.json` automatically. |
| 9 | "Could not fetch origin/main" on every run; no commits on GitHub | `git fetch` from `git@github.com:PaulTres/ComIntern.git` fails with *"make sure you have the correct access rights"*: SSH access to that repository isn't working from this Mac. cw only warned in the log and carried on, so a push could never have worked. |
| 10 | "Sandbox cannot reach github.com / pypi" | Your user settings enable the Claude Code sandbox, and the agent runs inherited it. |

Not caused by cw: `.agents/` and `AGENTS.md` in ComIntern were created on Oct 30 by Codex (their text says so), before cw existed.

### What changed

1. **Isolated agent runs.** Every run now passes `--strict-mcp-config` (no MCP servers) and `--setting-sources project,local` (no user settings or user plugins). The worker gets only Read, Edit, Write, Bash, Grep and Glob. Nothing is pinned into the prompt any more: `python-rules` and `clean-code` stay available as skills the agent loads when it needs them. **Measured: a turn starts at about 6k tokens instead of 37k.** In the end-to-end test a worker averaged about 10.7k tokens per turn, against about 60k before.
2. **Baseline checks.** Right after the worktree is created, cw runs lint and tests once on the untouched base. Only checks that pass there are enforced later; checks that already fail are reported on the ticket ("already fails on main; reported, not enforced") and never sent to a worker.
3. **Worker rules.** The worker must not touch dependency, lint or format config or CI files, must not reformat files it did not otherwise change, and must not try to fix the environment (missing tools, network, pre-existing errors). It reports those instead.
4. **No commits while working. One commit at the end.** Workers edit the worktree and cw commits nothing. `git status` in the worktree shows exactly the ticket's change. On **Open PR**, cw makes **one** commit, merges the base branch, pushes, and opens the PR. Reviews compare snapshots of the working tree (`git write-tree`), not commits.
5. **Tickets left the repository.** They are now `~/.cw/tickets/<project>/<id>.json`, never committed and never in a PR. Closed tickets stay there, which is your chat history (see 9).
6. **Onboarding writes nothing into the repository.** The report and decisions are in `~/.cw/projects/<project>/conventions.md`. `.claude/settings.json` is written only when you ask (`cw project wire <name>` or **Write team settings to repo** in FRIDAY), and later settings changes update it only if you did. The ComIntern `.cw/` folder and the cw-written `.claude/settings.json` were removed, and its conventions moved to `~/.cw/projects/ComIntern/`.
7. **Fewer runs by default:** 1 worker per ticket, 1 fix round, 1 review round, 1 question round. Your existing config was migrated. The planner is told one subtask is right for almost every change.
8. **No more Resume for normal outcomes.** Failing checks after the fix round, or remaining review findings, leave the ticket **Ready** with a warning. You choose: follow up, or **Open PR anyway** (`cw pr --skip-review`). Blocked and Paused now mean only real errors (git, code host, agent crash) or the usage limit.
9. **Splitting requests.** If a prompt asks for several changes on separate branches or PRs:
   - the worker stops before editing (2 turns), and the planner returns one standalone prompt per change;
   - FRIDAY shows **Start N tickets** (`cw split <id>`), which starts one ticket, branch and PR per change.
   - Tested: the "modulo + describe, each in its own branch" prompt became two tickets with two branches.
10. **Fetch failures are on the ticket** as a warning ("pushing will fail the same way"), not only in the log.
11. **FRIDAY:**
    - a **next-step card** on every ticket, in both the chat and the detail view, showing the state in plain words, where the code is ("Local only · 3 changed files, not committed or pushed", or "Pushed … · Open PR"), warnings, and the one or two buttons that matter;
    - a **History** section listing closed tickets, which open as conversations.

### Verified after the change

- Backend: `ruff check` / `ruff format` are clean and 3 tests pass. `claude plugin validate` passes on the toolkit.
- End to end on a throwaway repo with Haiku:
  - a direct ticket with a lint check that already fails was reported, not enforced;
  - 0 commits while working, and no `.cw` in the worktree;
  - a split prompt became 2 tickets;
  - `cw pr` ran the checks and reviewer, made exactly **one commit** and **pushed** it, then stopped at `gh` as expected (the test remote is a local folder).
- FRIDAY builds, and its models decode every new `cw --json` output.

### Still open

- **ComIntern SSH access.** `git fetch` and push to `git@github.com:PaulTres/ComIntern.git` fail. Check `ssh -T git@github.com` and that your key has access to that repository.
- **The old ComIntern ticket** (`cw/261111-what-features-are-there-add-a1b9`, 16 commits, worktree in `~/cw-worktrees/ComIntern/`) is untouched. It uses the old ticket format, so it no longer appears in cw. Delete it with `git worktree remove --force` and `git branch -D`, or keep the code.
- **ComIntern's commands need fixing.** Setup `uv sync` and test `uv run pytest` don't work there. Correct them in Project settings, or leave them: they are now only reported, not enforced.
- **Not tested live yet:** opening a real PR on GitHub or Bitbucket, and posting the review there.
- **Tokens of a failed run** are still not recorded on the ticket.
- **FRIDAY's layout** was not checked visually by me (no screen capture here).
