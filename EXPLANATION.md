# How this project works

The current state of the project: the concepts it builds on, the architecture, the workflow, every agent, skill and hook, how one agent run is assembled, the configuration, what was measured, what is still unverified, and further reading. The history of why things changed is in [CHANGES_AND_ISSUES.md](CHANGES_AND_ISSUES.md); setup is in [FIRST_RUN.md](FIRST_RUN.md).

---

## 1. The idea

A team works on several projects with Claude Code. The goals:

1. **Centralize.** One shared toolkit of agents, skills, rules, hooks and MCP servers, so every teammate and every project works the same way and the code looks the same.
2. **Simple and cheap.** Each job goes to the right model, with only the context it needs, in a fixed and predictable flow.
3. **Keep quality.** Deterministic checks, a reviewer before every PR, and a human approving plans and merging PRs.

The repository has three parts:

| Part | What it is |
|---|---|
| `toolkit/` | A Claude Code **plugin marketplace**: the team's shared agents, skills, rules and hooks. Push it to GitHub. |
| `backend/` → `cw` | A small Python CLI that runs the toolkit's agents on **tickets**, one branch per ticket, in the project's own checkout. |
| `friday/` → FRIDAY | A macOS SwiftUI app: projects, file tree, code viewer and a chat. It runs `cw` and shows its JSON. |

There is no server, database, daemon or custom agent framework. Claude Code does the agent work; cw only decides which agent runs, with what context, and what happens next.

---

## 2. Claude Code concepts used here

| Concept | What it is | Where it appears |
|---|---|---|
| **Session / context window** | One conversation with Claude. Everything in it is re-sent on every turn, so it costs tokens on every turn. | Each cw agent run is a fresh, small session. |
| **`/compact` vs `/clear`** | `/compact` summarises the conversation and continues; `/clear` starts empty. Use `/clear` between unrelated tasks. | cw never needs either: every run starts clean. |
| **CLAUDE.md** | Instructions loaded into every session in that folder. | Your projects' own `CLAUDE.md` files are respected; `/core:bootstrap-project` writes one. |
| **Skill** | A folder with `SKILL.md`. Only its description is in context until it's needed; then the full text loads. | `python-rules`, `clean-code`, `rule-precedence`, `bootstrap-project`. |
| **Command** | A prompt you invoke with `/name`. A skill with `disable-model-invocation: true` is a command only you can trigger. | `/core:bootstrap-project`, an extended `/init` that also creates the remote repository and pushes. |
| **Agent (subagent)** | A markdown file whose front matter sets `name`, `description`, `tools` and `model`, with the instructions in the body. `claude --agent <name>` runs a whole session as that agent. | `planner`, `worker`, `reviewer`, `onboarder`. |
| **Hook** | A command Claude Code runs on an event (`PreToolUse`, `PostToolUse`, …). Exit code 2 blocks the action and shows the message to Claude. It runs every time, unlike a skill, which is only advice. | `guard.py`, `ruff_on_edit.py`. |
| **MCP server** | An external tool server (GitHub, Jira, docs, databases). Each server's tool definitions cost context on every turn. | None bundled yet (section 9). |
| **Plugin / marketplace** | A plugin bundles agents, skills, commands, hooks and MCP/LSP servers. A marketplace is a git repository listing plugins; a project's `.claude/settings.json` enables them for everyone. | `toolkit/` with the `core` and `python` plugins. |
| **Headless mode** | `claude -p` runs one prompt without the interactive UI; `--output-format json` returns the result and token usage; `--json-schema` forces a structured answer. | Every cw agent run. |
| **Agent SDK** | A Python/TypeScript library that runs the Claude Code binary from your own code. | Not used, because of subscription login (section 13). |
| **Agent SDK vs hooks** | The SDK is how *your code drives Claude*; hooks are how *Claude Code calls your code* on an event. | cw drives Claude through the CLI; the toolkit's hooks run inside every session. |

---

## 3. Architecture and what is centralized

```
            toolkit (one git repo on GitHub, shared by the team)
            ├─ core:   agents planner · worker · reviewer · onboarder
            │          skills clean-code · rule-precedence · /core:bootstrap-project
            │          hook guard.py (blocks dangerous shell commands)
            └─ python: skill python-rules · hook ruff_on_edit.py
                 ▲ loaded into every cw run (--plugin-dir)        ▲ installed by each teammate's Claude Code
                 │                                                │ when a repo is wired (.claude/settings.json)
FRIDAY ──runs──► cw ──claude -p --agent …──► Claude Code ──edits──► the project checkout, on branch cw/<ticket>
  ▲ reads --json  │
  └───────────────┤ ~/.cw/config.json                       projects, models, limits, stacks
                  ├ ~/.cw/tickets/<project>/<id>.json        tickets = history (no database)
                  ├ ~/.cw/projects/<project>/conventions.md  optional project map and decisions
                  └ gh / Bitbucket REST ──► pull request + review comments
```

