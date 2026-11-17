# How this project works

The current state of the project: the concepts it builds on, the architecture, the workflow, every agent, skill and hook, how agents communicate, how one agent run is assembled, the configuration, what was measured, what is still unverified, and further reading. The history of why things changed is in [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md); setup is in [FIRST_RUN.md](FIRST_RUN.md).

---

## 1. The idea

A team works on several projects with Claude Code. The goals:

1. **Centralize.** One shared toolkit of agents, skills, rules and hooks, so every teammate and every project works the same way and the code looks the same.
2. **Simple and cheap.** Each job goes to the right model, with only the context it needs, in a fixed and predictable flow.
3. **Keep quality.** Deterministic checks, a strict review before every PR, and a human approving plans and merging PRs.
4. **From an idea to a project.** `cw new` starts a repository from one sentence and plans the whole thing, so a project can be built without touching the code.

The repository has three parts:

| Part | What it is |
|---|---|
| `toolkit/` | A Claude Code **plugin marketplace**: the team's shared agents, skills, rules and hooks. Push it to GitHub. |
| `backend/` → `cw` | A small Python CLI that runs the toolkit's agents on **tickets**, one branch per ticket, in the project's own checkout. |
| `friday/` → FRIDAY | A macOS SwiftUI app: projects, file tree, code viewer and a chat you can type or talk into. It runs `cw` and shows its JSON. |

There is no server, database, daemon or custom agent framework. Claude Code does the agent work; cw only decides which agent runs, with what context, and what happens next.

---

## 2. Claude Code concepts used here

| Concept | What it is | Where it appears |
|---|---|---|
| **Session / context window** | One conversation with Claude. Everything in it is re-sent on every turn, so it costs tokens on every turn. Parts that did not change are read from the prompt cache at about a tenth of the price. | The planner has one session per ticket; each worker has one session for all its tasks. |
| **Resuming a session** | `claude -p --resume <id>` continues a session. Its history is still cached for a while, so continuing is cheap, and the agent keeps what it has read. | A worker's second task, a follow-up, a fix, an answered question. |
| **CLAUDE.md** | Instructions loaded into every session in that folder. | Your projects' own `CLAUDE.md` files are respected; the last task of a new project writes one. |
| **Skill** | A folder with `SKILL.md`. Interactively, only its description is in context until it's needed. | `engineering`, `python-rules`, `pr-review`, `rule-precedence`, `clean-code`, `bootstrap-project`. |
| **Agent (subagent)** | A markdown file whose front matter sets `name`, `description`, `tools` and `model`, with the instructions in the body. `claude --agent <name>` runs a whole session as that agent. | `planner`, `worker`, `reviewer`, `onboarder`. |
| **Hook** | A command Claude Code runs on an event (`PreToolUse`, `PostToolUse`, …). Exit code 2 sends its message to Claude; before a tool call it also blocks the call. It runs every time, unlike a skill, which is only advice. | `guard.py`, `rules_on_touch.py`, `ruff_on_edit.py`. |
| **MCP server** | An external tool server. Each server's tool definitions cost context on every turn. | None bundled (section 9). |
| **Plugin / marketplace** | A plugin bundles agents, skills, commands and hooks. A marketplace is a git repository listing plugins. | `toolkit/` with the `core` and `python` plugins. |
| **Headless mode** | `claude -p` runs one prompt without the interactive UI; `--output-format stream-json` prints every step as a JSON line; `--json-schema` forces a structured answer. | Every cw agent run. |
| **Agent SDK** | A Python/TypeScript library that runs the Claude Code binary from your own code. | Not used, because of subscription login (section 13). |

---

## 3. Architecture and what is centralized

