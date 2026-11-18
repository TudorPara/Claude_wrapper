# cw + FRIDAY

A workflow around Claude Code for a small team: a shared **toolkit** of agents, skills and hooks, **cw** (a Python CLI that runs those agents on tickets) and **FRIDAY** (a macOS app over cw). How it works: `EXPLANATION.md`. Setup and daily use: `FIRST_RUN.md`. History of what broke and why: `CHANGES_AND_ISSUES.md`.

## Layout

| Path | What |
|---|---|
| `backend/src/cw/` | `cli.py` commands · `pipeline.py` the ticket flow · `prompts.py` what each agent is told · `claude_runner.py` one `claude -p` run · `process.py` child processes with a time limit · `tickets.py` ticket files · `project_memory.py` notes in a project's `CLAUDE.md` · `repo_map.py` the planner's map of a repository · `git.py` · `onboarding.py` projects · `config.py` · `toolkit.py` reads the toolkit |
| `backend/tests/` | `conftest.py` has `FakeAgents`, which stands in for `claude -p` |
| `toolkit/plugins/core/` | agents `planner`, `worker`, `reviewer`, `onboarder`; skills; `scripts/guard.py` |
| `toolkit/plugins/python/` | skill `python-rules` (index `SKILL.md` + `topics/`, the nexttech-claude-kit rules without reviewers' names), hooks `rules_on_touch.py`, `ruff_on_edit.py` |
| `friday/Sources/Friday/` | the SwiftUI app; it only runs `cw` and decodes its `--json` output (`Store.swift` reads and runs, `Insights.swift` turns a ticket into stages, agents and actions) |
| `friday/Tests/` | decoding and logic tests on real `cw` output; `make_fixtures.py` rebuilds the fixtures |

## Commands

```bash
cd backend && uv sync && uv run ruff check src tests && uv run ruff format --check src tests && uv run pytest -q
cd friday && swift build && ./test.sh   # ./build-app.sh makes FRIDAY.app
claude plugin validate toolkit
uv tool install --force ./backend   # after changing cw, so the `cw` on your PATH is the new one
```

## Branches and where things stand

- `main` works and is left as it is. New work goes on its own branch: `feature/<paper>` for an idea from a research paper (judged by: results as good as plain Claude Code, a multi-agent flow, fewer tokens), `fix/<topic>` for fixes. Never commit to `main` directly.
- `feature/react` (pushed): one back-off step, a fresh worker session started from the last attempt's reflection when a task's checks still fail after its fix round. Not tried on a real project yet.
- `fix/review-findings` (not committed yet when this was written): the outside review's B1–B8, B15, C and D1, D2, D4–D6; `cw fix` loops while blocking findings stay open; the event log (`cw events`), run labels, `cw doctor --json`; FRIDAY redesigned around the agents; shipped defaults of 2 review and 2 fix rounds. Details: the 2026-11-18 entries of `CHANGES_AND_ISSUES.md`.
- Still open: B9–B14 and B16–B21 (listed in `EXPLANATION.md` §16), and the three known bugs listed there. Nothing on this branch has run against a live ticket yet; the next useful check is one real ticket through `cw` and FRIDAY, compared with `cw stats` on `main`.

## Rules that must keep holding

- Agents never commit, push, merge or switch branches. cw does it, once, at `cw pr`. The guard hook and the worker's Bash allow-list enforce this.
- Agents communicate only through the ticket. `prompts.py` gives an agent the part of the ticket it needs; its structured answer is written back by `pipeline.py`. A resumed worker gets only what is new.
- Nothing is written into a project's repository except the agent's code changes and the "Notes kept by cw" section of its `CLAUDE.md` (`project_memory.py`, from the workers' optional `project_notes`, at most five per ticket). Both are in the ticket's diff, reviewed and committed once. Tickets live in `~/.cw/tickets/<project>/` as `<id>.json` (state) and `<id>.md` (the readable twin).
- Workers take turns in one checkout. No parallel edits, no worktrees.
- Every loop has a cap in `Limits` (`config.py`). A new loop gets a cap.
- Rules reach agents three ways: pinned skills (`pin: true`, keep them short), topic files shown by `rules_on_touch.py`, and hooks. Do not paste long rule files into prompts.
- `cw ... --json` is FRIDAY's contract. A field added in `models.py` or `config.py` needs a default, and `friday/Sources/Friday/Models.swift` if the app shows or saves it. Rerun `friday/Tests/make_fixtures.py` and `friday/test.sh` after changing it.

## When you change something

- Behaviour of the flow: add or change a test in `backend/tests/test_pipeline.py` using `FakeAgents`. Do not call the real `claude` in tests.
- An agent file or a skill: it is prompt text other agents depend on. Keep it short and concrete, and check `prompts.py` still matches what it promises.
- The flow, an agent, a hook or the config: update `EXPLANATION.md`, and add an entry to `CHANGES_AND_ISSUES.md` when it fixes something found in real use.
- Follow the team's own rules in `toolkit/plugins/core/skills/engineering/SKILL.md`: no magic values, no comments in code, one-line docstrings, the smallest change that works.