**What the team shares through the toolkit:**
- the four agents and their instructions;
- the coding rules (`python-rules`, `clean-code`, and which wins when they disagree);
- the safety and formatting hooks;
- the `/core:bootstrap-project` command;
- references to external plugins (ponytail, the Python language server).

Change the toolkit once, push it, and every teammate and every cw run uses the new version.

**What is not centralized yet:**
1. The toolkit isn't pushed to GitHub. Teammates' interactive Claude Code only picks it up after that and after a repository is wired with `cw project wire`.
2. No MCP servers are bundled. Add them to a plugin's `.mcp.json` when the team agrees on one; see section 9.
3. Each person has their own `~/.cw/config.json`, so models and limits are personal. A shared team default would be one more file in the toolkit.

---

## 4. The workflow

```
you: prompt (FRIDAY chat or cw run)
 │
 ├─ checkout must be clean ─► cw: git switch -c cw/<ticket> <base>  ─►  baseline: run lint + tests once
 │
 ├─ default ─► worker (one fresh run) ──"needs a plan / several branches"──┐
 │                                                                         ▼
 └─ Plan first ─────────────────────────────────────────────────► planner
                                                                   ├─ several changes? → "Start N tickets" (one branch + PR each)
                                                                   ├─ questions? → you answer in the chat
                                                                   └─ plan + ordered task list → you approve
                                                                              │
                                                    task 1: fresh worker ─► ✓ ─► task 2: fresh worker ─► ✓ …
 │
 ▼
checks (only those that passed on the base) ─ broke? ─► one fix round, then report
 │
 ▼  READY: changes are uncommitted in your checkout · reply to adjust · or Park
you: Open PR
 │
 ▼
reviewer (read-only) ─ blocking findings? ─► one fix round, re-review only the new diff
 │                       still blocking → READY with a warning: follow up, or "Open PR anyway"
 ▼
cw: one commit (WIP commits folded in) → merge base (conflicts → worker, once) → push → open PR → post review
 │
 ▼
you merge on GitHub/Bitbucket → Mark done → cw switches you back to your previous branch
```

**Key properties:**
- **One branch per ticket, made by cw, in your normal checkout.** Agents never create branches, commit or push; a hook blocks them. You see the changes in your IDE, and your existing `.venv` and `node_modules` work.
- **One ticket per project at a time.** cw refuses to start a second one while the checkout has changes. **Park** saves the work as a WIP commit and switches you back; the WIP is folded into the single commit at PR time. Different projects can run at the same time.
- **One worker at a time.** A planned ticket is a task list; each task gets a fresh worker. A fresh worker doesn't inherit the previous agent's transcript. It receives the request, the plan, the checklist, short summaries of finished tasks and the list of files changed so far, then reads the code itself. That is cheaper than carrying a growing transcript, which would be re-sent on every turn. Resume continues from the first unfinished task.
- **Nothing is committed or pushed until Open PR.** Then exactly one commit is made.
- **Every repeat is capped** by config: fix rounds, review rounds, question rounds and conflict rounds all default to 1, and every agent has a turn cap. Nothing loops by itself; anything more needs you to press a button.

---

## 5. Agents

All live in `toolkit/plugins/core/agents/*.md`. The `model` in each file is only a default for interactive use; **cw always uses the model from the project's config**.

| Agent | Default model | Tools | Job | Answers with |
|---|---|---|---|---|
| planner | opus | Read, Grep, Glob | Turns a bigger request into a plan and an ordered task list; splits multi-branch requests; asks at most 3 questions | questions · split · plan · options considered · tasks |
| worker | sonnet | Read, Edit, Write, Bash, Grep, Glob | Implements one task, one follow-up or one fix, with the smallest change. Never touches tooling it wasn't asked about. Reports environment problems instead of "fixing" them. | done / needs_plan / blocked · summary · commit message |
| reviewer | opus | Read, Grep, Glob | Checks the diff (only the diff) for correctness, security, rules and tests | approve / changes · PR summary · line comments (blocking / minor) |
| onboarder | sonnet | Read, Grep, Glob | **Optional.** Maps a repository: lint/test commands, architecture, tests, differences from the team rules | stack · commands · architecture · tests · differences · warnings |

---

## 6. Skills and rule precedence