```
            toolkit (one git repo on GitHub, shared by the team)
            ├─ core:   agents planner · worker · reviewer · onboarder
            │          skills engineering · pr-review · rule-precedence · clean-code · /core:bootstrap-project
            │          hook guard.py (blocks dangerous shell commands)
            └─ python: skill python-rules (index + topics/: the nexttech-claude-kit rules, without reviewers' names)
                       hooks rules_on_touch.py (shows a topic) · ruff_on_edit.py
                 ▲ loaded into every cw run (--plugin-dir)        ▲ installed by each teammate's Claude Code
                 │                                                │ when a repo is wired (.claude/settings.json)
FRIDAY ──runs──► cw ──claude -p --agent …──► Claude Code ──edits──► the project checkout, on branch cw/<ticket>
  ▲ reads --json  │
  └───────────────┤ ~/.cw/config.json                       projects, models, limits, stacks
                  ├ ~/.cw/tickets/<project>/<id>.json + .md  tickets = state + the readable record
                  ├ ~/.cw/projects/<project>/conventions.md  optional project map and decisions
                  └ gh / Bitbucket REST ──► pull request + review comments
```

Change the toolkit once, push it, and every teammate and every cw run uses the new version.

**What is not centralized yet:**
1. The toolkit isn't pushed to GitHub. Teammates' interactive Claude Code only picks it up after that and after a repository is wired with `cw project wire`.
2. No MCP servers are bundled (section 9).
3. Each person has their own `~/.cw/config.json`, so models and limits are personal.

---

## 4. The workflow

```
you: prompt (terminal, FRIDAY chat or voice)        cw new <name> "<idea>" also creates the repository first
 │
 ├─ checkout must be clean ─► cw: git switch -c cw/<ticket> <base, or --base branch> ─► baseline: run lint + tests once
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
 ▼
checks (only those that passed on the base; after each task, or once after a direct pass) ─ broke? ─► one fix round, then report
 │
 ▼  READY: changes are uncommitted in your checkout · reply to adjust · cw review · or Park
you: cw pr
 │
 ▼
reviewer (read-only, strict, always) ─ blocking findings? ─► one fix round on every finding, re-review only the new diff
 │
 ▼
cw: one commit → merge base (conflicts → worker, once) → push → open PR → post the review on it
 │        no remote: stops after the commit; cw done merges the branch into the base
 ▼
you or teammates comment on the PR → cw fix: the last worker addresses every open line comment
 │        (the reviewer agent's and people's) → checks → review → a new commit pushed → its summary
 │        posted on the PR → those threads resolved → the new review's comments → again, until
 │        none are left (at most `max_pull_request_fix_rounds`, 3)
 ▼
you: cw merge → refused while CI has not passed or a blocking finding is left → squash-merge on
         GitHub/Bitbucket, branch deleted there and locally → you are back on your previous branch
         (or merge on the host yourself, then cw done)
```

