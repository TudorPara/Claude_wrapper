"""The single error type cw raises, with one code per failure the user must tell apart."""

from enum import Enum


class AppErrorCode(Enum):
    """Failure codes with the message shown to the user."""

    INVALID_CONFIGURATION = ("invalid_configuration", "The cw configuration is missing or invalid.")
    PROJECT_NOT_FOUND = ("project_not_found", "No project with that name is registered.")
    TICKET_NOT_FOUND = ("ticket_not_found", "No ticket with that id exists.")
    INVALID_STATE = ("invalid_state", "The ticket is not in a state that allows this step.")
    TICKET_BUSY = ("ticket_busy", "Another cw process is already working on this ticket.")
    GIT_FAILED = ("git_failed", "A git command failed.")
    COMMAND_FAILED = ("command_failed", "A project command failed.")
    TIMED_OUT = ("timed_out", "A command or agent run took longer than its time limit.")
    AGENT_FAILED = ("agent_failed", "A Claude Code agent run failed.")
    SESSION_LOST = ("session_lost", "The agent's earlier session no longer exists.")
    CONTEXT_INDEX_FAILED = ("context_index_failed", "The context index or the embedding server failed.")
    USAGE_LIMIT = ("usage_limit", "The Claude usage limit was reached; resume the ticket later.")
    CHECKS_FAILED = ("checks_failed", "Lint or tests still fail after the allowed fix rounds.")
    REVIEW_NOT_APPROVED = ("review_not_approved", "The reviewer still requests changes after the allowed rounds.")
    MERGE_CONFLICT = ("merge_conflict", "Conflicts with the base branch could not be resolved.")
    GIT_HOST_FAILED = ("git_host_failed", "The code host (GitHub or Bitbucket) request failed.")

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


class AppException(Exception):
    """Raised for every business-rule, configuration or integration failure."""

    def __init__(self, error_code: AppErrorCode, detail: str = "", usage: object | None = None) -> None:
        self.error_code = error_code
        self.detail = detail
        self.usage = usage
        super().__init__(f"{error_code.message} {detail}".strip())
