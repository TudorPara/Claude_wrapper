## 8. Errors

- Business-rule and configuration failures use one exception, `AppException(AppErrorCode.X)`. `AppErrorCode` is an `Enum` whose members carry the stable wire code, a stdlib `HTTPStatus` and the client-facing message. One global handler renders it; no route builds error JSON.
- Add an error code only for a failure the client must tell apart; otherwise reuse one. A resource that is missing or owned by someone else is `RESOURCE_NOT_FOUND` (404, not 403).
- Configuration failures raise `INVALID_CONFIGURATION`: settings validators and every factory's `case _`.
- Framework errors keep their own handlers and stable codes (`validation_error`, `http_error`, `internal_error`); they are not enum members.
- Every error response has the same shape, `{"detail", "code"}` plus `errors` for validation, and carries the request id header.
- Never send `str(exc)` to a client. The handler owns the client-facing message; the raise site owns the log line.
- Never echo a rejected value in a validation error; report field and message only.
- Derive what is derivable: `WWW-Authenticate: Bearer` is emitted from the 401 status, not declared per error.
- Where a caller catches one failure deliberately, check the code and re-raise everything else.
- Cleanup helpers (delete a file, remove chunks, mark a row failed) log and swallow their own errors so they never hide the original one.
- Agent tool arguments are the exception to `AppException`: see section 13.

## 9. Logging

- Configure the root logger once in the composition root, with level and format from settings.
- Assign each request an id, or reuse an inbound `X-Request-ID`; publish it through a `ContextVar`, stamp it on every record with a logging filter, and echo it in the response header, error responses included.
- One line per request with method, path, status and duration.
- The log carries the specific reason (which check failed, which identifier, which exception type); the response carries one generic message per error code.
- Log ids, never personal data and never the credential itself. Identify a refresh session by its `jti`.
- Never ship debug callbacks such as `StdOutCallbackHandler`: they print every chain's inputs and outputs, including user data.

## Examples

Snippets carry no comments, as the rules require.

**One enum for business errors**
```python
class AppErrorCode(Enum):
    INVALID_CREDENTIALS = ("invalid_credentials", HTTPStatus.UNAUTHORIZED, "Could not validate credentials.")

    def __init__(self, code: str, status: HTTPStatus, message: str) -> None:
        self.code = code
        self.status = status
        self.message = message
```
```python
raise AppException(AppErrorCode.RESOURCE_NOT_FOUND)
```