**Key properties:**
- **One branch per ticket, made by cw, in your normal checkout.** Agents never create branches, commit or push; a hook blocks them. You see the changes in your IDE, and your existing `.venv` and `node_modules` work.
- **One ticket per project at a time.** cw refuses to start a second one while the checkout has changes. **Park** saves the work as a WIP commit and switches you back; the WIP is folded into the single commit at PR time.
- **Workers take turns; nothing runs in parallel.** The planner gives each task to a worker (`W1`, `W2`, … up to `max_workers`). A worker keeps one session for all its tasks, so related tasks reuse what it has already read instead of paying for a new session and re-reading the project. A different worker is for an unrelated area. All of them edit the same checkout, one after another, so there are no conflicts between them.
- **A small request is one worker run.** There is no cheaper unit: cw has no model of its own. Follow-ups, fixes and answers continue in that worker's session.
- **Nothing is committed or pushed until `cw pr`.** Then exactly one commit is made.
- **The review cannot be skipped.** `cw pr` always reviews first and then opens the PR. Findings left after the fix round are on the PR and on the ticket; you decide what to do with them. `cw review` runs the same review alone. The reviewer is told that the "Notes kept by cw" section of `CLAUDE.md` ships on purpose, whenever the diff contains it.
- **PR comments are worked off with `cw fix`.** It reads the open (unresolved) comment threads on lines of the PR, from the reviewer agent and from people, gives them to the last worker in one instruction, then runs the same steps as `cw pr`: checks, review, a new commit, push. It posts the worker's summary on the PR, which says why a comment needed no change, and resolves the threads it sent. Comments in the PR's general conversation are not read. The new review can leave new comments, so `cw fix` goes round again until none are left, at most `max_pull_request_fix_rounds` times (3). A round in which the worker changed nothing ends it, and comments still open afterwards get a `Pull request:` warning.
- **`cw merge` squash-merges, and only when it is safe.** It refuses while the PR's CI has not passed (a PR without checks passes), while cw's last review left a blocking finding, or while the checkout has uncommitted changes on the ticket's branch. Then it squash-merges on the host with the ticket's commit message, deletes the branch there, fast-forwards your local base branch to the host's (a warning if it has diverged), deletes the local branch, and closes the ticket like `cw done`.
- **A task whose checks still fail after its fix round stops the ticket** instead of letting the next task build on untested code. The cause is often outside the code (a missing setting, a database that is not running), and the worker's summary says so. Fix it and run `cw resume`: the task runs again, then the checks.
- **cw starts the project's containers.** When a project has a `compose_file` and the file exists, cw runs `docker compose --file <file> up --detach --wait` before every worker run and every check, because workers may run only the lint and test commands. It returns at once when the containers already run. A failure (Docker not running, a port already taken) stops the ticket with Docker's reason. `cw new` sets `compose_file` to `dev/docker-compose.yml`, and the planner's skeleton task writes that file, with a healthcheck per service.
- **A new project's agents write its `.env`.** `cw new` leaves `.env` off the project's protected paths, so the skeleton task writes working local development values (random values for local secrets, never real ones) next to `.env.example`. `.env` is gitignored, so it never reaches the repository; the tests use their own database.
- **Every repeat is capped** by config: fix rounds, review rounds, question rounds and conflict rounds default to 1, PR fix rounds to 3, every agent has a turn cap and a time limit. Nothing loops by itself.
- **A stop is never final.** Ctrl+C, FRIDAY's Stop, an error, a time limit or the usage limit leave the ticket blocked or paused with the step recorded; `cw resume` continues, and a task list continues at the first unfinished task.

---

## 5. Agents

All live in `toolkit/plugins/core/agents/*.md`. The `model` in each file is only a default for interactive use; **cw always uses the model from the project's config**.

| Agent | Default model | Tools | Job | Answers with |
|---|---|---|---|---|
| planner | opus | Read, Grep, Glob | Turns a bigger request into a plan and a clear, ordered task list, each task given to a worker; splits multi-branch requests; asks you at most 3 questions; answers the workers' questions | questions · split · plan · options considered · tasks |
| worker | sonnet | Read, Edit, Write, Bash, Grep, Glob | Implements one request, task or fix with the smallest change. Never touches tooling it wasn't asked about. Asks instead of guessing. | done / question / needs_plan / blocked · summary · files · handoff · commit message |
| reviewer | opus | Read, Grep, Glob | Applies the `pr-review` skill and every rule that covers the changed files, strictly, to the diff only | approve / changes · PR summary · line comments (blocking / minor) |
| onboarder | sonnet | Read, Grep, Glob | **Optional.** Maps a repository: lint/test commands, architecture, tests, differences from the team rules | stack · commands · architecture · tests · differences · warnings |

---

## 6. How agents communicate: the ticket

Agents never talk to each other directly. Everything goes through the ticket, which cw keeps as `~/.cw/tickets/<project>/<id>.json` (the state) and writes next to it as `<id>.md` (the same content to read).

The `.md` holds, in this order: the request (all your prompts), questions and answers, the plan, the task list, other work (follow-ups and fixes), and the reviews. Under each finished task is what its worker left behind:

```
### [x] S1 · Add the reminders table and repository (worker W1)
- Solved by W1: <how it was solved, a few lines>
- Files: src/app/schemas/reminder.py, src/app/repositories/reminder_repository.py
- For the next worker: ReminderRepository.list_due(now) returns ...; the migration is 0002
```

