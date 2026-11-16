---
name: nexttech-errors-and-logging
description: How failures and logs are handled in a Nexttech service. Covers the single AppError with its AppErrorCode catalogue, the one error body, the exception handlers, when to add an error code and when to reuse one, where a try/except belongs, provider and database failures, cleanup helpers, log levels, logger.exception, the request id, and what must never be logged. Use it when raising or catching an error, adding an error code or a status, writing an exception handler, wrapping a library call, or adding or reviewing a log line.
---

# Nexttech errors and logging

About 25 of the 650 review comments on `nexttech-assistant-svc` are about errors. A source such as `#13` is a pull request of that repository.

`from <package>.exceptions import AppError, AppErrorCode`. The package is `exceptions/`: `app_error.py`, `error_codes.py`, `status_codes.py`.

## 1. One error, one body

- One exception: `AppError(AppErrorCode.X)`, never a bare exception (#4, #18; #7; #13). No `raise Exception`, no bare `ValueError` for a business rule.
- Each member is an `ErrorDetail(code, status, message)`: a stable UPPERCASE code, an `AppStatusCode`, and a short message that is safe to show. The raised error is on `exception.error`, and its parts on `AppErrorCode.X.value`.
- One body for every failure, `{code, status, message}` (`ErrorResponseModel`), assembled only in `build_error_response` in `api/exception_handlers.py`. No route builds error JSON, and our code never raises `HTTPException`.
- The handlers are registered in `api/app.py`. `app_error_handler` answers an `AppError` with its own code. `internal_server_error_handler` is registered for `Exception` and answers anything else with `INTERNAL_SERVER_ERROR`, with the details in the log only. `request_validation_error_handler` answers a `RequestValidationError` with `VALIDATION_ERROR` and logs only the location and type of each problem, never the input. There is no error middleware.
- A failure is raised, never returned. No function reports one with `False`, `None` or a status the caller has to remember to check. The one exception is a soft dependency at startup (`nexttech-service-architecture`).
- A failure after a stream has opened cannot change the status line. It is sent as the stream's `error` event, in the same shape.

## 2. The catalogue

- A feature adds the members its business rules need, and only for a failure the client must tell apart; otherwise it reuses one.
- Reuse a code before adding one: `DATABASE_ERROR` (#6, #8). Merge codes that mean the same thing (#8).
- A code is named for what failed, not for a system the project does not have (#8 on `HR_UNAVAILABLE`).
- A status the code needs and `AppStatusCode` lacks is added to the enum.
- A resource that is missing or that belongs to someone else answers 404, never 403: a 403 confirms it exists.

## 3. Where a try/except goes

- A repository method: every one is wrapped in `try/except SQLAlchemyError` and raises `AppError(AppErrorCode.DATABASE_ERROR) from error`. "Why aren't we catching SQLAlchemy exception in all repository methods?" (#6).
- A call to a provider. A provider module turns its own client's exceptions into an `AppError`, as a repository does for SQLAlchemy. A generic LangChain call made from a service is wrapped where it is made. A vector-store failure becomes `VECTOR_STORE_UNAVAILABLE` (503), not a bare 500 (#13), so the client gets an answer it can retry.
- An HTTP helper catches every client error and raises our own, so a caller handles one exception type (#10).
- The undo of a half-finished write. A cleanup helper (delete a file, remove points, mark a row failed) logs and swallows its own error, so it never hides the original one.
- Nowhere else. A route has no `try`. A service catches only our own error from the repository (#6).
- Where a caller catches one failure deliberately, it checks the error code and re-raises everything else.
- Before wrapping a library call, run the library on real bad input and see what it does. Docling returns a failed status for an empty, corrupt or unknown file instead of raising, so on `nexttech-assistant-svc` the status check, not an `except`, turns those into `DOCUMENT_NOT_EXTRACTABLE`.
- No hand-written error handling where the library already owns it.

## 4. What the client sees and what the log holds

- The raise site logs; the handler does not. The log carries the specific reason and the ids: which check failed, which id, which exception type.
- The response carries the member's message and nothing else. Never send `str(exc)` to a client.
- A validation error never echoes the rejected input.

## 5. Logging

- One logger per module: `logger = logging.getLogger(__name__)`.
- The root logger is configured once, by `setup_logging()` in the lifespan, with the level from settings.
- Every request has a uuid7 request id (`api/middleware/request_id.py`, `request_context.py`), returned in `X-Request-ID`, published through a `ContextVar` and stamped on every record with a filter on the handler. Outside a request it is `-`.
- In the worker, every line carries the job id and the work item id, so a failed row leads to the run that produced it.
- An error is logged as an error, not a warning (#1, #8, #8). An unsupported provider or a failed build is an error.
- `logger.exception` only with an exception in flight, inside an `except` (#1).
- Add `exc_info=True` on the branch for unexpected failures (#22).
- A failure path gets a log line (#1).
- No email, name or token in a log line or a queue payload. Log ids (#6; #10, #17). Never a password, a hash or a cookie value.
- No `print` (#1). Never ship a debug callback such as `StdOutCallbackHandler`.
- Tracing is Langfuse: `LangfuseClient.get_callback_handler()` returns a new handler per call, or `None` when tracing is off. Langfuse traces agent runs; background work involves no agent and is not there.
