"""Run a child process in its own process group, so a time limit, Ctrl+C or Stop also ends everything it started."""

import contextlib
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from cw.errors import AppErrorCode, AppException

SECONDS_PER_MINUTE = 60


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process and its children if it is still running."""
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def run_process(
    command: list[str] | str,
    cwd: Path,
    environment: dict[str, str],
    timeout_minutes: int,
    stdin_text: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> tuple[int, str, str]:
    """Run a command (a list, or a string for the shell) and return its exit code, stdout and stderr.

    Raises:
        AppException: TIMED_OUT when the command runs longer than the limit.
    """
    timed_out = threading.Event()
    lines: list[str] = []
    with (
        tempfile.TemporaryFile("w+") as errors,
        subprocess.Popen(
            command,
            shell=isinstance(command, str),
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            start_new_session=True,
        ) as process,
    ):

        def expire() -> None:
            timed_out.set()
            _kill_group(process)

        timer = threading.Timer(timeout_minutes * SECONDS_PER_MINUTE, expire)
        timer.start()
        try:
            if stdin_text is not None:
                with contextlib.suppress(BrokenPipeError):
                    process.stdin.write(stdin_text)
                    process.stdin.close()
            for line in process.stdout:
                lines.append(line)
                if on_line:
                    on_line(line)
            process.wait()
        finally:
            timer.cancel()
            _kill_group(process)
        errors.seek(0)
        stderr = errors.read()
    if timed_out.is_set():
        raise AppException(AppErrorCode.TIMED_OUT, f"Stopped after {timeout_minutes} minutes.")
    return process.returncode, "".join(lines), stderr
