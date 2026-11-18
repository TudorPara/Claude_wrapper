# Changes, bugs and open issues

A running log of what went wrong in real use, why, and what was changed. Newest first.

---

## 2026-11-18 · B9, B10, B13, B14; runs load only the project's own setup

**Why:** four of the outside review's open bugs, and cw runs still read the personal `.claude/settings.local.json` while blocking the project's own MCP servers.

| # | Problem | Fix |
|---|---|---|
| B9 | A PR with no checks reported yet counted as CI passed, so `cw merge` could merge right after a push | No checks passes only when the branch has no `.github/workflows` or `bitbucket-pipelines.yml`; GitHub also needs the PR's head to be the local branch's commit |
| B10 | The squash merge was not pinned to the commit that passed CI | GitHub's merge gets `sha=<checked commit>` and refuses a moved head; Bitbucket's merge call has no such field, so cw compares the head just before it |
| B13 | A resumed planner got the whole request and repo map again on every feedback round | A resumed planner gets only the feedback and whether it may still ask |
| B14 | Any run that printed no result dropped the worker's session and started a fresh, full-price run; a session that really was gone blocked the ticket instead | Checked with Claude Code: a lost session prints an error result, `No conversation found with session ID: …`. That message alone (new `SESSION_LOST`) starts a fresh session; every other failure keeps it. Error results now show Claude Code's `errors` text |
| Isolation | Runs read `.claude/settings.local.json` (personal) and `--strict-mcp-config` blocked the project's `.mcp.json` | `--setting-sources project`, plus `--mcp-config <checkout>/.mcp.json` when the file exists |

Also: the test stand-ins no longer pass the reviewer's removed `verdict` field. Not run on a real ticket yet.

---

## 2026-11-18 · Defaults: two review and two fix rounds

**Why:** one fix round often left a finding or a check that a second round would have fixed. The shipped defaults (`config.example.json`, which `cw init` copies) are now 2 review rounds, 2 fix rounds, 3 `cw fix` rounds and planner 40 / worker 70 / reviewer 30 / onboarder 60 turns. Projects keep the profile they were added with: change an existing one in its settings, or with FRIDAY's **Apply models & limits to all projects**. The tests pin their own limits to one round, so they do not depend on the defaults.

## 2026-11-18 · FRIDAY redesigned around the agents (branch `fix/review-findings`)

**Why:** FRIDAY showed a ticket as a chat and a log, so it was hard to see which agent was working, which worker had which task, or what the reviewer had left open. It also read too little of the ticket, counted a resumed session's cost more than once, and could not reach `cw new`, `--base`, `cw doctor`, or unregistering and mapping a project.

| # | Change | Where |
|---|---|---|
| 1 | Each agent run is recorded in `~/.cw/tickets/<project>/<id>.events.jsonl` (start, tool call, end, failure, checks), labelled with worker and task; `cw events <id> --json` prints them | `claude_runner.py`, `tickets.py`, `cli.py` |
| 2 | Every run carries its label (`W2 · S3`), so cost can be shown per worker | `models.py` `RunUsage.label` |
| 3 | `cw doctor --json` | `cli.py` |
| 4 | FRIDAY rebuilt: sidebar, ticket list, project home, a ticket view with the pipeline, one card per agent, an inspector, six tabs, the next-step bar and a composer; Activity, Usage with charts, Health in Settings; sheets for a new ticket (plan first, base branch), a new project (`cw new`) and adding a repository | `friday/Sources/Friday` |
| 5 | FRIDAY's tests decode real `cw` output and check stages, agents, per-run cost and actions; `--snapshot` draws the screens | `friday/Tests`, `friday/test.sh`, `Snapshot.swift` |
| 6 | `cw fix` goes round again while blocking findings stay open (up to 3), as before D2, but its re-reviews no longer open PR threads | `pipeline._fix_pull_request` |

Dropped from FRIDAY: the file tree, the code viewer and the free chat. Not checked by hand against a live run yet.