| Skill | Plugin | Source | Loaded |
|---|---|---|---|
| `python-rules` | python | the team's Python rulebook (stack, layout, enums, Pydantic DTOs vs ORM, settings, errors, layers, Alembic, agents, git), adapted for agents | when the agent decides it's relevant |
| `clean-code` | core | [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills), MIT licence kept | when relevant |
| `rule-precedence` | core | written here | pinned for planner, worker and reviewer (it's short) |
| `bootstrap-project` | core | written here | only as the command `/core:bootstrap-project` |
| ponytail | external | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | through its own SessionStart hook, where it's enabled |

Rules used to be pasted in full into every run. That cost about 10k tokens per turn, so now only the short precedence skill is pinned.

**Precedence, highest first:**
1. the project's `CLAUDE.md` and your project-map decisions;
2. the stack rules;
3. ponytail;
4. clean-code.

---

## 7. Hooks

| Hook | Event | What it does |
|---|---|---|
| `core/scripts/guard.py` | `PreToolUse` on Bash | **Always:** blocks force pushes, `rm -rf /` or `~`, `git reset --hard origin` and `--no-verify`. **In cw runs only:** also blocks `git commit/push/merge/rebase/checkout/switch/reset/stash/worktree/branch -d`, and commands that name a protected path. It matches text, so it's a guard rail, not a wall. |
| `python/scripts/ruff_on_edit.py` | `PostToolUse` on Edit/Write | Formats and lints the edited `.py` file with the project's own ruff (or `uvx ruff`), and sends remaining errors back to Claude. It never creates a venv or lockfile. |

---

## 8. Permissions and safety

- **Protected paths** (default `.env*`, `**/*.key`, `**/*.pem`, `**/secrets/**`) become `Read/Edit/Write` deny rules in every run, plus the guard hook for shell commands.
- **Worker shell commands.** The worker runs with `acceptEdits`. Bash is pre-allowed only for the project's lint and test commands, read-only git and `extra_allowed_bash`; anything else is denied in headless mode.
- **Read-only agents.** Planner, reviewer and onboarder have no write tools.
- **Your work is protected.** cw refuses to start or switch tickets when your checkout has uncommitted changes, and **Abandon** (which discards a ticket's changes) is only offered for that ticket's own branch.
- **Two human checkpoints:** you approve plans, and you merge PRs. cw never merges.
- **Secrets:** the Bitbucket token comes only from the environment.

---

## 9. External plugins and MCP

| Plugin | Marketplace | Why |
|---|---|---|
| `ponytail@ponytail` | DietrichGebert/ponytail | Laziest correct solution; enabled for every project |
| `pyright-lsp@claude-plugins-official` | anthropics/claude-plugins-official | Python language server for code navigation; needs `pyright` installed |

**Isolation:** cw runs ignore your personal MCP servers and plugins (`--strict-mcp-config`, `--setting-sources project,local`). That is the biggest token saving in the project; see section 14.

**Adding a team MCP server**, for example GitHub, Jira or Context7, all available in Anthropic's official marketplace:
1. enable its plugin for a stack in `config.json`, or add an `.mcp.json` to a toolkit plugin;
2. **and** stop passing `--strict-mcp-config` for the agents that need it.

That step is deliberate, because every MCP server costs context on every turn. It isn't built yet.

---

## 10. Anatomy of one agent run

`backend/src/cw/claude_runner.py` runs:

```
claude -p --output-format json
  --agents <tmp>/agents.json          {name: {description, prompt: <agent file body>, model: <config>,
                                              maxTurns: <config>, tools: [... + StructuredOutput]}}
  --agent <name>                      the whole session is that agent
  --settings <tmp>/settings.json      allow/deny rules + which plugins are enabled
  --append-system-prompt-file <tmp>   pinned skills (rule-precedence) + the project's decisions, if any
  --json-schema '<pydantic schema>'   forces the structured answer
  --permission-mode acceptEdits|default
  --strict-mcp-config --setting-sources project,local      ← isolation from personal setup
  --plugin-dir <toolkit>/plugins/core --plugin-dir <toolkit>/plugins/<stack>
  [--resume <planner session>]        only when the planner continues after your answer
  < task prompt on stdin
cwd = the project checkout   env = yours + project env + CW_RUN=1 + CW_PROTECTED_PATHS
```

Verified on Claude Code 2.1.285:
- `--agents` with `--agent` uses the model from the JSON;
- `structured_output` is returned only when `StructuredOutput` is in the agent's tool list (cw adds it);
- `--append-system-prompt-file` works together with `--agent`;
- the result includes the usage numbers.

An error with HTTP 429 or "usage/rate limit" in its text makes the ticket **paused**; any other failure makes it **blocked**.

---

## 11. Tickets and history

- One JSON file per ticket in `~/.cw/tickets/<project>/<id>.json`. Nothing is ever written into the repository.
- A ticket holds:
  - status, branch, base commit and the branch you came from;
  - every prompt, questions, plan, tasks and which are done;
  - worker summaries, reviews, warnings, PR, push time;
  - every run's token usage.
- **History:** closed tickets stay there. FRIDAY lists them under History and opens them as conversations, and `cw show <id>` prints one in the terminal.
- **Agent transcripts:** Claude Code keeps its own session logs. A ticket's runs record their `session_id`, so `claude --resume <id>` in the project reopens one.

---

## 12. Configuration (`~/.cw/config.json`)

Validated on every run; unknown keys are rejected. FRIDAY saves through `cw config set`, so the same validation applies. `cw init` writes it from `backend/src/cw/config.example.json`.

| Key | Meaning |
|---|---|
| `toolkit` | local clone path, marketplace name, GitHub repo, always-on plugins |
| `external_marketplaces` | marketplace name → GitHub repository |
| `stacks.<name>` | marker files (detection), toolkit plugins, pinned skills, external plugins, starting `commands` (lint, test) |
| `default_protected_paths` | copied into each new project |
| `defaults` / `projects[].profile` | model per agent, `worker_permission_mode`, `reviewers`, and `limits` |
| `limits` | `max_review_rounds`, `max_fix_rounds`, `max_conflict_rounds`, `max_question_rounds` (all 1), `escalate_file_threshold` (5), `summary_max_lines`, `max_diff_chars`, `max_check_output_chars`, `max_turns` per agent (worker 40) |
| `projects[]` | name, path, base branch, remote, stack, git host (`github` / `bitbucket`), lint and test commands, protected paths, extra allowed shell prefixes, environment |

Secrets come only from the environment (`BITBUCKET__EMAIL`, `BITBUCKET__API_TOKEN`). `CW_HOME` moves the whole `~/.cw` folder.

---

## 13. Why the headless CLI and not the Agent SDK

The Agent SDK docs say:

> "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK."

Everyone on the team uses their own subscription, so cw runs the official CLI headless, which the same docs recommend for driving the agent loop from your own process. Nothing is lost: the SDK itself "runs the Claude Code binary", and everything cw needs is available as CLI flags (agents, plugins, hooks, permissions, sessions, structured output, usage). If the team moves to API keys, `claude_runner.py` is the only file that would change.

The video you pointed to ("You Can Build The Craziest Things with Claude's Agent SDK") couldn't be read from this environment beyond its title, so it hasn't been compared in detail.

---

## 14. Token efficiency: what was measured

| Measurement | Before | Now |
|---|---|---|
| Context of a run that only says "hi" | 36.9k tokens | 5.9k (no personal MCP or plugins, explicit tool list) |
| Worker context per turn (real runs) | 35–80k | 7.8–11.9k |
| Planner context per turn | ~19k | ~4.4k |
| Agent runs for the ComIntern "two small features" prompt | 1 planner + 17 workers + 2 reviewers | split into 2 tickets, about 1 worker + 1 reviewer each, plus 1 fix run if something breaks |
| Commits per ticket | 14 | 1 |

Where the savings come from:
1. isolation from personal setup;
2. no pasted rulebooks;
3. no router agent;
4. deterministic checks before any reviewer;
5. failures that already exist on `main` are ignored;
6. one reviewer per ticket, re-reviewing only the new diff;
7. caps on every loop;
8. one worker at a time;
9. fresh workers instead of growing transcripts.

`cw stats` and FRIDAY's Usage page sum every run's tokens per project and agent.

---

## 15. FRIDAY

| Area | What it does |
|---|---|
| Top bar | Current project, **"N waiting for you"** (jumps to the next ticket that needs you), Dashboard/Usage, refresh |
| Rail | Projects, Activity (every command FRIDAY ran, with live output), Toolkit, Settings |
| Projects panel | Each codebase with branch, clean/dirty, last commit and remote; **Add** registers repos (no agent runs); the sliders button opens project **Settings** and the optional **Project map** |
| Files | The project's tree; files changed on the ticket's branch are coloured; click a file to open a read-only tab |
| Center | File tabs, or **Tickets**: Open tickets, the 4-step guide when there are none, and **History** |
| Chat | Header says **New ticket in X** or **Replying to this ticket**. A **next-step card** shows the state in plain words, whether the code is local or pushed (or parked), warnings, a split list, and the button that matters (Start N tickets · Approve plan · Review & open PR · Open PR anyway · Resume · Mark done). Under it, the ticket as a conversation; the task list has checkmarks. |
| Settings | Models and turn caps per agent, loop caps, reviewers, permission mode, protected paths, allowed shell prefixes, environment; **Apply to all projects**; global defaults; toolkit location |
| Setup | First launch without a config: install cw, create the config |

---

## 16. Verified, and not verified

**Verified by running** (throwaway repo, Haiku):
- a two-task plan ran as two fresh workers one after another in the checkout, with 0 commits while working;
- cw refused a second ticket while the first had changes;
- **Park** saved the work and switched back to `main`, clean;
- a second ticket's **Open PR** made exactly **1 commit** and pushed it, then stopped at `gh` as expected (the remote is a local folder);
- **Done** switched back; **Abandon** discarded and deleted the branch;
- a multi-branch prompt was split into 2 tickets;
- a lint check that already failed on the base was reported, not enforced.

Backend lint and tests pass; `claude plugin validate` passes on the toolkit; FRIDAY builds and decodes every `cw --json` output.

**Not verified yet:**
- creating a real PR and posting the review on GitHub or Bitbucket;
- that `maxTurns` is enforced when the agent is the main session;
- usage-limit detection;
- FRIDAY's look (not checked visually).

**Known limits:**
- one open ticket per project;
- tokens of a failed run aren't recorded;
- no Java stack plugin yet;
- the toolkit isn't published yet.

---

## 17. Further reading

**Agentic workflows and design**
- Anthropic, [Building effective agents](https://www.anthropic.com/research/building-effective-agents) (Dec 2024): workflows vs agents, the five patterns (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer), and "find the simplest solution possible". The basis for cw's fixed pipeline.
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system): agents use about 4× the tokens of chat and multi-agent systems about 15×; "most coding tasks involve fewer truly parallelizable tasks than research". The reason parallel workers were dropped.
- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents): keeping context small and relevant, the idea behind fresh workers with short hand-offs.
- Anthropic, [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) and [Harness design for long-running application development](https://www.anthropic.com/research/harness-design-long-running-apps): task lists and progress files instead of one long session.
- Anthropic, [Writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents) and [Code execution with MCP](https://www.anthropic.com/research/code-execution-with-mcp): why tool definitions cost context.
- Anthropic, [Claude Code: Best practices for agentic coding](https://www.anthropic.com/research/claude-code-best-practices).
- Anthropic, [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills): progressive disclosure of skills.
- Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents): how to measure whether a workflow change helped.

**Papers**
- Xia et al., *Agentless: Demystifying LLM-based Software Engineering Agents* ([arXiv:2407.01489](https://arxiv.org/abs/2407.01489), FSE 2025): a fixed localize → repair → validate pipeline matched or beat complex agents on SWE-bench Lite at about $0.34 per issue. The strongest evidence for a simple pipeline.
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): 14 failure modes, mostly coordination and organisation rather than the individual agents.
- Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* ([arXiv:2210.03629](https://arxiv.org/abs/2210.03629)): the reason → act → observe loop inside every agent run.
- Madaan et al., *Self-Refine* ([arXiv:2303.17651](https://arxiv.org/abs/2303.17651)) and Shinn et al., *Reflexion* ([arXiv:2303.11366](https://arxiv.org/abs/2303.11366)): feedback loops, the idea behind the reviewer → worker fix round, and why it's capped.
- Jimenez et al., *SWE-bench* ([arXiv:2310.06770](https://arxiv.org/abs/2310.06770)) and Yang et al., *SWE-agent* ([arXiv:2405.15793](https://arxiv.org/abs/2405.15793)): how coding agents are evaluated, and how the agent–computer interface matters.
- Liu et al., *Lost in the Middle* ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)): long contexts are used less reliably, another reason for small hand-offs.

**Reference docs**
- Claude Code: [subagents](https://code.claude.com/docs/en/sub-agents), [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview), [headless mode](https://code.claude.com/docs/en/headless), plus the Claude Code docs pages on hooks, plugins, plugin marketplaces, settings and permissions, skills and MCP.
- [Model Context Protocol](https://modelcontextprotocol.io): the MCP specification.
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official), [obra/superpowers](https://github.com/obra/superpowers), [wshobson/agents](https://github.com/wshobson/agents), [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code): public marketplaces to borrow from.
- Stack: [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [uv](https://docs.astral.sh/uv/), [git switch](https://git-scm.com/docs/git-switch), [SwiftUI](https://developer.apple.com/documentation/swiftui), [GitHub CLI](https://cli.github.com/manual/), [Bitbucket Cloud REST API](https://developer.atlassian.com/cloud/bitbucket/rest/).
