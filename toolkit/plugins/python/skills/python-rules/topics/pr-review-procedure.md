# PR review

How to review a pull request, a branch or the working tree in a Nexttech repository, the way a senior engineer on the team would. The rules a review enforces are in the `nexttech-*` skills: load `nexttech-core-rules` and `nexttech-project-rulings` first, then the topic skills for what the diff touches. This file is the procedure, the checks and the shape of the answer.

A source such as `#13` is a pull request of `nexttech-assistant-svc`. It says where a rule comes from. In a comment on another repository, give the rule and its reason, not that source.

## Ground rules

- A review is read-only. Never post a comment, a review or an approval, and never write to Linear. The findings go to the project lead, who posts what they agree with.
- Never edit, stage or commit a file during a review. If he asks for a fix afterwards, that is a separate task.
- Never switch or check out a branch unless he asks. A branch that is not checked out is reviewed from `git show` and `git diff`, and the answer says that nothing was run.
- One inline pass in the main session is the default. The deep review at the end of this file runs only when he asks for it by name.
- Review what the PR changes and what that change breaks. Code outside the diff is in scope only where the diff makes it wrong.

## The procedure

**1. Understand the intent before the diff.**
The issue key is in the branch name (`feature/<KEY>-<n>-…`). Read the issue with the Linear read tools, and its sub-issues when it has them. Read the PR description. Read the doc sections the task implements. Then write the contract in two or three lines: what goes in, what comes out, what can fail. If you cannot, the first finding is a question about what the PR is for.

**2. Get the change.**

```bash
gh pr view <n> --json title,body,baseRefName,headRefName,files
gh pr diff <n>
gh api "repos/{owner}/{repo}/pulls/<n>/comments" --paginate

git fetch origin
git log --oneline origin/main..origin/<branch>
git diff origin/main...origin/<branch>
git show origin/<branch>:<path>

git status
git diff
git diff --staged
```

The first three read a pull request, the next four a branch, the last three the working tree. `git fetch` only refreshes the local copies of the remote branches. `gh` fails inside the Bash sandbox with a TLS or invalid-token error, because it cannot reach the keychain: rerun it outside the sandbox.

Read the existing review comments and their replies before writing yours. Do not repeat a point somebody already made; if it is still unaddressed, say so in one line. Read whole files, not only hunks: the diff shows what changed, and the bug is usually in what the change touches.

**3. Check the PR is reviewable.**
One concern, a description, files that belong to the issue. If it mixes concerns (a feature plus a rename, a style sweep, an unrelated fix), the first comment asks to split it, and the rest of the review covers the part that belongs.

**4. Review the design before the lines.**
Is this the right layer, the right owner, the right shape? Does a teammate's branch or an open PR already define the same thing? A wrong design makes line comments wasted, so raise it first and keep the line comments short.

**5. Trace each changed flow end to end.**
Route, service, repository, model, migration. For each step: what validates the input, what fails, who owns the session, what is written and in which order. For a worker handler: what happens if it runs twice, and what state is left if the process dies halfway.

**6. Hunt for what is missing, not only for what is wrong.**
The unhandled failure. The second caller. The concurrent run. The retry. The empty input. The other role. The migration for the model change. The `.env.example` key for the new setting. The deviation from the docs that the description does not mention.

**7. Check the shared contract against the docs.**
The docs are orientation, not law. What teammates build against is checked against the section that specifies it: table and column names, endpoint paths and status codes, payload keys, stream events. Quote the section in the finding. Anywhere else a deviation is fine when the description names it and gives the reason; an unnamed one is a finding.

**8. Run the checks below, then the over-engineering pass.**
Ask of every new function, class, option and dependency: can this be deleted?

**9. Run what can be run.**
When the branch is checked out: `make lint`, `make format-check`, `uv run python -c "import <package>.api.app"`, then boot the app and call the changed endpoints if the infrastructure is up. A branch that is not checked out can still be linted from a snapshot:

```bash
snapshot="$TMPDIR/review-snapshot"
rm -rf "$snapshot" && mkdir -p "$snapshot"
git archive origin/<branch> src alembic | tar -x -C "$snapshot"
uv run ruff check "$snapshot" --no-cache --output-format concise
uv run ruff format --check "$snapshot" --no-cache
rm -rf "$snapshot"
```

If `main` is not lint-clean, run the same on `origin/main` and count only the findings the branch adds. Where the project has no automated tests, the evidence is what the author says was run, and what you ran.