## 2026-11-18 · An outside review of cw (branch `fix/review-findings`)

**Why:** a reviewer agent read the backend, the toolkit and the docs at `6c3b6b8` and reported bugs (B), skill and prompt contradictions (C) and design problems (D). B1 to B8, all of C and D1, D2, D4, D5 and D6 are addressed here; D3 (latency) is left as it is on purpose.

| # | Problem | Fix |
|---|---|---|
| B1 | A merge-conflict resolution was pushed with no checks and no review, and marked as reviewed | After conflicts, cw runs the checks and reviews the resolved files before the push; only a clean merge moves the review past the base's changes |
| B2 | A review answering `approve` with a blocking comment passed every gate | cw decides from the severities; the reviewer no longer returns a verdict |
| B3 | A blocking finding disappeared once a later round reviewed only new work | Open blocking findings are kept on the ticket with ids (`F1`, ...); a review names the ones now fixed in `resolved`; the merge gate checks that list |
| B4 | The ruff hook deleted an import the agent had just added and said nothing | The hook leaves F401 and F841 to the lint check and tells the agent when it changed the file |
| B5 | The baseline ran before the containers started, so database tests were never enforced | The containers start before the baseline; if they do not, the ticket is not created and the checkout goes back |
| B6 | A planner that asked again after its last question round produced an empty plan that went to approval | cw tells it once to plan with assumptions; still no plan means the ticket waits for your reply |
| B7 | A killed `cw review` resumed as `cw pr` and opened a PR | The step is recorded before it runs; a stale review without one resumes as a review |
| B8 | `cw split` abandoned the original first, ran every split straight away, and one blocked split stopped the rest | It creates paused tickets without running agents, then abandons the original; `cw resume` plans each |
| C1–C6 | Rules that contradicted the flow: "a worker starts fresh and cannot ask", commands no agent can run (`uv pip show`, `git status`, `/ponytail`, the grep checklist), the 281-line review procedure with its own answer format pinned into every review, interactive ponytail text, "never touch `pyproject.toml`", and Should fix counted as blocking | Planner, worker, reviewer, `pr-review`, `engineering`, `rule-precedence` and ponytail rewritten to match what cw does; `core-rules` adapted; a 115-line `review-checks` topic replaces the procedure in reviews (reviewer rules about 13% smaller); only the procedure's Blocking is `blocking`; the planner's last task leaves cw's notes section in `CLAUDE.md` alone (B15); toolkit 0.4.0 |
| D1 | The docs called the permission rules a wall, but tests run code the worker wrote | EXPLANATION §9 says what the rules do not stop |
| D2 | `cw fix` posted each re-review as new threads, which fed the next round with minor findings too | Later reviews are posted as a comment, not as threads; the loop (up to `max_pull_request_fix_rounds`, 3) goes on only while blocking findings stay open, and stops after a round that changed nothing |
| D4 | Nothing checked the agents' paths | Context-pack entries and review findings on files that do not exist are dropped; a line past the end of a file moves to its last line |
| D5 | EXPLANATION said a model switch changes only `claude_runner.py` | §13 says that holds for the SDK, not for a local model |
| D6 | Required project notes grew `CLAUDE.md` without a gate | `project_notes` is optional, at most two a run and five a ticket |

Not done: B9 to B21 apart from B15 (CI with no checks yet, the merge not pinned to a SHA, `cw fix` resolving people's declined threads, re-onboarding wiping decisions, the resumed planner's full prompt, the session fallback, Bitbucket posting, task ids on re-planning, the lock, `tail -f`, `finish` without a remote, the stray `skills/` file). None of this has run on a real project yet.

## 2026-11-17 · Expense tracker from nothing (17 tasks, PostgreSQL); stop on broken checks, `cw fix` loop, containers

