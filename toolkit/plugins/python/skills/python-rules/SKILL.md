---
name: python-rules
description: "The team's Python rules. This file is the core every Python task needs: code style, naming, the review checklist and an index of the topic files (stack and layout, configuration, layers, errors and logging, security, data model and migrations, agents, PR discipline). Use it for any Python code, design or review task."
pin: true
---

# Python rules

The Python part of the team's rulebook. The language-independent rules are in the `engineering` skill. A project's own decisions (its brief, its reviewers' rulings, its `CLAUDE.md`) win over both.

## Code style

- No comments in code, and none in `.env` or `.env.example`. One-line docstrings; add a `Raises:` section only where it helps. A docstring earns its place by explaining a decision the code cannot show; it never restates the next line. Longer reasoning goes in the pull request.
- Full type hints and return types on every function. Use enums, `Annotated` types and `Field` / `Query` descriptions wherever they fit.
- One logger per module, lowercase, defined once: `logger = logging.getLogger(__name__)`.
- One import root for internal imports (`src.<package>`). Never mix it with the bare package name: in an editable install both resolve and the module loads twice. Ruff's import sorting does not catch the mix; a grep does.
- `ruff check` and `ruff format` clean; imports sorted, no duplicates, no extra blank lines, no typos in names or docstrings.

Naming:

- **Builders and factories** are `setup_<thing>`, with one `setup_<provider>_<thing>` per provider.
- **Repository verbs**: `create`, `get_by_...`, `list_...`, `count`, `update_...`, `replace`, `delete_...`.
- **DTOs** are `*RequestModel` / `*ResponseModel`. Table names are plural.

Public and private:

- The underscore marks attributes, not parameters. `def __init__(self, session)` keeps the plain name; the attribute is `self._session`.
- Every collaborator a class receives to do its job is underscored unless callers are meant to use it. Module-level helpers and constants meant to stay internal take the underscore too; local variables never do.
- A private name imported by another module is a contradiction: make it public or move it.

## Review checklist

- [ ] The application boots, refuses to boot without its hard dependencies, and the health endpoint exercises them.
- [ ] Every configurable value comes from settings, with no default; `.env.example` mirrors `.env`.
- [ ] Configuration is read inside functions, not bound at import.
- [ ] No layer speaks another layer's vocabulary; dependencies point downward only.
- [ ] The session does not escape the service; the repository decides nothing, including the time.
- [ ] Services return response models; routes hold no business logic.
- [ ] Every lifecycle resource has one owner and one disposal path, and is published only after it is verified.
- [ ] Shared objects hold configuration only.
- [ ] Client-facing messages come from the error enum; specific reasons go to the log only; every error carries a stable code.
- [ ] No abstraction without a second caller; no wrapper around a library call.
- [ ] Every name states what it is; internal attributes are underscored; every constant names a decision.
- [ ] No comments; docstrings are one line; one import root and one logger spelling throughout.
- [ ] Identity never reaches a tool schema; prompts treat input as data.
- [ ] One Alembic head, no drift, migration bodies filled.

## Topics

The detailed rules are in `topics/`, one file per area, with their original section numbers. In a cw run the toolkit shows you a topic the first time you read or edit a file it covers, so you do not have to look for it. In an interactive session, read the file before you work in that area.

| Topic file | Sections | Covers |
|---|---|---|
| `topics/stack-layout.md` | 2, 3 | Tech stack with verified versions, project layout, commands |
| `topics/configuration.md` | 5 | Settings, environment keys, `.env.example` |
| `topics/layers.md` | 6, 7 | Routes, services, repositories, transactions, dependency injection, lifetimes, startup |
| `topics/errors-logging.md` | 8, 9 | `AppException`, error codes, error responses, logging |
| `topics/security.md` | 10 | Authentication, tokens, password hashing |
| `topics/data-api.md` | 11, 12 | Data model, API conventions, Alembic migrations |
| `topics/agents.md` | 13 | Provider factories, graphs, tools and agents, RAG |
| `topics/git-pr.md` | 14, 15 | PR and git discipline, verifying by running |
