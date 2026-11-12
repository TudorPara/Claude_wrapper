"""Pick the code host client from the project's settings."""

import logging
from pathlib import Path
from typing import NoReturn

from cw.config import GitHostProvider, Project, Settings
from cw.errors import AppErrorCode, AppException
from cw.git import remote_url
from cw.git_hosts.base import GitHost
from cw.git_hosts.bitbucket import BitbucketHost
from cw.git_hosts.github import GitHubHost

logger = logging.getLogger(__name__)


def _reject_unsupported_provider(provider: str) -> NoReturn:
    """Log and raise for a code host cw does not support."""
    logger.error("Unsupported git host provider: %s", provider)
    raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Unsupported git host {provider}.")


def setup_git_host(settings: Settings, project: Project, checkout: Path) -> GitHost:
    """Return the client for the project's code host."""
    match project.git_host:
        case GitHostProvider.GITHUB:
            logger.info("Using GitHub (gh) for %s.", project.name)
            return GitHubHost(checkout)
        case GitHostProvider.BITBUCKET:
            logger.info("Using Bitbucket Cloud for %s.", project.name)
            if settings.bitbucket is None:
                logger.error("BITBUCKET__EMAIL and BITBUCKET__API_TOKEN are not set.")
                raise AppException(AppErrorCode.INVALID_CONFIGURATION, "Set BITBUCKET__EMAIL and BITBUCKET__API_TOKEN.")
            return BitbucketHost(remote_url(project.path, project.remote), settings.bitbucket)
        case _:
            _reject_unsupported_provider(project.git_host)