**Why:** the biggest ticket so far: a FastAPI expense tracker with cookie auth, seven migrations, CSV import, budgets, reports and a scheduled worker, built in 17 tasks by 3 workers, 65 tests, PR #1. It exposed three gaps in cw.

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Tasks S3 to S7 were built on untested code: every database test errored, and cw went on to the next task each time | `DATABASE__URL` was not set in the terminal that ran `cw approve`. cw gave each task one fix round, the worker reported the cause, and cw only added a `Checks: now failing` warning | in a task list, checks still failing after the fix round stop the ticket (BLOCKED); `cw resume` runs that task again, then the checks |
| 2 | `cw fix` had to be run by hand again after the reviewer's new comments | one `cw fix` was one pass | `cw fix` goes round again while the new review leaves comments, up to `max_pull_request_fix_rounds` (3); a round that changed nothing ends it |
| 3 | The database had to be started by hand, and `.env` could not be written by agents | workers may run only lint and tests, and `.env` was protected | `cw new` sets `compose_file` (`dev/docker-compose.yml`), cw runs `docker compose up --detach --wait` before every worker run and check, and a new project's agents write its local `.env` |

### Measured

Planner $1.34 (2 runs), reviewer $2.05 (4 runs), worker $4.67 (26 runs, 10.5M cached tokens read): about $8.06 for the whole service.

### Running the app afterwards

Starting the finished app by hand took a long detour with another assistant, for two reasons that cw now prevents for new projects:
- **Port 5432 was taken.** The hand-started test container (`expense-postgres`) held it, so `localhost:5432` reached that server, where the user `expense_tracker` does not exist. The error was "password authentication failed", which first sent the debugging after passwords and volumes. The fix was host port 5433 in `dev/docker-compose.yml`. The planner now tells the skeleton task to publish each service on a port picked for the project, not the default.
- **README commands with a comment after them broke in zsh.** `make run-api     # uvicorn ...` passed `#`, `uvicorn` and `--reload` to `make`, because zsh does not treat `#` as a comment when pasted. The planner now asks for one command per line with no comment, and cw's own README lists its commands in tables instead of a commented code block.

### Left open

- The squash commit message is still the first task's ("feat: add project files, settings and pagination models").
- Scope warnings are mostly empty `__init__.py` files, which hides the real ones.
- The ruff hook removes an import that is still unused after an edit, so workers learned to add the usage before the import.
- The expense tracker's own `compose_file` is not set: its compose file wants port 5432, which the hand-started container already uses.

---

## 2026-11-16 (night) · The Nexttech kit's rules replace the old Python rulebook; clearer names in cw

**Why:** the agents wrote `from src.tictactoe…` and `from src.inference…` imports. The toolkit's `python-rules` came from an older rulebook that prescribes `src.` imports, `AppException(AppErrorCode.X)` and one-line docstrings, all of which the `nexttech-claude-kit` (the conventions from the internship's code reviews) says are wrong in a Nexttech project.

### What changed

- `python-rules/topics/` now holds the kit's nine `nexttech-*` skills and its `PR_review.md`, copied with only the reviewers' and people's names removed: `#13 name` became `#13`, and the person the rules call the decision-maker became "the project lead". The old topic files and the old core are gone; `python-rules/SKILL.md` is only an index. The kit itself was not edited.
- `topics.json` gained an `always` list: `core-rules` and `python-style` go into every agent's system prompt. `new_project` gives `python-stack` and `service-architecture`; a review also gets `git-and-pr` and `pr-review-procedure`.
- `rule-precedence` says how the kit's rules apply inside a cw run, without editing them: cw commits, agents cannot invoke skills or read GitHub and Linear, questions go in the answer, reviews answer in cw's JSON.
- Cost: the pinned rules grew from about 110 to about 390 lines (about 9k tokens, ponytail included) for every agent, and a reviewer of a change touching a route gets about 20k tokens of rules. Both are cached after a session's first turn. Not measured on a real ticket yet.
- cw's own code got clearer names, behaviour unchanged (checked by comparing the code's structure with the names mapped back): `save_ticket` and `save_settings` → `write_…`, `discard_changes` → `delete_uncommitted_changes`, `pipeline._run` → `_resume_or_start_agent`, `_build_run_settings`, `_build_managed_entries`, `build_doctor_report`; no more `result`, `data`, `text`, `item` or one-letter names; the one code comment is gone.