**10. Verify every finding before reporting it.**
Re-read the code path. Confirm the file and the line. Confirm the diff caused it. Confirm no ruling or open point in `nexttech-project-rulings` or in the shared skills already answers it. Drop what you cannot substantiate. What you suspect but cannot confirm is a question, not a finding.

## The checks

**A. Does it work, and does it stay correct when things go wrong**

- The app imports and boots.
- Each changed endpoint has had its happy path and at least one error path exercised, by the author (stated in the PR) or by you.
- Empty, missing and oversized input. The same call twice. Two callers at once.
- No missing `await`. Blocking calls (file I/O, parsing, hashing, a synchronous client) leave the event loop through `asyncio.to_thread`.
- No session is held across a model call, an agent run, parsing, embedding, a vector-store call or file I/O.
- Two writes to two stores: what is left if the process dies between them, and does the next run or retry repair it? What says "this is done" is written last. On removal, the derived data and then the record.
- A write that fails after another store was already written undoes that earlier write and re-raises the original error. The undo logs its own failure and never hides the first one.
- A worker handler is safe to run twice. An error that can never succeed goes straight to failed.
- Every failure path is reachable: the exception is imported, and the error raised is one the handler maps.

**B. Identity and access**

- Who the caller is comes only from what the project's auth reads (the docs say what that is). No user id in a path, a query, a body or a tool schema decides it.
- A new route declares the current user unless it is meant to be public.
- Admin-only routes check the role, not a document scope.
- Every query on somebody's own data filters on the user id, in the repository. A missing or foreign resource answers 404, not 403.
- Entitlements are resolved before any model runs and are never widened later. A search filters by document scope inside the query.
- No token in a response body. No password, hash, token or cookie value in a log. A validation error does not echo the input.
- Retrieved text, user input and a form's `context` are validated as data and never obeyed as instructions.
- No secret, `.env`, upload or data directory in the diff.

**C. Contract with the docs**

- Paths, methods, status codes and who may call each endpoint match the project's API doc.
- Table and column names, nullability, constraints and indexes match the data doc, apart from the rulings. Enum values are the UPPERCASE ones in `models/`, even where a doc lists them in lowercase.
- Stream events and their order match the doc that specifies them.
- A deviation is either explained in the PR description or a finding. A deviation the project lead asked for is not a defect: check only that the description names it.
- Something the docs leave open ("a starting point", "yours to work out", any number) is a design choice. Check that it is stated and configurable, not that it equals a number in the doc.

**D. Layers and lifetimes**

- Route: HTTP only, one service call, no business rule, no session, no query, no `try`, no log line. It declares `response_model`, `status_code` and `responses`.
- Service: the rules and the transaction; returns response models through a mapper, not ORM entities; no `HTTPException`, `status_code` or `Response`.
- A new value object is a Pydantic model, not a dataclass or a `NamedTuple`. A function with a few inputs takes plain arguments. A dataclass already on `main` is not a finding.
- Repository: no commit, no decision, no clock; every method wrapped; ids only in its logs.
- Dependencies point downward. No private name imported across modules, no sibling import to borrow a value.
- A mapper is in `mappers/`, an enum in `models/`, a helper only in a helpers module.
- Anything with a lifecycle is built once in the lifespan, published on `app.state` after it is verified, and disposed on every exit path. No module-level instance and no import-time side effect.
- Shared objects (services, compiled agents, middleware) keep no per-request or per-run state.

**E. Configuration**

- Nothing a teammate would change is hardcoded. No new default. No settings read at module level.
- Each new key is in `config.py` and in `.env.example` under the same name, with an empty value and no comment, and the file ends with a newline.
- Changing provider is a settings change only, and the settings stay provider-neutral.
- No service, route or repository imports a provider's package or is typed to a provider's class. Provider-specific code stays in `vector_store/`, `embedding/` and `llm/`, behind a dispatch. The accepted exceptions are listed in `nexttech-project-rulings`.

**F. Errors and logs**

- One exception type, one member per failure the client must tell apart, one place building the body. No `raise Exception`, no bare `ValueError` for a business rule, no error JSON in a route, no `str(exc)` in a response.
- The raise site logs the specific reason with ids, at the right level. `logger.exception` appears only inside an `except`.
- No `print`, no debug callback.

**G. Data model and migrations**

