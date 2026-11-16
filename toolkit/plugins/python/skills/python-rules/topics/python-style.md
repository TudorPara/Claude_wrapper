---
name: nexttech-python-style
description: How Python is written in a Nexttech service. Covers naming (verb plus object, the fixed verb list, banned names, constants with units), functions and classes (one thing, one level of detail, read top-down), docstrings in the team form, the no-comments rule, type hints, public and private names, and where the clean-code skill is overridden. Use it whenever you write, rename, refactor or review a function, a class, a constant, a variable, a docstring or a comment, together with clean-code.
---

# Nexttech Python style

Naming is what reviewers flag most (about 75 of the 650 review comments on `nexttech-assistant-svc`), docstrings fourth (about 35), comments and leftovers seventh (about 17). A source such as `#13` is a pull request of that repository.

## 1. Code style

- No magic numbers or strings: a named constant next to its use, or a setting. That includes path fragments, metadata keys, prefixes, router prefixes and tags.
- No comments in code, `.env`, `.env.example` or `alembic.ini` (#1; #11; #22). No `print` (#1). No `TODO`: what stands in for another task is listed in the PR description, not in the code.
- The only comments that stay: one the project lead asks for, one a reviewer asked for ("add a comment that you override it somewhere", #7), and the header of a stand-in file below.
- A file that stands in for something another task owns, and is not on `main` yet, starts with a two-line comment saying so: who owns the real thing, and to delete the file once that PR is merged. Files already on `main` are never deleted or marked this way.
- Nothing is built for a later task. No method only a future caller would use, no parameter nobody passes yet, no comment about how a teammate's part will work. If the code needs a sentence about the future to make sense, the code is doing too much.
- A new value object is a Pydantic model, not a dataclass or a `NamedTuple` (#6; #13). A function with a few inputs takes plain arguments rather than an object built to carry them. A dataclass already on `main` stays as its owner wrote it.
- A Pydantic value object lives in `models/`, in its domain's file, even when one service is its only user (#9 review).
- Full type hints and return types on every function. Use enums, `Annotated` types and `Field` or `Query` descriptions wherever they fit, and make the descriptions say something. A plain string inside `Annotated` does nothing for Pydantic; use `Field(description=...)` (#13).
- A description, a docstring or a message never repeats a constant's value as a literal. Build it from the constant: `description=f"Page number, starting at {MIN_PAGE}."` (#9 review).
- One logger per module, defined once: `logger = logging.getLogger(__name__)`.
- One import root: the bare package name, never `src.`. Ruff's import sorting does not catch a `src.` import; a grep does.
- No dead code: no unused constant, helper, import or method.
- Code reads as written by a person: no filler names, no docstring that repeats the code.

## 2. Docstrings

- The team form (#13; #11). The opening quotes stand on their own line, then a summary of at most three lines that explains, a blank line, one `:param name:` per argument, `:return:` when something is returned, and `:raises AppError: CODE when ...` naming the error codes.
- A function with no argument and no return keeps a one-line docstring.
- A docstring with only `:param` lines is incomplete (#14).
- Every `:param` matches a real argument and every `:raises` names what is actually raised (#17). Write them from the code, never from memory.
- A docstring says what the code cannot show, such as a decision or an ordering. It never restates the next line. Longer reasoning goes in the pull request.
- If a function name has a verb such as `build`, the docstring says what is built and from what (#13).
- Read AI-written docstrings before pushing; delete what a reader cannot follow (#22: "I understand almost nothing from this docs ... keep them only if they are clear enough").
- A route's docstring is shown in the generated API docs as the endpoint's description, `:param` lines included.

## 3. Naming

These rules are strict. The test for every name: would a reviewer have to ask what it is? If yes, rename it.

- **Verb plus object.** Every function is a verb followed by what it acts on: `get_document_by_path`, `delete_document`, `build_relative_path`. Never `handle`, `process`, `do_work`, `execute`, `run`, `manage`, `save`.
- **The object is spelled out in full, even when the class name already implies it.** `get_document_by_id`, `write_file`, `split_into_chunks`, `parse_document`, never `get_by_id`, `write`, `split`, `parse`. Two methods that return different things never differ by the verb alone: `list_jobs` beside `get_job` became `list_ingestion_jobs` and `get_ingestion_job`. The docstring states the difference as well.
- **A fixed verb list for stored things.** `get` (one, may be missing), `list`, `count`, `create`, `update`, `create_or_update`, `delete`, `build` (make a value, no I/O), `validate`, `read`, `write`, `index`.
- **Other accepted verbs**, each the plain word for one action: `upload`, `reingest`, `retry`, `request`, `parse`, `split`, `compute`, `find`, `setup`, `preload`. A verb in neither list is a question to the project lead before it is used.
- **One word per action.** Deleting is always `delete`: no `remove`, `discard`, `drop` or `requeue` in a function name. A name that belongs to a teammate's code or to a stored enum value stays as its owner wrote it.
- **No jargon, no abbreviation.** Not `upsert`, not `probe`.
- **Explicit over short** (#6, #11, #14): `cv_reading_service` not `reader`, `auth_service`, `token_service`, `outbox_item`, `outbox_id`, `revoked_token`.
- **No one-letter names**: `c`, `k`, `e`, `ei`, `I` (#8, #13, #14, #22).
- **One action per method.** A repository has `create_document` and `update_document`, and the caller picks the one that applies; it does not have one method that inserts or updates depending on what it finds.
- **A name states everything the function does.** One that also deletes chunks says so: `_delete_chunks_and_mark_document_failed`. A long exact name beats a short vague one. A name that needs three verbs means the function does too much and is split into one method per use case.
- **A name says what the function does, and ask "what" when it does not** (#8, #14: "The function name does not match what the actual function is doing", "Prepare what?", "Submission of what?").
- **Two functions that differ get names that show the difference.** `get_document_by_id` returns `None` when the row is missing; the service method that raises instead is `_get_document_or_raise`. `build_relative_path` and `_build_absolute_path`, never two things called "path".
- **Builders** are `setup_<provider>_<thing>`, one per provider, never `get_...` (#1).
- **Repository verbs:** `create_…`, `get_by_…`, `list_…`, `count_…`, `update_…`, `create_or_update`, `delete_…`.
- **DTOs** are `*RequestModel` and `*ResponseModel`; stream events are `*EventModel`.
- **A field is named for what it is**, never a bare `type`: `payload_type`, `outbox_type` (#4).
- **Classes are nouns** that say what the thing is: `DocumentService`, `TextChunker`, `DoclingDocumentParser`. Never a word that says nothing: `Manager`, `Helper`, `Processor`, `Info`, a bare `Data`. A class name that says nothing is renamed: `Intent` (#7), `CVService` (#20), `Principal` (#18).
- **An acronym keeps its capitals in every name:** `CVScanJob`, `CVSource`, `SendCVReminderHandler` (#5, #8; #17).
- **ORM classes are singular**; tables keep the names in the project's data doc (#5).
- **A SQLAlchemy select is called `statement`**, not `query` (#22).
- **A name never promises a type or a shape the thing does not have.** A dict is not `document_list`; a quoted SQL fragment is not `TYPE_VALUES`.
- **Booleans and predicates** read as questions: `is_active`, `has_expired`.
- **Constants say what the value is**, with the kind or the unit in the name: `OVERSIZE_CHECK_EXTRA_BYTES`, `TITLE_LABELS_BY_PRIORITY`, `PATH_SEPARATOR`, `OUTBOX_TYPE_SQL_VALUES`. A constant whose name is its value in capitals is inlined, and so is a literal the keyword argument already names: `image_placeholder=""`, not `EMPTY_IMAGE_PLACEHOLDER`.
- **Units live in the name:** `timeout_seconds`, `max_file_size_bytes`, `access_token_expire_minutes`.
- **Banned as standalone names:** `data`, `info`, `obj`, `temp`, `result`, `value`, `item`, `text`, `manager`, `helper`, `util`. Say which one: `metadata_value`, `stored_document`, `markdown_text`.
- **A name on `app.state`, and the provider that reads it, say what the object is for:** `document_chunker` and `get_document_chunker` (#9 review).
- **One word per concept** across the codebase and across layers: pick `get` or `fetch` or `find` and never alternate; `similarity_score` in settings and in code, not `min_score`; `neighbours`, not both spellings (#13).
- **File names match their content and have no typo:** `vector_store_factory.py` (#1), `outbox_consumer.py` (#11). For a new file, follow the naming of its neighbours on `main`.
- If a name needs a comment to be understood, fix the name.

Public and private:

- The underscore marks attributes, not parameters: `def __init__(self, session)` keeps the plain name and stores `self._session`.
- Every collaborator a class receives is underscored unless callers are meant to use it. Module-level helpers and constants meant to stay internal take the underscore; local variables never do.
- A private name imported by another module is a contradiction: make it public or move it.

After an IDE rename with "search in comments and strings", grep both the old and the new name in strings, prompts, env keys and docstrings.

## 4. Functions and classes

These come from the `clean-code` skill, adjusted to Nexttech. The test: can someone who has never seen the flow read the method from top to bottom and say what happens?

- **One thing per function.** If describing it takes an "and", or its name needs three verbs, it is split into one function per use case: new upload, new version, reingest and delete are four methods.
- **Small.** A body longer than 20 lines is a reason to check that it still does one thing. It is not a reason to cut it into helpers.
- **One level of detail.** A public service method reads as the steps of its use case, in the order they happen, one step per line: a call to a private method, to a repository or to a collaborator.
- **Read top-down.** Constants sit at the top of the module, under the imports. In a class: `__init__`, then the public methods, then the private ones in the order the public methods reach them. A small lookup shared by several use cases goes last. Module-level helpers follow the class.
- **A local variable is assigned right before the step that uses it**, never in a block at the top of the function.
- **Arguments.** Three is where to stop and look. When several come from one object, pass the object: `split_into_chunks(text, document)` replaced five arguments. Never build an object only to carry them. A constructor that receives its collaborators and a route that declares its query parameters are not what this rule is about.
- **No hidden effect.** A function changes only what its name says, and never module-level state.
- **A condition that needs explaining gets a name, not a comment:** a predicate, or a local that says what was found (`existing_document`).
- **A class has one responsibility.** A class that needs "and" to be described is two classes.
- **A method talks to what it was given:** its arguments and the collaborators on `self`. It never reaches through one of them into another (`self._service._repository._session`). That is why a factory receives its settings group and not the whole `Settings`.
- **Signs that the design is wrong.** Stop and simplify when one change forces edits in many files, when a change breaks a place that looks unrelated, when a piece cannot be reused without dragging its surroundings along, when doing it the wrong way is easier than doing it the right way, or when the same rule is written in two places. A few plain lines repeated in two use cases are not that last sign: they read better than a shared helper whose name needs explaining.

One level of detail, from `DocumentService.upload_document` on `nexttech-assistant-svc`. Each line is one step, and the private methods follow below in this order:

```python
file_name = self._validate_file_name(file_name)
content = await self._read_upload_within_size_limit(stream, file_name)
path = self._file_storage.build_relative_path(access_level, file_name)
async with self._session_factory.get_session() as session:
    existing_document = await DocumentRepository(session).get_document_by_path(
        path
    )
if existing_document is None:
    return await self._upload_new_document(path, access_level, content)
return await self._upload_new_version_of_document(existing_document, content)
```

## 5. Where clean-code and Nexttech disagree, Nexttech wins

| `clean-code` says | Here |
|---|---|
| Keep extracting until every function is tiny | Rule 2 wins. A private method earns its place by naming one step of a use case. A helper with one caller that only shortens its caller is removed |
| Some comments are good: legal, informative, clarifying, `TODO` | No comment and no `TODO` in the code, with the exceptions named in section 1 |
| Don't return null | A `get_…` returns `None` for a row that is missing, and its return type says so. The service turns that into an error once, in a `_get_…_or_raise` method. `None` never means "it failed" |
| Don't pass null | A parameter with a real default never defaults to `None`. A value that may be missing when it arrives from outside is checked once, at the edge: `_validate_file_name` takes `str \| None` and returns `str` |
| Write the try-catch-finally first | A `try/except` goes only where `nexttech-service-architecture` and `nexttech-errors-and-logging` put one: a repository method, a call to a provider, the undo of a half-finished write. Run the library on bad input before wrapping it |
| The three laws of TDD; a failing test for every change | `CLAUDE.md` says whether the project has tests. Where it has none, do not add `pytest` or a test unasked; a change is verified with steps 3 to 5 of "Before you push" in `nexttech-core-rules` |
| `camelCase` examples (`isPasswordValid`, `postPayment`) | `snake_case`, and the naming rules above |

## 6. Names that were asked about or renamed

All from `nexttech-assistant-svc`.

| Vague | Clear | What was wrong |
|---|---|---|
| `upsert` | `create_document` and `update_document` | Jargon, and two actions in one method |
| `list_jobs` beside `get_job` | `list_ingestion_jobs`, `get_ingestion_job` | Only the verb differed |
| `_mark_failed(document, chunks)` | `_delete_chunks_and_mark_document_failed` | Hid that it deletes points |
| `_save_as_pending`, then `_create_or_update_document_as_pending` | `_upload_new_document` and `_upload_new_version_of_document` | One helper created or updated the row, hashed the file and set the status |
| `compute_checksum` | `_compute_content_hash` | The column is `content_hash`, and only this module uses it |
| `split_into_chunks(text, document_id, path, file_name, access_level)` | `split_into_chunks(text, document)` | Five arguments that all came from the one document |
| `remove_document` beside `delete_document` | one `delete_document` | Two words for one action |
| `_resolve` | `_build_absolute_path` | Did not say what it returns |
| `SIZE_PROBE_EXTRA_BYTES` | `OVERSIZE_CHECK_EXTRA_BYTES` | "Probe" explained nothing |
| `TYPE_VALUES` | `OUTBOX_TYPE_SQL_VALUES` | Not a list, and of which type? |
| `value` | `metadata_value` | Banned as a standalone name |
| `reader` | `cv_reading_service` | Too vague (#14) |
| `chunker`, `get_chunker` | `document_chunker`, `get_document_chunker` | Did not say what it chunks (#9 review) |
| `text` | `markdown_text` | Which text? (#9 review) |