- `ponytail` is now pinned for the planner, worker and reviewer. `core-rules` said every piece of code starts with `/ponytail`, but agents cannot invoke skills and ponytail was not in their prompt. `rule-precedence` now calls it one of the most important skills for writing code, and `pr-review` makes over-engineering a check.
- Contradictions resolved in the toolkit's own files: the review procedure's four levels map to cw's two (`pr-review`), tests are required only where the project has a test command, and the kit's file and skill names map to what a cw agent sees (`rule-precedence`).

### Left open

- `run_agent`, `run_process`, `_run_check` and `remove_project` use verbs the rules ban, but no listed verb fits; cw keeps `AppException` and one-line docstrings, which are its own rules.

---

## 2026-11-16 (evening) · A project from nothing: terminal tic-tac-toe

**Why:** the first `cw new` on a real GitHub account. One request (two players, or one against a computer with easy, medium and minimax-hard levels, rematches and a session score, logic separate from the terminal, an exhaustive test that hard never loses) became a private repository, a 6-task plan for 4 workers, PR #1 and a merge. The game works when played by hand, 51 tests pass, and the logic was read and is correct.

### What happened

- The plan was changed three times through `cw run <ticket>` (more modular and 4 workers, then simpler, then back). Each feedback resumed the planner's session.
- W1 built the skeleton and the rules, W2 the AI, W3 the terminal interface and game loop, W4 the README and `CLAUDE.md`. Each worker read only the files from earlier tasks it needed.
- The review asked for 8 changes. The fix round made them, the second review asked for one rename, and `cw fix` made it from the PR comment.

### Bugs found, not fixed yet

| # | Symptom | Root cause |
|---|---|---|
| 1 | `.idea/` (PyCharm) and a stray `skills/` file reached `main`, although the fix round added both to `.gitignore` and the second review called them fixed | `git.snapshot` stages every file in git's real index. The worker's `git rm --cached` was refused (only read-only git is allowed), and `.gitignore` does not unstage files. Fix: build snapshots in a scratch index (`GIT_INDEX_FILE`) |
| 2 | `cw merge` refused with "is ready; finish it first", and it took `cw pr` twice more to get back | `cw review` sets the ticket to `ready` even when its PR is open. Fix: a ticket with an open PR stays `pr_open` |
| 3 | The whole app was squash-merged as "chore: add project skeleton with uv, ruff and pytest" | cw keeps the first worker's `commit_message`. Fix: the planner writes one message for the whole plan; the worker's is for the direct route |

### Fixed

- A new project's `.gitignore` now lists `.idea/` and `.vscode/`.
- The stray `skills/system/run-workflow/SKILL.md` was removed from the tic-tac-toe repository and from this one, and `skills/` is ignored in both. Its origin is unknown: in tic-tac-toe it appeared ten seconds after the planner finished, and no agent session wrote it.

### Measured

Planner $0.82 (4 runs, because the plan changed three times), reviewer $0.71 (3 runs), worker $0.96 (8 runs): $2.49 in total.

---

## 2026-11-16 (afternoon) · Second Live_FER ticket: per-face tracking, through `cw merge`