- Names and types as specified: `timestamptz`, `text`, uuid7 from the application, `Audit`, a native enum holding the values of the enum in `models/`.
- A table another task owns may appear as a stand-in only when the file starts with a comment naming the owner and the description names it too. A stand-in is not held to `doc=`, indexes or a migration.
- Every column has `doc=`. An invariant the docs or a docstring state is a constraint.
- The model is exported from `schemas/__init__.py`.
- The migration matches the model, there is one head, no body is empty, an enum change is hand-written, and `downgrade` undoes `upgrade`. No migration already on `main` was edited.
- No second definition of a table or an enum that another PR already owns.

**H. Agents and streaming**, when the diff touches them

- The orchestrator receives only the entitled sub-agents. Routing never widens access. Identity is not a tool argument.
- Agents are built with `create_deep_agent`, built-in tools are filtered with middleware, and tool names come from the enum in the agent's `constants.py`.
- Decisions are typed. Thresholds and loop bounds come from settings. A bound reached returns what was found.
- Nothing is written to an outside system before the person confirms. There is no LangGraph `interrupt`.
- Sources come from the retrieved passages.
- The answer streams one delta per model chunk, the deltas concatenate to the stored content, there is at most one payload, and exactly one `final` or `error` ends the stream.

**I. Simplicity**

- What can be deleted: a helper with one caller, a wrapper around one library call, a base class or a registry with one user, an option nobody sets, a constant whose name is its value, dead code, a `TODO`.
- What may not be deleted in the name of simplicity: the abstraction a service depends on, validation at a trust boundary, the undo of a half-finished write.
- Anything written for a later task is a finding: a method only a future caller would use, a hook nobody uses yet, a comment about how a teammate's part will work. The branch implements its own Linear issue; what is stubbed goes in the description.
- An interface with one implementation, unless the docs ask for it.
- A new dependency for what the standard library or an installed package already does.
- Hand-written error handling where the library already owns it.

**J. Hygiene**

- Only the task's files. `uv.lock` changes only with `pyproject.toml`. No generated or personal file, and nothing personal added to `.gitignore`.
- `make lint` and `make format-check` gain no finding.
- One import root, one logger per module, no comments, no typo in a name or a file name.
- The README and `.env.example` still match the tree after the change.
- The description says what the PR adds, how it was tested, what deviates from the docs, what is stubbed and what goes beyond the Linear issue.

**K. Names**, held to `nexttech-python-style`, and strictly

- Every function is a verb plus what it acts on. For reading and changing stored things the verb comes from the fixed list: `get`, `list`, `count`, `create`, `update`, `create_or_update`, `delete`, `build`, `validate`, `read`, `write`, `index`. The other accepted verbs are each the plain word for one action (`upload`, `reingest`, `retry`, `request`, `parse`, `split`, `compute`, `find`, `setup`, `preload`). A verb in neither list is a question to the author.
- One word per action across the diff. Deleting is `delete` everywhere: `remove`, `discard` and `requeue` in a function name are findings.
- No jargon and no abbreviation a reader has to look up (`upsert`, `probe`). No one-letter name.
- A name states everything the function does. One that also creates a row, or also deletes chunks, says so (`_delete_chunks_and_mark_document_failed`).
- A constant says what its value is, with the unit or the kind in the name (`OVERSIZE_CHECK_EXTRA_BYTES`, `OUTBOX_TYPE_SQL_VALUES`). No bare `type`, `data`, `result`, `value`, `text` or `helper`.
- The test for every name: would a reviewer have to ask what it is?

## What Nexttech reviewers actually flag

Approximate counts over the top-level inline comments of PRs #1 to #24 of `nexttech-assistant-svc` (about 650 comments, read on 2026-10-14). Expect these, and catch them first, in this order.

