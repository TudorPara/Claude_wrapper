---
name: nexttech-core-rules
description: The entry point for any code work in a Nexttech Python service (FastAPI, async SQLAlchemy, Alembic, pydantic-settings, LangChain, LangGraph). Holds the order of precedence, the three rules above all others (generic, no over-engineering, names that explain themselves), the ponytail ladder, the order of work, the map of which nexttech skill covers which topic, and the checklist to run before pushing. Load it before writing, changing or reviewing any code, even for a small change, then load the topic skills it names.
---

# Nexttech core rules

The conventions of Nexttech's Python services, as the mentors and reviewers enforced them on `nexttech-assistant-svc` (review comments of PRs #1 to #24, read on 2026-10-14). A source such as `#13` is a pull request of that repository. It says where a rule comes from; it is not something to quote in another repository's review.

The `nexttech-*` skills, `AGENTS.md` and `PR_review.md` are the same in every project and are never edited inside one. What differs in a project lives in two places: `.claude/CLAUDE.md` (the package name, the docs, the commands, the state of the code) and the `nexttech-project-rulings` skill (this project's rulings and open points). Wherever a rule here says `<package>`, read the package name from `CLAUDE.md`.

## 1. Precedence

1. A ruling by the project lead, the mentors or the project's reviewers.
2. `nexttech-project-rulings`.
3. The `nexttech-*` skills.
4. The project's `docs/`.
5. The generic skills: `clean-code`, `ponytail` and anything from a plugin.

When two rulings disagree, the later one wins. A point listed as open is a question for the project lead, not a defect. For anything else that is unclear, ask.

The docs are orientation, not law. What teammates build against is followed by default: table and column names, endpoint paths and status codes, payload keys, stream events. Everything else may deviate when the project lead asks or when a rule here says otherwise. Every deviation is named in the PR description, with its reason.

In a Nexttech project these skills replace `general-rules`, `engineering-review-rules` and `layered-service-architecture`. Between them those three still prescribe `src.` imports, `AppException(AppErrorCode.X)` and non-native enum columns, all of which are wrong here.

## 2. The three rules above all others

The conventions in these skills are strict.

1. **Generic.** Anything a teammate might configure differently (a provider, a model, a limit, a path, a cadence, a threshold) comes from settings, never from code. A service, a route or a repository depends on the abstraction (`VectorStore`, `Embeddings`, `BaseChatModel`, an interface of ours) and never on a provider's class, so changing a provider in the environment changes no service.
2. **No over-engineering.** The simplest code that is clean and correct. No abstraction, helper, layer or option the task does not need. It never overrides rule 1: remove a layer inside the provider package, not the abstraction a service depends on.
3. **Names that explain themselves.** A reviewer never has to ask what a function or a constant is. The naming rules are in `nexttech-python-style`.

## 3. The ladder

Writing code always starts with the `ponytail` skill in your system prompt. The ladder as it is used here, stopping at the first rung that holds:

1. Does this need to exist at all? If the need is speculative, skip it and say so in one line.
2. Is it already in this codebase or in an open PR? Reuse it.
3. Does the standard library do it? Python 3.14 has `uuid.uuid7`, and `hashlib`, `pathlib` and `asyncio.to_thread` cover most of the rest.
4. Does the database do it? A constraint beats application code.
5. Does an installed dependency do it? Never add one for what a few lines can do.
6. Can it be one line?
7. Only then, the minimum code that works.

Never simplify away validation at a trust boundary, error handling that prevents data loss, a security measure, the abstraction a service depends on, or a part of the docs teammates build against. The ladder shortens the solution, never the reading: trace the whole flow before choosing a rung.

What the ladder removed on `nexttech-assistant-svc` (AI-24), as a guide to what counts as too much: a dataclass that only carried three arguments into one method, a second delete of points the next call deleted anyway, a setting with one usable value, a helper with one caller, a provider dispatch that took the provider as an argument when the object already said what it was, and a background task plus three methods written only for a worker that did not exist yet.

Order of work: settle the contract (what goes in, what comes out, what can fail); write the narrowest thing that works; run it; generalise only when a second caller exists.

Where `ponytail` and these rules disagree, these rules win:

| `ponytail` says | Here |
|---|---|
| Comment only the why the code cannot show, in one line | No comment in code. The why goes in the docstring or the pull request (`nexttech-python-style`) |
| A shortcut with a known limit gets a `shortcut:` code comment | No comment. Name the shortcut and its limit in the reply, and list it for the PR description under known gaps |
| New non-trivial logic leaves one small test or an assert-based self-check | `CLAUDE.md` says whether the project has tests. Where it has none, do not add `pytest` or a test file unasked: verify with section 5, and say plainly what was not verified |

The same kind of table for `clean-code` is in `nexttech-python-style`.

## 4. Which skill covers what

| Working on | Load |
|---|---|
| Any name, function, class, docstring or comment | `nexttech-python-style`, with `clean-code` |
| `pyproject.toml`, a dependency, a command, lint, the sandbox | `nexttech-python-stack` |
| A route, a service, a repository, the lifespan, a provider factory, an interface | `nexttech-service-architecture` |
| A setting, an env key, `.env.example` | `nexttech-configuration` |
| An error code, a handler, a `try/except`, a log line | `nexttech-errors-and-logging` |
| An ORM model, a constraint, a migration | `nexttech-data-and-migrations` |
| An agent, a tool, a prompt, a stream | `nexttech-agents` |
| A branch, a commit, a merge, a PR description, a reply to a review | `nexttech-git-and-pr` |
| Anything, to see what this project decided differently | `nexttech-project-rulings` |
| Reviewing a PR, a branch or the working tree | `.claude/PR_review.md` |

## 5. Before you finish

1. The lint and test commands in your prompt pass, with no finding your change added. cw runs them again after your task.
2. A Grep search for each of these finds nothing in your change:
   - `^\s*(from|import) src(\.| )`: a `src.` import.
   - `^[A-Za-z_][A-Za-z_0-9.]* *(:[^=]+)?= *.*get_settings\(\)`: settings read at module level.
   - `^\s*print\(|StdOutCallbackHandler`: a print or a debug callback.
   - `HTTPException|status_code|JSONResponse|fastapi` in services or repositories.
   - `def _?(remove|discard|requeue|upsert|handle|process|save|manage|execute)[a-z_]*\(`: a banned verb.
   - `@dataclass|NamedTuple\)` in services, repositories, models or api.
   - `^\s*#|\s#\s`: a comment or `TODO` of yours.
   - A second `logging.getLogger` in one module.
   - `qdrant|docling|fastembed|ollama|anthropic|azure` in services, api or repositories, outside the modules the project's rulings name.
3. If a model or a migration changed: one Alembic head, a migration that matches the model, and `downgrade` undoing `upgrade`.
4. Every new setting is in `config.py` and in `.env.example` under the same name.
5. The full diff read line by line: only intended files, no dead code, no typo, no stray blank line.
6. After a rename, both names searched in strings, prompts, env keys and docstrings.
7. Every function you added or changed, read against "Functions and classes" in `nexttech-python-style`: it does one thing, its steps are in the order they happen, public methods come before private ones, nothing reaches through an object, and no comment stands in for a name.

You cannot import the app, start it or call its endpoints: say in your summary which of these were not verified.

What reviewers flag most often, in the order to check it: names, placement, dead code, docstrings, hardcoded values, errors, comments, enum case, format. The counts are in `PR_review.md`.
