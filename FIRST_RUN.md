# First run

From a fresh Mac to your first ticket. Commands assume this repository is at `~/Projects/Nexttech/Claude_wrapper_v2`; change the path if yours differs.

## 1. What you need

| Tool | Check | Install |
|---|---|---|
| Claude Code, logged in with your subscription | `claude --version` | see claude.com/claude-code, then run `claude` once to log in |
| git | `git --version` | Xcode Command Line Tools: `xcode-select --install` |
| uv | `uv --version` | `brew install uv` |
| Swift (for FRIDAY) | `swift --version` | comes with the Command Line Tools |
| gh, for GitHub projects | `gh auth status` | `brew install gh && gh auth login` |
| SSH access to your repositories | `ssh -T git@github.com` | add your key to GitHub or Bitbucket |

## 2. Install the backend (`cw`)

```bash
uv tool install --force ~/Projects/Nexttech/Claude_wrapper_v2/backend
```

This puts `cw` in `~/.local/bin`. Check it:

```bash
cw --help
```

Run the same command again after you pull changes to `backend/`.

## 3. Create the configuration

```bash
cw init --toolkit ~/Projects/Nexttech/Claude_wrapper_v2/toolkit --github-repo YOUR-ORG/nexttech-toolkit
```

- `--toolkit` is the local folder with the team's agents, skills and hooks.
- `--github-repo` is where the toolkit is published on GitHub. Teammates' Claude Code installs it from there, and only when you choose to write team settings into a repository.

That writes `~/.cw/config.json`. Everything in it can be changed later in FRIDAY → Settings, or by editing the file. The parts you're most likely to touch:

| Key | What it is | Default |
|---|---|---|
| `defaults.models` | model per agent: planner, worker, reviewer, onboarder | opus, sonnet, opus, sonnet |
| `defaults.limits.max_fix_rounds` | worker retries when lint/tests it broke fail | 1 |
| `defaults.limits.max_review_rounds` | worker fixes after the reviewer asks for changes | 1 |
| `stacks.python.commands` | lint / test a new Python project starts with | `uv run ruff check .`, `uv run pytest` |
| `defaults.limits.max_turns.worker` | most steps one worker run may take | 40 |
| `default_protected_paths` | files agents may never read or edit | `.env*`, keys, certificates, `secrets/` |

`defaults` only applies to projects added later. Each project has its own copy, which you edit in FRIDAY under the project's sliders button → **Settings**.

**Bitbucket Cloud projects only:** add these lines to `~/.zshrc`, then open a new terminal.

```bash
export BITBUCKET__EMAIL=you@company.com
export BITBUCKET__API_TOKEN=your-api-token
```

The token is never written to the config.

Check everything:

```bash
cw doctor
```

Anything marked `missing` comes with the command that fixes it.

## 4. Add a project

```bash
cw project add ~/code/my-service
```

This registers the repository, detects its stack and code host, and copies the stack's commands. **No agent runs and nothing is written into the repository.**

Then check the commands, because every repository is different. If `pyproject.toml` lives in `backend/`, for example, set the test command to `cd backend && uv run pytest`. A command that already fails on the base branch is only reported, never enforced.

Optional extras:
- `cw project onboard my-service`: maps the project with the onboarder agent. It costs tokens; useful for an unfamiliar repository without a `CLAUDE.md`.
- `cw project wire my-service`: writes the toolkit into the repo's `.claude/settings.json`, so teammates' interactive Claude Code gets the same agents and skills. Commit that file if the team agrees.

## 5. Run FRIDAY (the app)

Build it once, and again after you pull changes to `friday/`:

```bash
~/Projects/Nexttech/Claude_wrapper_v2/friday/build-app.sh
```

Then run it in one of these ways.

- **As an app:**
  ```bash
  open ~/Projects/Nexttech/Claude_wrapper_v2/friday/FRIDAY.app
  ```
- **From the terminal**, so its output stays there and Ctrl+C stops it:
  ```bash
  ~/Projects/Nexttech/Claude_wrapper_v2/friday/FRIDAY.app/Contents/MacOS/Friday
  ```
- **From source while developing:**
  ```bash
  cd ~/Projects/Nexttech/Claude_wrapper_v2/friday && swift run
  ```

FRIDAY runs `cw` through your login shell, so it sees the same `cw`, `claude`, `gh` and `uv` as your terminal. If there's no config yet, it opens a Setup screen that does steps 2 and 3 for you.

## 6. Your first ticket

In FRIDAY:

1. Pick the project in the **Projects** panel.
2. Type the task in the **chat** on the right and press **Start ticket**.
   - Small task: leave **Plan first** off, and a worker starts right away.
   - Bigger task: turn **Plan first** on. You'll get a plan to approve, or questions to answer, first.
   - Several features, each on its own branch: FRIDAY offers **Start N tickets**, one per feature.
3. Watch the **next-step card** at the top of the chat. It says what's happening, whether the code is still local or already pushed, any warnings, and the button to press next.
4. The agent works on a new branch (`cw/<id>`) **in your own checkout**, so you see the changes in your IDE, and changed files are coloured in FRIDAY's file tree. Don't edit that project while it works. Reply in the chat to adjust anything.
   - Want to start something else in the same project first? **Park** (in the card's More menu, or `cw park <id>`) saves the work and switches you back. Only one ticket per project can have uncommitted work.
5. Press **Review & open PR**. The reviewer checks the change, then cw makes **one** commit, pushes it and opens the PR. Nothing leaves your Mac before this step.
6. Merge the PR on GitHub or Bitbucket, then press **Mark done**.

Or, in the terminal:

```bash
cw run my-service "make the save button blue"
```

```bash
cw status
```

```bash
cw pr <ticket-id>
```

```bash
cw done <ticket-id>
```

## Where things are

| What | Where |
|---|---|
| Configuration | `~/.cw/config.json` |
| Tickets, including history | `~/.cw/tickets/<project>/<id>.json` |
| Project maps and decisions | `~/.cw/projects/<project>/conventions.md` |
| Each ticket's code | branch `cw/<id>` in your own checkout; one commit at Open PR |
| Agents, skills, hooks | `toolkit/` in this repository |

## If something goes wrong

| You see | Do this |
|---|---|
| "Could not fetch origin/main" | Your SSH access to the repository is failing. Check `ssh -T git@github.com`. Pushing will fail the same way. |
| "already fails on main; reported, not enforced" | Fine. Fix the command in the project's settings if it's simply wrong. |
| Ticket **Paused** | You hit the Claude usage limit. Press **Resume** later. |
| Ticket **Blocked** | A real error (git, code host, agent crash) is shown on the card. Fix it, then **Resume**. |
| "has uncommitted changes; park it" | Another ticket (or your own work) is in that checkout. Park it, open its PR, or commit/stash your own changes. |
| FRIDAY shows nothing | Run `cw doctor` in a terminal, then check that FRIDAY → Settings has the right `cw` path. |
