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
| Build a project from nothing | `cw new reminders "Remind me when the car insurance expires"` | terminal only |
| Do a small task | `cw run my-service "make the save button blue"` | type or dictate it in the chat |
| Plan a bigger one first | `cw run my-service --plan "add CSV export"` | turn on **Plan first** |
| Start from another branch | `cw run my-service --base develop "..."` | terminal only |
| Answer a question, or follow up | `cw run <ticket> "use semicolons"` | select the ticket, reply in the chat |
| Approve a plan | `cw approve <ticket>` | **Approve plan** |
| Have it reviewed, nothing committed | `cw review <ticket>` | **Review only** |
| Review it and open the PR | `cw pr <ticket>` | **Review & open PR** |
| Continue after a stop, an error or the usage limit | `cw resume <ticket>` | **Resume** |
| Clean up after merge | `cw done <ticket>` (`--abandon` to drop it) | **Mark done** / **More** |
| Pause a ticket to start another | `cw park <ticket>` | **More → Park** |
| Read a ticket | `cw show <ticket>`, or open `~/.cw/tickets/<project>/<ticket>.md` | select it |
| See everything | `cw status`, `cw stats`, `cw toolkit`, `cw project list` | Overview, Usage, Toolkit |

Every view has a `--json` form; FRIDAY uses exactly those. While a command runs, cw prints each tool call the agent makes, so you can follow it live.

## Develop

```bash
cd backend && uv sync && uv run ruff check src tests && uv run pytest -q
```

```bash
cd friday && swift build && ./.build/debug/Friday
```

```bash
claude plugin validate toolkit
```
