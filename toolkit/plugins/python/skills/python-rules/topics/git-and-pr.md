---
name: nexttech-git-and-pr
description: Branch, commit, merge and pull-request discipline on a Nexttech project. Covers one Linear issue per branch, what belongs in a PR and what never gets committed, conventional commit messages, the PR description, replying to review comments, merging main and stacked branches safely, and reusing what a teammate's branch already defines. Use it when preparing a branch for review, drafting a commit message or a PR description, answering a review comment, merging or resolving conflicts, or deciding whether a change belongs in this PR.
---

# Nexttech git and PR discipline

A source such as `#13` is a pull request of `nexttech-assistant-svc`. What the agent itself may run (never `git add`, `commit` or `push`) is in `.claude/AGENTS.md`; this file is the discipline the work follows.

## 1. Scope of a branch

- One Linear issue per branch, named `feature/<KEY>-<n>-<slug>`. `CLAUDE.md` gives the team key.
- Implement what the Linear issue lists and no more. What another issue owns is stubbed in the smallest way that lets this one work, and named in the PR description; read that issue in Linear to name the right owner.
- Reuse what `main` or an open PR already defines (an enum, the error type, the page model, an ORM model). Do not write a second definition.
- When the same thing exists on a branch and on `main`, the one on `main` is used. When `main` is merged in, main's version of a shared file is taken as it is and the branch adapts to it.
- No cross-cutting edit inside a feature PR: no import-root sweep, rename, style pass or lint clean-up of files the task does not own.
- A development script, an upload, a data directory, `.env`, a cache or `__pycache__` is never committed.
- An edit to a file a teammate wrote is a question to the project lead first.

## 2. Commits

- Small commits per folder with conventional messages: `feat:`, `fix:`, `refactor:`, `chore:`. Example: `refactor: address review feedback on settings` (#1).
- `uv.lock` changes only together with `pyproject.toml`.

## 3. The pull request

- Sync with `main` before review (#7). Merge `main` in early and often.
- After any merge, import the app and boot it: a resolved conflict can leave a name without its import, or delete a call without a conflict.
- Run "Before you push" in `nexttech-core-rules`.
- The description says what the PR adds, how it was tested, what deviates from the docs, what is stubbed and why, what goes beyond the Linear issue, and the known gaps, so the reviewer does not have to find them. The template is in `.claude/PR_review.md`.
- The README and the PR description match the code: "The description still mentions `search_wiki` and `list_documents`" (#13).
- Reviewers flag AI-written code that was applied as-is. Read every line before it goes up.

## 4. Review comments

- Reply to every review comment with the reason, not only the fix. When you keep the code, say why.
- A comment marked resolved must be resolved: "it does not look resolved to me" (#1); "This is not solved" (#4).
- From #4, on a comment asking to rename `page` to `page_number`: "I'd keep page, because it matches the query parameter clients send (?page=2&page_size=20) and its description already says it's the page number, starting at 1."

## 5. Merging between stacked branches

- Back up first: `git branch backup/<branch>`.
- Merge, not rebase, into a branch with an open PR: `git merge --no-commit --no-ff <source>`. An IDE may auto-commit, so check `git status` before any commit.
- Before committing, look for silent deletions, one-sided changes dropped without a conflict, empty files left by "keep" on a deleted-by-them conflict, leftover conflict markers and stale imports.
- Resolve a generated file by regenerating it, never by editing it: the lockfile, a migration.
- After merging `main`, run `alembic heads`; two heads means your revision is regenerated on top of the new one.
- Untrack a file but keep it locally with `git rm --cached`, then exclude it in `.git/info/exclude`.
- Never `stash pop` onto a branch other than the one stashed from.