**Why:** a bigger planned ticket (2 tasks, 11 files: a new face tracker and a shared analyzer for the webcam app and both backends) ran end to end, through `cw fix` and the first real `cw merge` (PR #3). The code was right; cw itself showed four bugs and one false alarm.

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Review 1 had 2 blocking and 3 minor findings; review 2 called the 3 minors "still not fixed" and made them blocking, which cost an extra `cw fix` run | the fix round sent the worker only the blocking findings, but the reviewer was asked to check all its previous findings | the fix round sends every finding, each with its severity |
| 2 | `cw stats` showed $1.76 for the worker; it had spent about $0.57 | Claude Code's `total_cost_usd` is the session's total so far when a session is resumed (checked with two Haiku calls: the resumed one reported the first one's cost plus its own) | `cw stats` subtracts the session's previous run; existing tickets are corrected too |
| 3 | After `cw merge`, the local `main` did not have the merge | cw merged on the host but never updated the local branch, and the repo map was then written from the old code | `cw merge` fast-forwards the local base branch before closing the ticket; a diverged branch gets a warning |
| 4 | The worker never ran ruff, and about 6 turns went to refused commands | only the exact lint and test commands were allowed (`ruff check src tests` is not `ruff check src scripts tests`), nothing could be piped into `tail`, and the planner wrote a `Done when:` with `python -c` | trailing folder arguments are dropped from the allowed commands, `head` and `tail` are allowed, the worker's prompt says what it may run, and the planner writes `Done when:` with those commands only |
| 5 | "Scope: S2 changed files outside its task: analyzer.py", though S2's instructions allowed it | the planner named the file in the instructions but not in `files` | a file named in the task's instructions is in scope |

### Found, not fixed

- **The worker edits one place per call.** S2 used 40 turns, exactly the worker's cap: 14 Edits for the two backends after a scripted rewrite was refused, and 4 Edits that failed because edits it sent together overlapped. Every turn re-reads the session from cache (460k cached tokens).
- **One analyzer is shared by every websocket client** in Live_FER, as the predictor was before; per-connection analyzers were out of the plan's scope.
- Nobody ran the Live_FER backend or webcam app: `fastapi` is not in its `.venv`, and there is no checkpoint.

### Measured

Planner $0.36 (1 run), reviewer $0.55 (3 runs), worker about $0.56 (4 runs in one session), so about $1.47. The worker read only the files the plan named.

---

## 2026-11-16 · First real ticket on Live_FER; `cw fix` and `cw merge`

**Why:** the first real ticket (`emotion`, "add a test that ResNetFER rejects an input with 3 channels", Haiku for every role) opened PR #2 with a blocking finding nobody could fix, and the PR's comments had no way back to a worker.

### What the ticket showed

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | The reviewer flagged `CLAUDE.md` as "blocking, outside the task"; the fix round changed nothing, so there was no second review and the PR opened with "1 blocking finding left" | nothing told the reviewer that cw writes the "Notes kept by cw" section on purpose | the reviewer's prompt explains the section whenever the diff contains it; a note is flagged only if it is wrong |
| 2 | PR comments, the agent's and people's, could only be fixed by hand or by retyping them into `cw run` | cw posted comments but never read them back | `cw fix <id>`: open line threads → last worker → checks, review, new commit, push → the worker's summary posted → threads resolved |
| 3 | Merging needed the host's web page, then `cw done` | cw had no merge step | `cw merge <id>`: refuses while CI has not passed or a blocking finding is left, then squash-merges, deletes the branch on the host and locally, closes the ticket |

### Found, not fixed yet

- **Fixed later the same day:** **The worker's `ruff check` was refused.** The worker may only run commands that start with the configured lint command (`.venv/bin/ruff check src scripts tests`). It ran `.venv/bin/ruff check tests/test_model.py`, chained after `;`. Lint is also not enforced in Live_FER, because it already fails on `main`.
- **Fixed later the same day:** **Minor findings are never sent back by `cw pr`.** Only blocking findings get a fix round, by design. `cw fix` now covers them, because the reviewer's minor comments are threads on the PR too.
- **A resumed planner gets the whole request and the repo map again** when you give feedback on a plan. Its session already has them.
- **Running `cw done` before merging closes the ticket.** Follow-ups are refused after that; `cw merge` avoids the order problem.

### Measured on that ticket

Three runs (worker, reviewer, worker fix round), $0.015 in total with Haiku. The worker ran 2 greps, read 2 files, and averaged about 8.4k tokens of context per turn over 13 turns: it did not read the codebase.

### Verified, and not verified

- 78 tests pass; ruff check and format are clean.
- The new GitHub code read the open threads and the checks of the real PR #2 correctly.
- Not run against a real host: resolving a thread and merging on GitHub, and all of the Bitbucket side. Bitbucket's resolve call (`POST …/comments/{id}/resolve`) comes from Atlassian's community answers, not from a test.
- The `emotion` project is back on opus (planner, reviewer) and sonnet (worker, onboarder).

---

## 2026-11-13 (evening) · Checking the scaling changes, and the project's CLAUDE.md as memory

**Why:** a review of the "Architecture scaling" changes below. Some of them did not do what that entry says, and you decided that cross-ticket memory should be the project's own `CLAUDE.md`, not a knowledge graph.

### Corrections to the entry below

- Its "63 tests pass; ruff is clean" was not true: `test_project_map_command` failed (its config used `extra_allowed_shell_prefixes` and had no `profile`), and ruff reported an unsorted import in `cli.py`.
- `codebase_graph.html` does not exist in the repository.
- FRIDAY had not been compiled; it builds (`swift build`, checked this time).

### Bugs found and fixed

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | A worker session never started fresh, however big it grew | the 45k ceiling compared `input_tokens`, which with prompt caching is only the uncached part; most of a session is `cache_read_tokens` | it compares the average turn, cached and uncached together (`RunUsage.context_per_turn`) |
| 2 | A worker could report `done`, change nothing, and pass | when git showed no change, cw fell back to the worker's own `files` | `files` is always git's list; an empty `done` adds a `No changes:` warning |
| 3 | A later task lost what an earlier task left for it | the compaction dropped the handoff of every task but the last finished one | it drops the older summaries instead; files and handoffs stay |
| 4 | Only the last scope problem was kept; a project in a subfolder of a repository flagged every file | one shared warning slot; git paths were relative to the repository root | one `Scope: S<n>:` warning per task; `git diff --relative` |
| 5 | In a large repository the repo map lost whole folders and was slow | it cut at 250 lines in alphabetical order, walked through `node_modules` and `.venv`, and ignored `.gitignore` | built from `git ls-files`; when the full outline is too long it falls back to one line per file, then per folder |
| 6 | A broken task became the base of the next one | the checks ran only after the last task | they run after every task, with the same one fix round |

### Research: knowledge graph or CLAUDE.md

- **A knowledge graph (for example graphify) was rejected.** It is useful to look at, but an agent would pay tokens to walk it, and it saves nothing over a short text map plus reading the right files.
- **Checked:** with cw's isolation flags (`--strict-mcp-config --setting-sources project,local`), both plain and as a `--agent`, Claude Code still loads the `CLAUDE.md` in the project folder (Claude Code 2.1.311, a Haiku run answered with a codeword that only that file contained). So that file reaches every agent for free, and it is cached.
- **Decided:** the project's own `CLAUDE.md` is the memory across tickets. It is committed and reviewed like code, and teammates' interactive sessions read it too. The repo map stays as well: it is the planner's index of *what exists*, built without an LLM; `CLAUDE.md` holds *how and why*.

### Agents must keep the notes up to date

- **Per ticket (already the case):** every agent answers through a required structured result, and cw writes it into `~/.cw/tickets/<project>/<id>.json` and `<id>.md` after every run. Agents do not write these files, so none can forget or corrupt them.
- **Per project (new):** every worker answer must have `project_notes` (an answer without it is rejected): lasting facts such as a command, where a kind of code lives, a decision. cw adds new ones, without duplicates, to `## Notes kept by cw` in the project's `CLAUDE.md` (`project_memory.py`). The change is in the ticket's diff, so it is reviewed and committed once. Past 40 notes the ticket warns you to prune the section.
- The rule "nothing is written into a project's repository" now has that one exception (`CLAUDE.md` in this repository).

### Verified

72 backend tests pass, including new ones for every fix above and for `project_memory.py`; ruff check and format are clean; FRIDAY builds.

### Not verified yet

- Any real agent run with these changes, and whether workers write useful `project_notes` or noise.
- Whether 45k tokens per turn is the right ceiling; measure it with `cw show <id> --json`.

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
