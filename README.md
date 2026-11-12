# cw + FRIDAY

A small team workflow around Claude Code. One shared **toolkit** (agents, skills, rules, hooks), one terminal orchestrator (**cw**) that runs those agents on tickets across all your projects, and one macOS app (**FRIDAY**) that shows and drives the same thing.

```
toolkit/   Claude Code plugin marketplace: the team's agents, skills, hooks  → push to GitHub
backend/   cw, the Python CLI: tickets, one branch per ticket, planner → tasks one at a time → checks → reviewer → one-commit PR
friday/    FRIDAY, the SwiftUI macOS app: a window over cw
```

First time? Follow [FIRST_RUN.md](FIRST_RUN.md). How it works and why: [EXPLANATION.md](EXPLANATION.md). What changed after real use: [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md).

## Requirements

- macOS 14+, Claude Code CLI (`claude`) logged in with your own subscription
- `git`, [`uv`](https://docs.astral.sh/uv/)
- `gh` (logged in) for GitHub projects; a Bitbucket Cloud API token for Bitbucket projects
- Swift toolchain (Xcode Command Line Tools are enough) to build FRIDAY

## Setup

1. **Publish the toolkit.** Push `toolkit/` as its own GitHub repository (for example `your-org/nexttech-toolkit`) and keep a local clone.

2. **Install cw.**

   ```bash
   uv tool install ./backend
   ```

3. **Write your config** (`~/.cw/config.json`):

   ```bash
   cw init --toolkit ~/Projects/nexttech-toolkit --github-repo your-org/nexttech-toolkit
   ```

4. **Check your setup.** `cw doctor` lists anything missing, including the external plugins (ponytail, pyright-lsp) with the exact install command.

   ```bash
   cw doctor
   ```

5. **Bitbucket only:** export your Atlassian email and API token (never stored in the config).

   ```bash
   export BITBUCKET__EMAIL=you@company.com BITBUCKET__API_TOKEN=...
   ```

6. **Add a project.** This registers it, detects the stack and code host, and takes the stack's setup/lint/test commands from the config. No agent runs and nothing is written into the repository. `--onboard` (or **Map this project** in FRIDAY) optionally runs the onboarder agent.

   ```bash
   cw project add ~/code/my-service
   ```

7. **Build FRIDAY** (optional). On first launch without a config, it shows a Setup screen that does steps 2, 3 and 6 for you:

   ```bash
   ./friday/build-app.sh && open friday/FRIDAY.app
   ```

## Daily use

| You want to | Terminal | FRIDAY |
|---|---|---|
| Do a small task | `cw run my-service "make the save button blue"` | type it in the chat on the right |
| Plan a bigger one first | `cw run my-service --plan "add CSV export"` | turn on **Plan first** |
| Answer questions or follow up | `cw run <ticket> "use semicolons"` | select the ticket, reply in the chat |
| Approve a plan | `cw approve <ticket>` | **Approve plan** (tasks then run one at a time) |
| Finish into a reviewed PR | `cw pr <ticket>` | **Review & open PR** |
| Resume after the usage limit | `cw resume <ticket>` | **Resume** |
| Clean up after merge | `cw done <ticket>` (`--abandon` to drop it) | **Mark done** / **More** |
| See the code | `cw project list` | Projects panel + file tree (the agent works on a branch in your checkout) |
| Pause a ticket to start another | `cw park <ticket>` | **More → Park** |
| See everything | `cw status`, `cw show <ticket>`, `cw stats`, `cw toolkit` | Overview, Usage, Toolkit |

Every view has a `--json` form; FRIDAY uses exactly those.

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
