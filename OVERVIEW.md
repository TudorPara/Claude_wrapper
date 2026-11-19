# cw + FRIDAY: overview

A small team workflow around Claude Code. A developer describes a change in one sentence; AI agents plan it, write it, test it and review it; the developer approves the plan and the merge. Each developer uses their own Claude subscription, so there are no API keys and no extra billing.

## The three parts

| Part | What it is |
|---|---|
| **Toolkit** | The team's shared agents, coding rules and safety hooks, packaged as a Claude Code plugin. Every agent follows the same rules on every project. |
| **cw** | A command-line tool that runs the agents on a "ticket" and does all the git work. |
| **FRIDAY** | A macOS app over cw: every project, ticket and agent (planner, workers, reviewer) on screen, with the same commands as buttons. |

There is no server, database or custom agent framework. Claude Code does the agent work; cw only decides which agent runs, with what context, and what happens next.

## The flow

```
cw run <project> "<request>" [--plan]
   │
   ├─ planner (optional, for bigger work) ── writes a plan of ordered tasks
   │       you read it, give feedback, then:  cw approve
   │
   ├─ workers (up to 4, taking turns in one checkout) ── one task each
   │       after each task cw runs the project's lint and tests
   │
cw pr
   ├─ reviewer (always) ── checks the diff against the team rules
   │       blocking findings go back to the worker, up to two fix rounds
   ├─ cw commits once, pushes and opens the pull request with the review on it
   │
cw fix      the worker addresses the PR's comments (the reviewer's and colleagues') and the open
            blocking findings, round after round while blocking findings stay open (at most 3)
cw merge    squash-merges, only once CI has passed and no blocking finding is left
```

There are two human checkpoints: approving the plan and merging. Nothing reaches `main` without them.

| Agent | Model | Can do |
|---|---|---|
| Planner | Opus | Read the code; write the plan |
| Worker | Sonnet | Edit files; run only the project's lint and tests, and read-only git |
| Reviewer | Opus | Read the code; judge the diff against the rules |

## How agents communicate

Agents never talk to each other. Everything goes through the **ticket**, a file cw keeps outside the repository (`~/.cw/tickets/<project>/<id>.json`, with a readable `.md` twin).

