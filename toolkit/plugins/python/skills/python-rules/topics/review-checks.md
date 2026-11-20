# Review checks

What to check in a Python diff, taken from the team's PR review procedure (`pr-review-procedure`), without its workflow and answer format: in a cw run the request, the diff and the check results are in your prompt, and you answer in cw's format with the severities `pr-review` gives.

## The checks

**A. Does it work, and does it stay correct when things go wrong**

- The workers' notes say how each changed endpoint was exercised, or say plainly that it was not.
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
- The lint check cw ran gains no finding.
- One import root, one logger per module, no comments, no typo in a name or a file name.
- `.env.example` still matches the settings after the change. A README finding only when the diff changes something the README documents (a command, an env key, a setup step, a public endpoint), naming that README line.
- The description says what the PR adds, how it was tested, what deviates from the docs, what is stubbed and what goes beyond the Linear issue.

**K. Names**, held to `nexttech-python-style`, and strictly

- Every function is a verb plus what it acts on. For reading and changing stored things the verb comes from the fixed list: `get`, `list`, `count`, `create`, `update`, `create_or_update`, `delete`, `build`, `validate`, `read`, `write`, `index`. The other accepted verbs are each the plain word for one action (`upload`, `reingest`, `retry`, `request`, `parse`, `split`, `compute`, `find`, `setup`, `preload`). A verb in neither list is a question to the author.
- One word per action across the diff. Deleting is `delete` everywhere: `remove`, `discard` and `requeue` in a function name are findings.
- No jargon and no abbreviation a reader has to look up (`upsert`, `probe`). No one-letter name.
- A name states everything the function does. One that also creates a row, or also deletes chunks, says so (`_delete_chunks_and_mark_document_failed`).
- A constant says what its value is, with the unit or the kind in the name (`OVERSIZE_CHECK_EXTRA_BYTES`, `OUTBOX_TYPE_SQL_VALUES`). No bare `type`, `data`, `result`, `value`, `text` or `helper`.
- The test for every name: would a reviewer have to ask what it is?

## Weighing a finding

| Level | What belongs there |
|---|---|
| **Blocking** | A bug, data loss, an identity or access hole, a deviation from the docs that is not explained, a session or an HTTP type leaking across a layer, an app that does not boot, a secret in the diff, migration drift or two heads |
| **Should fix** | A broken rule: a value in code, the wrong layer, a missing error path, a misleading name, dead code, over-engineering |
| **Nit** | Readability and style. Batch them and keep them few. Skip whatever `ruff format` fixes |
| **Question** | Intent you cannot see, or an open point. Ask which convention the author follows; do not call it a defect |

Only **Blocking** is `blocking` in your answer. Should fix, Nit and Question are `minor`: they go on the pull request, and cw sends them to the worker with the next blocking fix.
