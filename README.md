# cw + FRIDAY

A small team workflow around Claude Code. One shared **toolkit** (agents, skills, rules, hooks), one terminal orchestrator (**cw**) that runs those agents on tickets across all your projects, and one macOS app (**FRIDAY**) that shows and drives the same thing, by chat or by voice.

```
toolkit/   Claude Code plugin marketplace: the team's agents, skills, hooks  → push to GitHub
backend/   cw, the Python CLI: tickets, one branch per ticket, planner → workers taking turns → checks → reviewer → one commit → PR
friday/    FRIDAY, the SwiftUI macOS app: a window over cw
```

First time? Follow [FIRST_RUN.md](FIRST_RUN.md). How it works and why: [EXPLANATION.md](EXPLANATION.md). What changed after real use: [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md). Working on this repository with Claude Code: [CLAUDE.md](CLAUDE.md).

## Requirements

- macOS 14+, Claude Code CLI (`claude`) logged in with your own subscription
- `git`, [`uv`](https://docs.astral.sh/uv/)
- `gh` (logged in) for GitHub projects; a Bitbucket Cloud API token for Bitbucket projects
- Swift toolchain (Xcode Command Line Tools are enough) to build FRIDAY

## Setup

1. **Install cw.** Run this again after every change to `backend/`.

   ```bash
   uv tool install --force ./backend
   ```

2. **Write your config** (`~/.cw/config.json`). The toolkit can stay in this repository until you publish it.

   ```bash
   cw init --toolkit ./toolkit --github-repo your-org/nexttech-toolkit
   ```

3. **Check your setup.** `cw doctor` lists anything missing, with the command that fixes it.

4. **Bitbucket only:** export your Atlassian email and API token (never stored in the config).

   ```bash
   export BITBUCKET__EMAIL=you@company.com BITBUCKET__API_TOKEN=...
   ```

5. **Add a project you already have**, or let cw create one from an idea (see below).

   ```bash
   cw project add ~/code/my-service
   ```

6. **Build FRIDAY** (optional).

   ```bash
   ./friday/build-app.sh && open friday/FRIDAY.app
   ```

## Daily use

| You want to | Terminal | FRIDAY |
|---|---|---|
| Build a project from nothing | `cw new reminders "Remind me when the car insurance expires"` | **New project from an idea** |
| Do a small task | `cw run my-service "make the save button blue"` | **Start a ticket** on the project's page, or ⌘N |
| Plan a bigger one first | `cw run my-service --plan "add CSV export"` | turn on **Plan first** |
| Start from another branch | `cw run my-service --base develop "..."` | **From** in the new ticket box |
| Answer a question, or follow up | `cw run <ticket> "use semicolons"` | the box under the ticket |
| Approve a plan | `cw approve <ticket>` | **Approve plan** |
| Have it reviewed, nothing committed | `cw review <ticket>` | **Review only** |
| Review it and open the PR | `cw pr <ticket>` | **Review and open PR** |
| Continue after a stop, an error or the usage limit | `cw resume <ticket>` | **Resume** |
| Let the worker address the PR's comments and open blocking findings, round after round while blocking findings stay open | `cw fix <ticket>` | **Fix comments** |
| Squash-merge once CI passed | `cw merge <ticket>` | **Merge** |
| Clean up after merging yourself | `cw done <ticket>` (`--abandon` to drop it) | **More → Done** / **Abandon** |
| Pause a ticket to start another | `cw park <ticket>` | **More → Park** |
| Read a ticket | `cw show <ticket>`, or open `~/.cw/tickets/<project>/<ticket>.md` | select it: pipeline, agents, Plan, Work, Review, Cost |
| Follow the agents live | `cw events <ticket>` | the agent cards and the **Activity** tab |
| See everything | `cw status`, `cw stats`, `cw toolkit`, `cw project list`, `cw doctor` | sidebar, Usage, Toolkit, Settings → Health |

Every view has a `--json` form; FRIDAY uses exactly those. While a command runs, cw prints each tool call the agent makes, so you can follow it live.

## Examples

Each command goes on its own line; paste them one at a time. `cw` prints the ticket id when a step ends: use it in place of `<ticket>`.

### Build a new project from an idea

Create the repository (here a private GitHub repo in `~/Projects/habits`) and let the planner plan it:

```bash
cw new habits "A habit tracker REST API with FastAPI and PostgreSQL: habits, daily check-ins and streaks" --path ~/Projects --github private
```

Read the plan, send feedback if you want changes, then approve it:

```bash
cw show <ticket>
```

```bash
cw run <ticket> "split the first task in two"
```

```bash
cw approve <ticket>
```

The workers build it task by task, and cw runs lint and tests after each one. When the project needs a database, the first task writes `dev/docker-compose.yml` and a local `.env`, and cw starts the containers itself. Then review, open the pull request, work off its comments and merge:

```bash
cw review <ticket>
```

```bash
cw pr <ticket>
```

```bash
cw fix <ticket>
```

```bash
cw merge <ticket>
```

### Change or add a feature in a repository you already have

Register the repository once. `--onboard` also has the onboarder agent map it (stack, commands, how it differs from the team rules), which costs one agent run:

```bash
cw project add ~/code/my-service --onboard
```

A small change goes straight to a worker:

```bash
cw run my-service "make the save button blue"
```

A bigger one gets a plan first, which you approve:

```bash
cw run my-service --plan "add CSV export of the monthly report"
```

```bash
cw approve <ticket>
```

From there it is the same as above: `cw review`, `cw pr`, `cw fix`, `cw merge`. If a step stops (a failing check, an error, the usage limit), fix the cause and continue with `cw resume <ticket>`.

### Watch what is happening

The command you ran prints every tool call the agent makes. In other terminal tabs:

Ticket status, refreshed every 5 seconds:

```bash
while true; do clear; cw status --project my-service; sleep 5; done
```

The files the workers are changing:

```bash
while true; do clear; git -C ~/code/my-service status -s; git -C ~/code/my-service diff --stat; sleep 5; done
```

The ticket as it fills in (plan, each task's result, reviews):

```bash
tail -f ~/.cw/tickets/my-service/<ticket>.md
```

Tokens and cost per project and role:

```bash
cw stats
```

## All commands

`<ticket>` is a ticket id, `<project>` a project name, and `[...]` is optional. `cw <command> --help` shows the same.

**Setup**

| Command | What it does |
|---|---|
| `cw init --toolkit <path> --github-repo <owner/name> [--force]` | Write a starter config |
| `cw config [show] [--json]` | Print the config |
| `cw config set < config.json` | Replace the config with JSON from stdin |
| `cw doctor [--json]` | Check binaries, tokens, toolkit, plugins, SQLite FTS5, Ollama and each project's context index |
| `cw toolkit [--json]` | Agents, skills and models |

**Projects**

| Command | What it does |
|---|---|
| `cw project add <path> [--name <name>] [--host github\|bitbucket] [--onboard]` | Register a repository |
| `cw project onboard <project>` | Map it with the onboarder agent |
| `cw project remove <project> [--purge] [--yes]` | Unregister it and delete its caches (index, repo map); `--purge` also deletes its decisions and tickets |
| `cw project list` | Each project's codebase state, as JSON |
| `cw project wire <project>` | Write the toolkit into the repo's .claude/settings.json |
| `cw project map <project> [--json]` | Generate or print the repo map |
| `cw new <name> "<idea>" [--path <path>] [--stack <stack>] [--github private\|public]` | Create a project from an idea |

**Tickets, in the order you use them**

| Command | What it does |
|---|---|
| `cw run <project> "<request>" [--plan] [--base <branch>]` | Start a ticket |
| `cw run <ticket> "<follow-up>"` | Answer a question, give plan feedback, or follow up |
| `cw approve <ticket>` | Approve the plan and run the workers |
| `cw split <ticket>` | One paused ticket per change the planner split out; start each with `cw resume` |
| `cw resume <ticket>` | Re-run the step that paused, was blocked or stopped |
| `cw review <ticket>` | Checks and reviewer; nothing is committed |
| `cw pr <ticket>` | Review, commit once, push, open the pull request |
| `cw ci <ticket>` | The pull request's CI status |
| `cw fix <ticket>` | The worker answers each PR comment (fixed or declined) and the open blocking findings; one review per round, skipped for a small comment-only fix; again while blocking findings stay open (at most 3 rounds) |
| `cw merge <ticket>` | Squash-merge once CI passed and no PR thread or blocking finding is open, close the ticket |
| `cw done <ticket> [--abandon]` | Close the ticket and switch back |
| `cw park <ticket>` | Save the work as a WIP commit and switch back |

**Looking at things**

| Command | What it does |
|---|---|
| `cw status [--project <project>] [--include-archived] [--json]` | List tickets |
| `cw show <ticket> [--json]` | One ticket |
| `cw stats [--json]` | Token usage per project and role |
| `cw events <ticket> [--last N] [--json]` | The ticket's latest agent events: starts, tool calls, ends, failures, checks |

**The context engine and its data**

| Command | What it does |
|---|---|
| `cw context build <project> [--prune]` | Build the whole index at HEAD, embeddings included (needs Ollama) |
| `cw context status <project> [--json]` | What the index holds, whether it is current, its size |
| `cw context search <project> "<text>" [--no-hyde] [--json]` | The seed the planner would get |
| `cw context eval <project> [--from-commits N] [--method <name>] [--yes] [--json]` | How often the seed finds the files changes really touched |
| `cw context migrate-notes <project>` | Copy CLAUDE.md's old "Notes kept by cw" into the index |
| `cw gc [<project>] [--dry-run] [--json]` | Remove old index data and expired event logs |

**Benchmarks** (`BENCHMARK.md`)

| Command | What it does |
|---|---|
| `cw audit <ticket> [--json]` | One extra review of a ticket's final diff; kept out of its cost |
| `cw benchmark <project> [--json]` | Cost, reviews per model, fix rounds and audit results per ticket |

## Develop

```bash
cd backend && uv sync && uv run ruff check src tests && uv run pytest -q
```

```bash
cd friday && swift build && ./test.sh && ./.build/debug/Friday
```

`friday/test.sh` decodes real `cw` output (`friday/Tests/FridayTests/Fixtures`, rebuilt by `uv run --project backend python friday/Tests/make_fixtures.py`). `./.build/debug/Friday --snapshot friday/Tests/FridayTests/Fixtures <folder>` draws the main screens to PNG files.

```bash
claude plugin validate toolkit
```