- **A worker's first prompt is this ticket** (without status lines and reviews), then the files changed so far, its instruction, the checks, what is off limits and its limits.
- **A worker that already has a session gets only what is new**: what other workers did since its last turn, then its next instruction. Nothing is sent twice.
- **Agents write to the ticket through their structured answer**, which cw records. They do not edit the file, so two agents can never corrupt it and nothing depends on an agent remembering to update it.
- **Questions go up one level at a time.** A worker returns `status: question`; cw asks the planner in the planner's own session, where the plan still is; what the planner cannot answer goes to you (`needs_input`), and your reply is delivered to that worker in its session. A direct ticket has no planner, so its worker asks you. During a fix (failing checks, review findings, conflicts) workers decide for themselves. `max_question_rounds` caps the rounds per instruction; after that the worker is told to choose the simplest option and say what it assumed.
- **The reviewer** gets the request, the plan, the workers' notes (marked as claims to verify), how the checks ended and the diff.

`backend/src/cw/prompts.py` is the one place that builds these prompts.

### What cw checks instead of trusting, and what it remembers

- **git decides what a worker changed.** cw snapshots the tree before and after every worker run; the ticket's `Files:` line is git's list, not the worker's. A worker that says `done` but changed nothing gets a `No changes:` warning, and a task that changed files outside its planned list, and not named in its instructions either, gets a `Scope: S<n>:` warning (one per task).
- **The checks run after every task**, so a broken step is fixed before the next task builds on it.
- **Worker sessions have a size limit.** When a run's average turn passes 45k tokens (cached and uncached together), that worker starts a fresh session next time, seeded from the ticket.
- **Older tasks are shortened in a worker's view**: their summary is dropped, but their files and handoff stay, because later tasks build on them. The ticket's `.md` keeps everything.
- **The planner hands over what it read**: a `context_pack` of 5–15 `path:lines` with the reason, shown to every new worker session so it starts reading in the right place.
- **The planner gets a repo map**: `~/.cw/projects/<project>/repo_map.txt`, made without an LLM from `git ls-files` (so `.gitignore` is respected), at most 250 lines. When the full outline is too long it falls back to one line per file, then one line per folder, so no folder is dropped. It is refreshed at every ticket start and by `cw project map <name>`.
- **The project's `CLAUDE.md` is its memory across tickets.** Claude Code loads it into every session in the project, cw's agents included (checked on 2026-11-13 with cw's own isolation flags). Every worker must return `project_notes` (an answer without the field is rejected): lasting facts such as a command, where a kind of code lives, or a decision. cw adds new ones, without duplicates, to the section `## Notes kept by cw` of that file. The change is part of the ticket's diff, so the reviewer sees it and it is committed with the code; past 40 notes the ticket warns you to prune the section.

---

## 7. Skills and how the rules reach an agent

| Skill | Plugin | What it is | Reaches cw agents |
|---|---|---|---|
| `engineering` | core | The team's language-independent rules: generic configuration, no over-engineering, how to work, naming | pinned for every agent (`pin: true`) |
| `python-rules` | python | An index of `topics/*.md`, which are the `nexttech-claude-kit` rules copied with only the reviewers' and people's names removed: `core-rules`, `python-style`, `python-stack`, `service-architecture`, `configuration`, `errors-and-logging`, `data-and-migrations`, `agents`, `git-and-pr` and `pr-review-procedure` (the kit's `PR_review.md`) | the index is pinned; `core-rules` and `python-style` always; the other topics by hook or for a review (below) |
| `pr-review` | core | How to review: what to check in which order, apply every rule strictly, name the rule each finding breaks | pinned for the reviewer |
| `ponytail` | core | The laziest solution that works: the ladder to run before any code. One of the most important skills for writing code | pinned for planner, worker and reviewer |
| `rule-precedence` | core | Which rule wins when two disagree, and what the Nexttech rules mean inside a cw run (cw commits, no skills, no GitHub or Linear, cw's answer format) | pinned for planner, worker and reviewer |
| `clean-code` | core | [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills), MIT licence kept | interactive sessions only |
| `bootstrap-project` | core | The command `/core:bootstrap-project` | interactive sessions only |

The Python rulebook used to be one 500-line file that, after the first token fixes, was no longer pinned and could not be loaded either (no agent has the Skill tool), so agents were told to follow rules they never saw. Since 2026-11-16 the Python rules are the `nexttech-claude-kit` skills, the conventions from the internship's code reviews, copied into `topics/` with the reviewers' names removed (they replaced an older rulebook that still prescribed `src.` imports and `AppException`). Each rule arrives when it applies:

1. **Pinned** (about 390 lines, about 9k tokens, cached after a session's first turn): `engineering`, `ponytail`, `rule-precedence`, the `python-rules` index, and the topics `topics.json` lists under `always`: `core-rules` and `python-style`.
2. **By topic, when a file is touched.** `topics/topics.json` maps file names and folders to topics. The `rules_on_touch.py` hook shows a topic once per session, the first time the agent reads or edits a matching file. Before an edit the hook stops the edit so the rules are read first.
3. **For a review.** cw knows which files changed, so it pins exactly the topics that cover them, plus `git-and-pr` and `pr-review-procedure`, into the reviewer's system prompt (about 20k tokens for a change that touches a route).
4. **For a new project.** While the repository has no code, every agent also gets `python-stack` and `service-architecture`.
5. **Mechanically.** What a script can check is a hook, not advice (section 8).

**Precedence, highest first:** the project's `CLAUDE.md` and your project-map decisions; the Nexttech rules for the stack, then `engineering`; ponytail; clean-code. The kit's files are edited in the kit, then copied again with the reviewers' names removed. `ponytail` ranks third only where the Nexttech rules say otherwise (comments, tests); otherwise it is the first thing an agent applies when it writes code.

---

## 8. Hooks

| Hook | Event | What it does |
|---|---|---|
| `core/scripts/guard.py` | `PreToolUse` on Bash | Reads the command the way a shell splits it. **Always:** blocks force pushes, `rm -rf /` or `~`, `git reset --hard origin…` and `--no-verify`. **In cw runs only:** also blocks `git commit/push/merge/rebase/checkout/switch/reset/stash/worktree/restore/clean` and `branch -d`, also behind `git -C …` or `sh -c "…"`, and commands that name a protected path. A guard rail, not a wall: the permission rules below are the wall. |
| `python/scripts/rules_on_touch.py` | `PreToolUse` on Edit/Write, `PostToolUse` on Read | Shows the team's rules for the area of the file, once per session (section 7). |
| `python/scripts/ruff_on_edit.py` | `PostToolUse` on Edit/Write | Formats and lints the edited `.py` file with the project's own ruff (or `uvx ruff`). A file that was unformatted or had lint errors in the last commit is left as it is, so the agent is never sent to fix problems it did not cause. |

---

## 9. Permissions, safety, external plugins and MCP

- **Protected paths** (default `**/*.key`, `**/*.pem`, `**/secrets/**`) become `Read/Edit/Write` deny rules in every run, plus the guard hook for shell commands. `.env` is not on the list: agents may open and update it when a task adds or changes a setting. Whatever is in it is then sent to the model and kept in the run's transcript, so keep production secrets out of it, or add `.env` to a project's protected paths.
- **Worker shell commands.** The worker runs with `acceptEdits`. Bash is pre-allowed only for the project's lint and test commands (also narrowed: cw drops the command's trailing folder arguments, so `ruff check src tests` also allows `ruff check <any path>`; a trailing file, which may be the script itself, is never dropped), `head` and `tail` for piping their output, read-only git and `extra_allowed_bash`; anything else is denied in headless mode. The worker's prompt says so, and the planner writes `Done when:` with only those commands.
- **Read-only agents.** Planner, reviewer and onboarder have no write tools.
- **Secrets.** The Bitbucket token comes only from the environment and is removed from the environment agents run in.
- **Your work is protected.** cw refuses to start or switch tickets when your checkout has uncommitted changes, and **Abandon** is only offered for that ticket's own branch.
- **Two human checkpoints:** you approve plans, and you merge PRs. cw merges only when you run `cw merge`, never by itself.

| External plugin | Marketplace | Why |
|---|---|---|
| `ponytail@ponytail` | DietrichGebert/ponytail | Laziest correct solution; enabled for every project |
| `pyright-lsp@claude-plugins-official` | anthropics/claude-plugins-official | Python language server for code navigation; needs `pyright` installed |

**Isolation:** cw runs ignore your personal MCP servers and plugins (`--strict-mcp-config`, `--setting-sources project,local`). cw agents also cannot load skills on demand, by design: the rules they need are pinned or shown by hook, so nothing depends on the model deciding to look. Adding a team MCP server means enabling its plugin for a stack **and** dropping `--strict-mcp-config` for the agents that need it; every server costs context on every turn, so it isn't built.

---

## 10. Anatomy of one agent run

`backend/src/cw/claude_runner.py` runs:

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
  --strict-mcp-config --setting-sources project,local      ← isolation from personal setup
  --plugin-dir <toolkit>/plugins/core --plugin-dir <toolkit>/plugins/<stack>
  [--resume <session>]                the planner's, or the worker's, own earlier session
  < the prompt on stdin
cwd = the project checkout   env = yours without cw's secrets + project env + CW_RUN=1 + CW_PROTECTED_PATHS + CW_SHOWN_TOPICS
```

- cw reads the stream line by line and logs every tool call (`worker · Edit src/app/routes/items.py`), which is what you see live in the terminal and in FRIDAY.
- The last `result` line carries the structured answer and the usage numbers. The tokens of a failed run are recorded too.
- The process runs in its own process group with a time limit (`agent_timeout_minutes`). Ctrl+C, FRIDAY's Stop and the time limit kill the agent and everything it started.
- A session that can no longer be resumed is replaced by a new one that gets the whole ticket.
- An error with HTTP 429 or "usage/rate limit" in its text makes the ticket **paused**; any other failure makes it **blocked**.

---

## 11. Tickets and history

- One JSON file and one markdown file per ticket in `~/.cw/tickets/<project>/`. Nothing is ever written into the repository.
- A ticket holds: status, branch, the branch it started from and goes back into, every prompt, questions and answers, plan, tasks with their worker, what each worker pass left behind, reviews, warnings, PR, and every run's token usage and `session_id`.
- **History:** closed tickets stay there. FRIDAY lists them under History; `cw show <id>` prints the markdown.
- **Agent transcripts:** Claude Code keeps its own session logs, so `claude --resume <session_id>` in the project reopens any run.

---

## 12. Configuration (`~/.cw/config.json`)

Validated on every run; unknown keys are rejected. FRIDAY saves through `cw config set`, so the same validation applies. `cw init` writes it from `backend/src/cw/config.example.json`.

| Key | Meaning |
|---|---|
| `toolkit` | local path, marketplace name, GitHub repo, always-on plugins |
| `external_marketplaces` | marketplace name → GitHub repository |
| `stacks.<name>` | marker files (detection), toolkit plugins, extra pinned skills, external plugins, starting `commands` (lint, test) |
| `default_protected_paths` | copied into each new project |
| `defaults` / `projects[].profile` | model per agent, `worker_permission_mode`, `reviewers`, and `limits` |
| `limits` | `max_review_rounds`, `max_fix_rounds`, `max_conflict_rounds`, `max_question_rounds` (all 1), `max_pull_request_fix_rounds` (3), `max_workers` (4), `escalate_file_threshold` (5), `summary_max_lines`, `max_diff_chars`, `max_check_output_chars`, `agent_timeout_minutes` (45), `check_timeout_minutes` (15), `max_turns` per agent (worker 40) |
| `projects[]` | name, path, base branch, remote, stack, git host, lint and test commands, protected paths, extra allowed shell prefixes, environment, `compose_file` (the containers cw starts; empty for none) |

`max_workers`, `agent_timeout_minutes` and `check_timeout_minutes` have defaults, so a config written before they existed still loads. Which skills are pinned is decided in the toolkit (`pin: true` in a skill's front matter), not here, so the whole team gets the same rules. Secrets come only from the environment (`BITBUCKET__EMAIL`, `BITBUCKET__API_TOKEN`). `CW_HOME` moves the whole `~/.cw` folder.

---

## 13. Why the headless CLI and not the Agent SDK

The Agent SDK docs say:

> "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK."

Everyone on the team uses their own subscription, so cw runs the official CLI headless. Nothing is lost: the SDK itself runs the Claude Code binary and reads the same `stream-json` lines cw reads. If the team moves to API keys, `claude_runner.py` is the only file that would change.

---

## 14. Token efficiency

Measured on 2026-11-11, before the worker sessions and the pinned rules of 2026-11-13:

| Measurement | Before | After the first fixes |
|---|---|---|
| Context of a run that only says "hi" | 36.9k tokens | 5.9k (no personal MCP or plugins, explicit tool list) |
| Worker context per turn (real runs) | 35–80k | 7.8–11.9k |
| Planner context per turn | ~19k | ~4.4k |
| Agent runs for the "two small features" prompt | 1 planner + 17 workers + 2 reviewers | split into 2 tickets, about 1 worker + 1 reviewer each |
| Commits per ticket | 14 | 1 |

What the 2026-11-13 changes do to these numbers has **not been measured yet**:
- The pinned rules add roughly 390 lines (about 9k tokens) to every agent's system prompt since the kit's rules replaced the old ones. They are cached, so after a session's first turn they cost about a tenth.
- A worker session is reused for related tasks, follow-ups, fixes and answers, instead of a new session that re-reads the project each time. A reused session grows with every task, which is why the planner gives a worker only related tasks.
- A resumed worker is sent only what is new.

Measure it on your own tickets with `cw stats` and `cw show <id> --json` (`runs`: tokens and turns per run). A run's `cost_usd` is what Claude Code reports, which for a resumed session is the session's total so far; `cw stats` subtracts the session's previous run, so each run is counted once.

Where the savings come from: isolation from personal setup; no pasted rulebooks; no router agent; deterministic checks before any reviewer; failures that already exist on the base are ignored; one reviewer, re-reviewing only the new diff; caps on every loop; workers that take turns and keep their session.

---

## 15. FRIDAY

| Area | What it does |
|---|---|
| Top bar | Current project, **"N waiting for you"**, Dashboard/Usage, refresh |
| Rail | Projects, Activity (every command FRIDAY ran, with live output and **Stop**), Toolkit, Settings |
| Projects panel | Each codebase with branch, clean/dirty, last commit and remote; **Add** registers repos; the sliders button opens project **Settings** and the optional **Project map** |
| Files | The project's tree; files changed on the ticket's branch are coloured; click a file to open a read-only tab |
| Center | File tabs, or **Tickets**: Open tickets and **History** |
| Chat | **New ticket in X** or **Replying to this ticket**. A **next-step card** shows the state in plain words, where the code is, warnings, and the button that matters (Start N tickets · Approve plan · Review & open PR · Review only · Resume · Mark done). Under it, the ticket as a conversation, with each task's worker and files. |
| Voice | The microphone under the chat box (⇧⌘D) dictates into the box with the Mac's own speech recognition; with **Speak replies** on, FRIDAY says where a ticket stands when a step ends. Works when FRIDAY runs as the app. |
| Settings | Models and turn caps per agent, loop caps, workers, time limits, reviewers, permission mode, protected paths, allowed shell prefixes, environment; **Chat, voice and look** (text size, live output lines, plan first by default, speak replies, voice language, accent colour) |
| Setup | First launch without a config: install cw, create the config |

`cw new` and `--base` are terminal only for now.

---

## 16. Verified, and not verified

**Verified by tests that run the real cw code with a stand-in for `claude`** (`cd backend && uv run pytest`, 88 tests):
- a project created from an idea, planned, approved, built in two tasks, reviewed, committed once and merged by `cw done`, through the command line;
- related tasks continue one worker session, another worker gets a new one and is told what happened;
- a worker's question is answered by the planner; one the planner cannot answer goes to you and your reply reaches the worker;
- a lost session is replaced by a fresh one with the whole ticket;
- Ctrl+C in the middle of a task list leaves the ticket blocked, and resume continues at the next task; a ticket left "in progress" by a killed process resumes too;
- merge conflicts with the base are resolved and committed; a later review does not include the base's changes;
- the PR is opened after the review even when findings are left; `cw review` commits nothing; a ticket from `--base develop` targets `develop`;
- a check the ticket broke gets one fix round and is then reported, and in a task list a check still broken after that round stops the ticket, which resumes at that task; cw starts a project's containers before the worker and the checks once its compose file exists, and stops the ticket when they do not start; `cw fix` goes round again while the new review leaves comments, up to its cap, and stops after a round that changed nothing; a ticket without changes cannot open a PR; files the checks leave behind are reported;
- the guard, the rules hook and the topic index; time limits kill a command and everything it started;
- `cw fix` sends the open PR threads to the last worker, pushes a second commit, posts the worker's summary and resolves the threads; with no open threads it runs no agent;
- `cw merge` refuses while a blocking finding is left or CI has not passed, and otherwise merges, fast-forwards the local base branch, closes the ticket and deletes the local branch;
- the review's fix round gets every finding, minor ones included; `cw stats` counts a resumed session's cost once; a file named in a task's instructions is in its scope; the worker may narrow the lint and test commands to a path but not run another script;
- the reviewer is told about cw's notes only when the diff contains them.

**Verified on real projects with the real Claude Code (2.1.311) and GitHub, on 2026-11-16** (`CHANGES_AND_ISSUES.md` has each run):
- three tickets: a one-file test on an existing repository (direct route), a 2-task, 11-file change to the same repository (planned route), and a project created from nothing with `cw new --github private` (6 tasks, 4 workers, 29 files);
- real agent runs with `--max-turns`, `stream-json`, `--json-schema` and resumed worker and planner sessions, including plan feedback through `cw run <ticket>`;
- the pull request opened and the review posted on GitHub; `cw fix` read the open threads, pushed the fix and resolved them; `cw merge` squash-merged once CI passed, deleted the branch on GitHub and fast-forwarded the local `main`;
- the resulting tic-tac-toe game was played by hand in every mode.

**Not verified:**
- that the rules hook's message reaches the agent as intended when it reads a file (exit code 2 after a Read);
- FRIDAY: it compiles (`swift build`), but its screens, dictation, speech and Stop were not used by hand after these changes;
- all of the Bitbucket side: PRs, reviews, comments, CI and merging. Resolving a comment is `POST …/comments/{id}/resolve`, taken from Atlassian's community answers, not from a test;
- usage-limit detection;
- the links in section 17 that start with `anthropic.com/research/` may have moved to `anthropic.com/engineering/`.

**Known bugs, not fixed yet** (from the tic-tac-toe ticket):
- cw's snapshots stage every file in git's real index, so a file the worker later adds to `.gitignore` is still committed (the worker cannot run `git rm --cached`). Check the PR's file list for IDE folders and other stray files;
- `cw review` on a ticket whose PR is open sets it back to `ready`, and `cw merge` then refuses. Run `cw pr` again, then `cw merge`;
- a planned ticket's commit and squash-merge message is the first task's message (for example "chore: add project skeleton"), not one for the whole change. Edit it on the host if it matters.

**Known limits:** one open ticket per project; no Java stack plugin; the toolkit isn't published.

---

## 17. Further reading

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
- Liu et al., *Lost in the Middle* ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)): long contexts are used less reliably, another reason for small hand-offs.

**Reference docs**
- Claude Code: [subagents](https://code.claude.com/docs/en/sub-agents), [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview), [headless mode](https://code.claude.com/docs/en/headless), plus the pages on hooks, plugins, plugin marketplaces, settings and permissions, skills and MCP.
- [anthropics/skills](https://github.com/anthropics/skills): Anthropic's own skills. Its README gives `/plugin marketplace add anthropics/skills`, then `/plugin install document-skills@anthropic-agent-skills` and `/plugin install example-skills@anthropic-agent-skills`. For interactive sessions; cw runs do not load them.
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official), [obra/superpowers](https://github.com/obra/superpowers), [wshobson/agents](https://github.com/wshobson/agents), [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code): public marketplaces to borrow from.
- Stack: [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [uv](https://docs.astral.sh/uv/), [git switch](https://git-scm.com/docs/git-switch), [SwiftUI](https://developer.apple.com/documentation/swiftui), [Speech](https://developer.apple.com/documentation/speech), [GitHub CLI](https://cli.github.com/manual/), [Bitbucket Cloud REST API](https://developer.atlassian.com/cloud/bitbucket/rest/).
