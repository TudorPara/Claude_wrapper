---
name: python-rules
description: "The Nexttech rules for Python: an index of the topic files, which are the nexttech-claude-kit skills without the reviewers' names (core rules, Python style, stack, service architecture, configuration, errors and logging, data and migrations, agents, git and PR, the PR review procedure). Use it for any Python code, design or review task."
pin: true
---

# Python rules

The Nexttech rules for Python services. Each file in `topics/` is a skill or file from `nexttech-claude-kit`, copied with only the reviewers' names removed: edit the kit, then copy the file here again and remove the names. How these rules rank against the project's own decisions and the other skills, and what differs in a cw run, is in `rule-precedence`.

In a cw run, `core-rules`, `python-style` and the `ponytail` skill are always in your system prompt. The toolkit shows any other topic the first time you read or edit a file it covers, so you do not have to look for it. In an interactive session, read the file before you work in that area.

| Topic file | From the kit | Covers |
|---|---|---|
| `topics/core-rules.md` | `nexttech-core-rules` | Precedence, the three rules, the ladder, which topic covers what, before you push |
| `topics/python-style.md` | `nexttech-python-style` | Code style, docstrings, naming, functions and classes |
| `topics/python-stack.md` | `nexttech-python-stack` | Stack, commands, imports and packaging, dependencies, the Bash sandbox |
| `topics/service-architecture.md` | `nexttech-service-architecture` | Layout, layers, routes, transactions, lifetimes, providers |
| `topics/configuration.md` | `nexttech-configuration` | Settings, env keys, `.env.example` |
| `topics/errors-and-logging.md` | `nexttech-errors-and-logging` | `AppError`, the error catalogue, `try/except`, logs |
| `topics/data-and-migrations.md` | `nexttech-data-and-migrations` | ORM models, enums, constraints, Alembic |
| `topics/agents.md` | `nexttech-agents` | Agents, tools, the orchestrator, streaming |
| `topics/git-and-pr.md` | `nexttech-git-and-pr` | Branches, commits, the PR description, review replies |
| `topics/pr-review-procedure.md` | `PR_review.md` | How a review is done, its checks and the answer format |
