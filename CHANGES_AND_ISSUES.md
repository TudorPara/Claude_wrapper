# Changes, bugs and open issues

A running log of what went wrong in real use, why, and what was changed. Newest first.

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
