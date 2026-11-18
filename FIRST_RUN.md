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
| `defaults.limits.max_fix_rounds` | worker retries when lint/tests it broke fail | 2 |
| `defaults.limits.max_review_rounds` | worker fixes after the reviewer asks for changes | 2 |
| `defaults.limits.max_question_rounds` | times the planner may ask you, and a worker may ask per instruction | 1 |
| `defaults.limits.max_workers` | most workers the planner may spread a plan over; they take turns | 4 |
| `defaults.limits.agent_timeout_minutes` | an agent run that takes longer is stopped | 45 |
| `defaults.limits.check_timeout_minutes` | a lint or test command that takes longer counts as failed | 15 |
| `stacks.python.commands` | lint / test a new Python project starts with | `uv run ruff check .`, `uv run pytest` |
| `defaults.limits.max_turns.worker` | most steps one worker run may take | 40 |
| `default_protected_paths` | files agents may never read or edit | keys, certificates, `secrets/` (not `.env`: agents may update it) |

`defaults` only applies to projects added later. Each project has its own copy, which you edit in FRIDAY under the project's sliders button → **Settings**. A config written before these keys existed keeps working: the last three use the defaults above until you set them.

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

**Or start a project from nothing.** This creates the folder with a README and a `.gitignore`, makes the first commit, registers the project and has the planner plan the whole thing from your sentence. Nothing else is needed: no remote, no GitHub.

```bash
cd ~/code && cw new reminders "Remind me when the car insurance expires and when a service appointment is due"
```

Add `--github private` to also create the repository on GitHub with `gh`, so the ticket ends in a real pull request. Without a remote, `cw pr` reviews and commits on the ticket's branch and `cw done` merges it into `main`.

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

1. Pick the project in the sidebar. Its home page shows the checkout, what its tickets are doing, and a **Start a ticket** box (⌘N opens the same box from anywhere).
2. Type the task and press **Start**.
   - Small task: leave **Plan first** off, and a worker starts right away.
   - Bigger task: turn **Plan first** on. You'll get a plan to approve, or questions to answer, first.
   - **From** starts the branch from another branch than the project's base (`--base`).
   - Several features, each on its own branch: the planner says so, and **Start N tickets** creates one paused ticket per feature; start each with **Resume**.
3. Open the ticket. At the top: the pipeline (Plan → Work n/m → Checks → Review → PR → Merge) and one card per agent (planner, W1, W2, …, reviewer). The agent working now is highlighted; click a card to see its tasks, handoffs, questions and runs. The tabs show the plan, each worker's pass, the review's findings, every tool call live, and the cost.
4. The agent works on a new branch (`cw/<id>`) **in your own checkout**, so you see the changes in your IDE. Don't edit that project while it works. The box under the ticket answers questions, sends plan feedback or asks for more work.
   - Want to start something else in the same project first? **More → Park** (or `cw park <id>`) saves the work and switches you back. Only one ticket per project can have uncommitted work.
5. The bar above the box shows the next step and its button: **Approve plan**, **Resume**, **Review and open PR** or **Review only**. The reviewer always runs first; blocking findings get up to two fix rounds. Then cw makes **one** commit, pushes it and opens the PR, with the review posted on it. Nothing leaves your Mac before this step.
6. **Fix comments** (`cw fix`) sends the PR's comments and open blocking findings to the worker, round after round while blocking findings stay open. **Merge** (`cw merge`) squash-merges once CI has passed and no blocking finding is open. If you merged on the host yourself, use **More → Done**.

**Talking to FRIDAY.** Click the microphone next to the box, say what you want, click it again and send. Turn on **Speak replies** in Settings and FRIDAY says where the ticket stands when a step ends. Dictation needs FRIDAY to run as the app (`open friday/FRIDAY.app`); macOS asks once for the microphone and for speech recognition.

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

When the PR has comments, from the reviewer agent or from people, let the worker address them and push the fix:

```bash
cw fix <ticket-id>
```

Squash-merge once CI passed and no blocking finding is left; this also closes the ticket and deletes its branch:

```bash
cw merge <ticket-id>
```

Or merge on GitHub/Bitbucket yourself, then close the ticket:

```bash
cw done <ticket-id>
```

## Where things are

| What | Where |
|---|---|
| Configuration | `~/.cw/config.json` |
| Tickets, including history | `~/.cw/tickets/<project>/<id>.json`, and `<id>.md` to read: request, answers, plan, what each task left behind, reviews |
| Project maps and decisions | `~/.cw/projects/<project>/conventions.md` |
| Each ticket's code | branch `cw/<id>` in your own checkout; one commit at Open PR |
| Agents, skills, hooks | `toolkit/` in this repository |
| A run you want to look inside | every run's `session_id` is in the ticket: `claude --resume <id>` in the project |

## If something goes wrong

| You see | Do this |
|---|---|
| "Could not fetch origin/main" | Your SSH access to the repository is failing. Check `ssh -T git@github.com`. Pushing will fail the same way. |
| "already fails on main; reported, not enforced" | Fine. Fix the command in the project's settings if it's simply wrong. |
| Ticket **Paused** | You hit the Claude usage limit. Press **Resume** later. |
| Ticket **Blocked** | A real error (git, code host, agent crash, time limit) or your own Stop / Ctrl+C is shown on the card. Fix it if needed, then **Resume**: a task list continues where it stopped. |
| Ticket stays **In progress** but nothing runs | The process was killed. `cw resume <ticket>` continues it. |
| Ticket **Needs your answer** | The planner or a worker asked something only you know. Reply with `cw run <ticket> "<answer>"` or in the box under the ticket. |
| "has uncommitted changes; park it" | Another ticket (or your own work) is in that checkout. Park it, open its PR, or commit/stash your own changes. |
| FRIDAY shows nothing | Run `cw doctor` in a terminal, then check that FRIDAY → Settings has the right `cw` path. |
