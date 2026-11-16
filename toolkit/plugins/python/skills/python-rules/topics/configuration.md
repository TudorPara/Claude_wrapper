---
name: nexttech-configuration
description: How configuration works in a Nexttech service. Covers the single pydantic-settings Settings with nested groups and the double-underscore delimiter, what must be a setting and what must not, provider groups as discriminated unions, provider-neutral env keys, where get_settings may be called, settings validators, secrets, and keeping .env.example in step. Use it when adding, renaming or removing a setting or an env key, touching config.py or .env.example, wiring a provider, or deciding whether a value belongs in settings, in a constant or in code.
---

# Nexttech configuration

Hardcoded values are the fifth thing reviewers flag most (about 30 of the 650 review comments on `nexttech-assistant-svc`): "This should come from env." (#1). A source such as `#13` is a pull request of that repository.

## 1. What is a setting

- A value someone may change comes from the environment, never from code (#1; #6; #13): a provider, a model, a limit, a path, a cadence, a threshold.
- Every number the docs give as a starting point is a setting: cadences, intervals, batch sizes, timeouts, thresholds, token lifetimes, retry ceilings.
- A fixed number is a named constant next to its use (#6, #10; #7).
- A setting with only one value the code can use is not a setting. On `nexttech-assistant-svc`, `PARSER__EXPORT_FORMAT` was removed because the chunker needs Markdown.
- A library default that changes cost or behaviour is set from a setting, not left to the library. Docling runs OCR by default, which took a 25-page PDF from 15 to 95 seconds for 30 extra characters: `PARSER__OCR_ENABLED` decides it. The Qdrant client waits 5 seconds by default: `VECTOR_STORE__TIMEOUT_SECONDS` decides it.
- The reverse also holds: a choice with one intended value is written in the code, or left to the library's default, and is not made a setting.

## 2. The Settings class

- One `Settings` in `configuration/config.py`, made of nested `BaseModel` groups and read with the `__` delimiter (`DATABASE__URL`, `LLM__PROVIDER`). A group's env prefix is its field name on `Settings`, uppercased.
- Env keys match the field names exactly, with no `SETTINGS` in the prefix: `TICKET_AGENT__*` (#10; #1).
- Group names say what they configure: `LoggingSettings`, `SMTPSettings` (#1, #17).
- A new setting has no default: a missing key stops startup with an error naming it. Do not add a default, and do not remove an older field's default inside an unrelated PR.
- Constrain a field with its type: `PositiveInt`, `NonNegativeInt`, `Annotated[..., Field(ge=...)]`. A score is `ge=0, le=1`, a probability too (#13; #7).
- A new secret field is a `SecretStr`.
- A connection URL arrives whole from the environment (`DATABASE__URL`), never assembled in code.
- `load_dotenv` is called once, in `config.py` (#10, #13).

## 3. Providers

- A provider group is a discriminated union: one `BaseModel` per provider with `provider: Literal[<Enum>.<MEMBER>]`. Switching provider is a settings change and nothing else.
- Provider enums live in `models/`, one file per kind (`llm.py`, `embeddings.py`, `vector_store.py`), and are imported from there, not through `config` (#1). Their values are lowercase, the ones the environment uses.
- Settings are provider-neutral (`EMBEDDINGS__MODEL_NAME`, `LLM__API_KEY`). A field every provider needs is not declared on one provider's class only (#1): declare it on each, so the key is never silently ignored.
- A builder is `setup_<provider>_<thing>`, not `get_...` (#1). A factory receives its settings group (#1).

## 4. Reading settings

- `get_settings()` is cached. Call it at the moment the object using it is built: the lifespan reads the settings and hands each group to what it builds.
- Never at module level, which freezes values before any override and makes importing a module validate the environment.
- A factory never reads the settings itself.

Bad: `settings = get_settings()` at the top of a factory module. Good, from `api/lifespan.py`:

```python
embedding = EmbeddingFactory.create_embedding(settings.embeddings)
```

Bad: `chunk_size=1000` written inside the chunker, which a teammate can change only by editing code. Good:

```python
self._recursive_splitter = RecursiveCharacterTextSplitter.from_language(
    Language.MARKDOWN,
    chunk_size=settings.chunk_size,
    chunk_overlap=settings.chunk_overlap,
)
```

## 5. Validation and failures

- A settings validator logs the offending values with `logger.error`, then raises our own `AppError`. Never a bare `ValueError` there (#18).
- Do not write a validator for a check the library already makes when the object is built. Run the library first and see.
- A configuration failure, such as an unsupported provider, raises `AppError(AppErrorCode.INTERNAL_SERVER_ERROR)` after one `logger.error`. It is an error, not a warning.

```python
case _:
    logger.error(
        "Failed to create chat model for provider=%s", llm_settings.provider
    )
    raise AppError(AppErrorCode.INTERNAL_SERVER_ERROR)
```

## 6. The env files

- `.env.example` mirrors every key of `.env`, with the exact name the settings read, an empty value, no comment, one key per line and a final newline (#13; #6).
- A new setting touches `config.py`, `.env.example` and `.env`.
- Never ask for `.env` and never print its values; `.env.example` is enough. There are two such files, `.env` and `dev/.env`, and a repository-wide search prints both: exclude them (`grep --exclude='.env'`). Read a setting through `get_settings()` and print only what is not a secret.
