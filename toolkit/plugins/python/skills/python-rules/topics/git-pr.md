## 14. PR and git discipline

- Ship only the task. Development scripts stay local and gitignored; when a brief asks for something kept local, say so in the PR.
- Never commit `.env`, data directories, uploads, model caches or generated files.
- Explain anything non-obvious in the PR description: deviations from the brief, provider and resource lifecycle, model preloading, capability checks of a replacement model. Known gaps are named in the PR, not left for the reviewer to find.
- Reply to every review comment with the reason for the change, not only the fix.
- Small commits per folder with conventional messages (`fix:`, `refactor:`, `feat:`, `chore:`). One concern per branch; never apply cross-cutting edits on two branches independently.
- Merge the base branch in early and often. After any merge, boot the application.

Merging between stacked branches:

- Back up first: `git branch backup/<branch>`.
- Merge, not rebase, into a branch with an open PR: `git merge --no-commit --no-ff <source>`. An IDE may auto-commit, so check `git status` before any commit.
- Before committing, check for silent deletions, one-sided changes dropped without a conflict, empty files created by "keep" on deleted-by-them conflicts, leftover conflict markers and stale imports.
- Untrack a file but keep it locally with `git rm --cached`, then gitignore it.
- Never `stash pop` onto a different branch than the one stashed from.

## 15. Verify by running

The first test in any service boots the application and hits the health endpoint; it catches wiring, import and merge damage.

```python
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
```

Injection is what makes the rest testable: `app.dependency_overrides[get_database]` swaps the whole persistence layer. Before claiming a failure path works, check it is reachable.

Pre-push checklist, every time:

1. `ruff check src` and `ruff format --check src`.
2. grep for the wrong import root, module-level `get_settings()` and `StdOutCallbackHandler`; nothing should match. No duplicate loggers.
3. `alembic heads` (one), `alembic upgrade head`, `alembic check` (no drift; on Alembic 1.19.x see section 12).
4. Start the app and exercise every endpoint touched: the happy path and at least one error path.
5. `git status` and the full diff read line by line: only intended files, no dead code, no typos, no extra blank lines.
6. After any IDE rename, grep both names in strings, prompts, env keys and docstrings.

## Examples

Snippets carry no comments, as the rules require.

**Replying to a review comment**
Comment: "This should be put in a function and called in lifespan. register_harness_profile applies per LLM, not per deep agent."
Good reply, reason first: "Moved the registration into `register_deep_agent_harness_profile`, called from lifespan right after `setup_llm` and keyed by `LLM__PROVIDER`. deepagents looks profiles up by provider, so a key named after the agent was never applied."
