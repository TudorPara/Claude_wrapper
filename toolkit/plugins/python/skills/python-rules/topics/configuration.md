## 5. Configuration

- One pydantic-settings `Settings` composed of nested `BaseModel` groups, read with the `__` env delimiter (`DATABASE__URL`, `LLM__PROVIDER`). A group's env prefix is its field name on `Settings`, uppercased.
- **No defaults** for configurable behaviour: every value comes from the environment, or the application refuses to start, with the failure naming the key. Never at first use.
- Constrain every field with `Annotated[..., Field(ge=...)]` against a named minimum constant.
- Provider settings are discriminated unions: one `BaseModel` per provider with `provider: Literal[<Enum>.<MEMBER>]`, combined as `Annotated[A | B, Field(discriminator=...)]`. One provider `StrEnum` per kind; secrets as `SecretStr`. Switching provider is a settings change only.
- Connection URLs arrive whole from the environment, never assembled in code. Env names stay vendor-neutral.
- `get_settings()` is `lru_cache`d. Call it inside the function or method that needs it, at the moment the object using it is created, lifespan included. Never at module level: that freezes values before any override, validates the environment as an import side effect and makes import order load-bearing. An object built from settings (a hasher, a client) is built lazily too.
- Settings validators raise the project's own error: `logger.error` the offending values, then `raise AppException(AppErrorCode.INVALID_CONFIGURATION)`. Never a bare `ValueError` there.
- `.env.example` mirrors every key of `.env` with placeholders for secrets; `.env` stays gitignored. One key per line, and the file ends with a newline. A new setting touches three places: `config.py`, `.env.example`, `.env`.

## Examples

Snippets carry no comments, as the rules require.

**Generic configuration**
Bad: a constant a coworker can only change by editing the service.
```python
MAX_ROWS = 100
```
Good: the cap comes from settings, read when the method runs.
```python
pagination_settings = get_settings().pagination
page_size = min(
    page_size or pagination_settings.default_page_size,
    pagination_settings.max_page_size,
)
```

**Settings read where the object is created**
Bad: bound at import time.
```python
settings = get_settings()
```
Good: the lifespan reads the group when it builds the object.
```python
app.state.llm = setup_llm(get_settings().llm)
```