| Theme | Approx. | Typical comment |
|---|---|---|
| Naming: vague, one-letter, not explicit, not matching what the code does | 75 | "Please improve the naming, do not use so vague naming ... let's call this `cv_reading_service`" (#14) |
| Code in the wrong place: model, mapper, lifespan, service | 55 | "This is a mapper, why is this in a controller?" (#6) |
| Not needed: delete it | 45 | "Do we need this? And why?" (#5, #6, #7, #13) |
| Docstrings missing, unclear or not in the team form | 35 | "I understand almost nothing from this docs ... keep them only if they are clear enough" (#22) |
| Magic numbers; values that should come from env or a constant | 30 | "This should come from env." (#1) |
| Errors: our `AppError`, the right log level, `try/except` in the right layer | 25 | "Why aren't we catching SQLAlchemy exception in all repository methods?" (#6) |
| Comments, `TODO`s and `print`s left in code | 17 | "why this comment here? if valuable info -> docs" (#22) |
| Enum values not UPPERCASE | 16 | "These should be uppercase" (#4, #5, #6) |
| Formatting, lint, generated files, `.gitignore` | 14 | "make format-check fails here ... Run make format before pushing." (#13) |

Also seen more than once:

- Settings that are specific to one provider when they should be generic.
- A `src.` import, or two import roots in one file.
- A branch with no log line, the wrong level, or `logger.exception` with no exception in flight.
- A factory that reads the settings itself instead of receiving them.
- A function named `get_…` that creates something.
- An enum defined in the wrong package, or imported through a module that only re-exports it.
- A hardcoded protocol constant, such as a version or a catalog id.
- A dev tool listed as a runtime dependency. Template text left in `pyproject.toml`.
- A README command that no longer matches the tree.
- A column without `doc=`. An invariant written in a docstring and not enforced. A counter with no default.
- A comment marked resolved that is not.

## Weighing a finding

| Level | What belongs there |
|---|---|
| **Blocking** | A bug, data loss, an identity or access hole, a deviation from the docs that is not explained, a session or an HTTP type leaking across a layer, an app that does not boot, a secret in the diff, migration drift or two heads |
| **Should fix** | A broken rule: a value in code, the wrong layer, a missing error path, a misleading name, dead code, over-engineering |
| **Nit** | Readability and style. Batch them and keep them few. Skip whatever `ruff format` fixes |
| **Question** | Intent you cannot see, or an open point. Ask which convention the author follows; do not call it a defect |

How a senior reviewer behaves:

- Approves when the change is correct and leaves the code better, not when it is perfect.
- Makes one point per comment: what is wrong, why (the rule or the doc section), and the smallest fix. Prefers deleting to adding.
- Separates "must" from "could" in the wording.
- Comments on the code, never on the person. Asks when unsure.
- Does not restate the diff, does not praise by default, does not ask for a rewrite to personal taste, and does not relitigate a ruling.
- On a second pass, checks that each comment was answered with a reason and that the fix broke nothing next to it.

## The answer

```
## Review: <PR title or branch> (<KEY>-<n>)

Verdict: approve | approve with nits | request changes
What it does: <the contract, two or three lines>
Verified: <what was run, and the result>
Not verified: <what was not, and why>

### Blocking
1. `path/to/file.py:LINE` <the comment, ready to paste>
   Why: <rule or doc section>. Fix: <smallest change>.

### Should fix
### Nits
### Questions
### Outside this PR
```

Most severe first. Omit an empty section. "Outside this PR" holds what is real but belongs to another issue.

Each comment is written the way the team writes them on GitHub: short, direct, in English, one point, the reason included, no heading and no emoji. Real ones show the tone:

- "This should come from env."
- "ruff is a dev tool, not a runtime dependency."
- "LLMProvider now lives in models, so import it from there. Routing it through config is the indirection the move was meant to remove."
- "logger.exception with no exception in flight. The same branch in vector_score_factory logs a warning, so pick one."

## Reviewing your own branch before opening a PR

Run the same procedure on `git diff origin/main...HEAD`, then "Before you push" in `nexttech-core-rules`. Tick every item the Linear issue says the task should cover as done, missing, or stubbed and said so. Then draft the description:

```
What this adds (<KEY>-<n>)
- <one line per thing>

How I tested it
<what was run, against what, and what was seen>

Deviations from the docs
<each one, with the reason; "none" if none>

Stubbed or not done
<what waits for another task, and which one>

Beyond the issue
<what this adds that the Linear issue does not ask for, and why; "nothing" if nothing>
```

## Answering a review comment

Give the reason first, then the change. When you keep the code, say why. An example, on a comment asking to rename `page` to `page_number`: "I'd keep page, because it matches the query parameter clients send (?page=2&page_size=20) and its description already says it's the page number, starting at 1."

## Deep review, only when the project lead asks for it by name

The six review-council reviewers go over the same diff in parallel: `review-council:architecture-reviewer`, `review-council:correctness-reviewer`, `review-council:interface-reviewer`, `review-council:policy-boundary-reviewer`, `review-council:security-reviewer` and `review-council:test-evidence-reviewer`. They can only read files, so the branch must be the one checked out. Give each of them the base branch, the issue's contract and the path of `.claude/skills/`. Merge their findings, remove the duplicates, verify each one yourself as in step 10, and answer in the same format. Never start this on your own: it is slower and costs more than the inline pass.