- **cw writes every prompt from the ticket**, choosing what each agent needs: the planner gets the request and a map of the repository; a worker gets the plan, its task and what earlier workers left; the reviewer gets the request, the plan, the workers' notes (marked as claims to verify) and the diff.
- **Agents answer in a fixed JSON format**, enforced by Claude Code, and cw records the answer in the ticket. Agents never edit the ticket themselves, so it can't be corrupted or forgotten.
- **Each finished task leaves a short hand-off:** how it was solved, the files git saw change, and a note for the next worker.
- **A worker resumes its own session** for related tasks and is sent only what is new, so nothing is sent twice.
- **Questions go up one level at a time:** worker → planner (in the planner's own session) → you, only when the planner can't answer.
- **Shared state besides the ticket:** the code in the checkout (workers see each other's changes, and the reviewer sees all of it in the diff) and the project's `CLAUDE.md`. That file is the memory between tickets. cw adds lasting facts each worker must report, and the change is reviewed and committed with the code.

## Caps on every loop

Every loop has a limit in the config, so a ticket can't run away in time or cost. These are the defaults, adjustable per project.

| Limit | Default |
|---|---|
| Fix rounds after the review leaves blocking findings | 2, each followed by a re-review of the new diff |
| `cw fix` rounds while blocking findings stay open | 3 |
| A task's checks still failing after its fix round | the ticket stops |
| Fix rounds for checks the ticket broke | 2 |
| Merge-conflict rounds | 1 |
| Question rounds per instruction | 1 (after that the worker picks the simplest option and says what it assumed) |
| Workers per ticket | 4, taking turns, never in parallel |
| Files before a no-plan ticket must be planned | 5 |
| Files per task (planner rule) | about 8 |
| Turns per run | planner 40 · worker 70 · reviewer 30 |
| Time per agent run · per check | 45 min · 15 min |
| Worker session size before a fresh start | 45k tokens per turn |
| Diff sent to the reviewer | 60,000 characters |
| Repository map for the planner | 250 lines, built without an LLM |
| Notes in `CLAUDE.md` before a warning to prune | 40 |

## What makes it efficient and good

**Cost**
- **Isolation.** cw's agents ignore each developer's personal plugins and MCP servers. A run that only says "hi" went from 36.9k tokens of context to 5.9k.
- **Small, targeted context.** Workers went from 35–80k tokens per turn to about 8–12k, and the planner from about 19k to 4.4k.
  - The planner reads a map of the repository, not the code.
  - The planner hands workers 5–15 `file:lines` pointers to start from.
  - Rules arrive only when relevant: the core rules, the style rules and ponytail always (about 390 lines, cached), and a topic only when an agent touches a matching file.
- **The team's own conventions.** The Python rules are the Nexttech internship conventions (`nexttech-claude-kit`), copied without the reviewers' names, so agents write code the team's reviewers expect.
- **Prompt caching.** A worker keeps one session across related tasks, so repeated context is read from cache at about a tenth of the price.
- **No router agent and no parallel agents.** A first version used 1 planner, 17 workers and 2 reviewers for "two small features"; now the same work is 2 tickets with about 1 worker and 1 reviewer each, and 1 commit instead of 14.

**Quality**
- **A fixed pipeline, not free-roaming agents.** Plan → work → check → review → PR, the same every time.
- **cw checks instead of trusting.**
  - git decides which files changed, not the agent's report.
  - cw runs lint and tests itself after every task.
  - Failures that already exist on `main` aren't blamed on the ticket.
  - A task that touches files outside its plan gets a warning.
- **A strict reviewer on every change** names the team rule behind each finding. The worker fixes every finding, minor ones included, before the PR opens.
- **Agents can't commit, push, merge or switch branches.** A guard hook and a command allow-list enforce it; cw does all git work once, at the end.
- **Warnings, not hard blocks, where the planner might be wrong.** A guess about which files a task needs shouldn't stop a correct change.

## Research behind the design

| Finding | What cw does with it |
|---|---|
| A fixed localize → repair → validate pipeline matched or beat complex agents on SWE-bench Lite (Xia et al., *Agentless*, FSE 2025) | A fixed pipeline with deterministic checks |
| Agents use about 4× the tokens of chat, multi-agent systems about 15×, and most coding work is less parallel than research (Anthropic, multi-agent research system) | Workers take turns in one checkout; no parallel agents |
| Most multi-agent failures come from coordination, not from the individual agents (Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*, 14 failure modes) | One message channel (the ticket), owned by cw |
| Long contexts are used less reliably (Liu et al., *Lost in the Middle*) and context should stay small and relevant (Anthropic, context engineering) | Short hand-offs, small prompts, sessions capped in size |
| Self-feedback loops help but must stop (*Self-Refine*, *Reflexion*) | One review → fix → re-review round, capped |
| "Find the simplest solution possible" (Anthropic, *Building effective agents*) | A workflow, not an autonomous agent; no router |
| The Agent SDK may not offer claude.ai login to third parties | cw runs the official CLI headless, so each developer's own subscription works |

**Found while testing on real projects:**
- Claude Code reports a resumed session's cost as a running total; `cw stats` now counts each run once.
- A rule an agent learns only by being refused costs turns, so every limit is also stated in the agent's prompt.
- The reviewer may judge the same finding "minor" in one round and "blocking" in the next, so the fix round now gets every finding.
- Headless `/compact` works (`claude -p "/compact" --resume <id>`), but cw starts a fresh session from the ticket instead.

Sources and links: `EXPLANATION.md` §18.

## Tested on real work (16 November 2026)

| Ticket | Size | Result | Cost |
|---|---|---|---|
| Add a test to an existing ML repository | 1 file, no plan | merged | $0.02 |
| Per-face tracking in the same repository (a real bug fix) | 2 tasks, 11 files | merged; the reviewer's 5 findings were all fixed | ~$1.5 |
| A terminal tic-tac-toe game, from nothing | 6 tasks, 4 workers, 29 files, 51 tests | new private GitHub repo, merged, works when played | ~$2.5 |

Each run surfaced bugs in cw. Most are fixed; three are documented, each with a workaround, in `EXPLANATION.md` §16.

## Limits today

- Python is the only supported stack; Java is planned.
- GitHub is tested end to end; Bitbucket is written but has never run against a real server.
- One open ticket per project at a time.
- The toolkit is not published to a shared repository yet.

## More detail

`README.md` covers setup and every command, `EXPLANATION.md` how it works and why, `FIRST_RUN.md` a step-by-step first run, and `CHANGES_AND_ISSUES.md` what broke in real use and how it was fixed.
