"""Run one external tool for a flow step.

Every tool invocation goes through :func:`run_tool`, which gives all steps the
same guarantees:

* the tool and everything it spawns live in their own process group, which is
  terminated as a whole on a timeout or when the command is interrupted;
* output goes straight to a file, so a running step can be followed with
  ``tail -f`` and a noisy simulation cannot exhaust memory;
* a wall-clock limit bounds every call;
* tool processes abandoned by a killed command are found and stopped before
  the same step runs again.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

_SESSION_ENV = "ECOS_FE_TOOL_SESSION"
_SESSION_FILE = ".tool-session"
_TERMINATE_GRACE_SECONDS = 5.0
_CAPTURE_LIMIT_BYTES = 32 * 1024 * 1024
TIMEOUT_RETURNCODE = 124

# Generous wall-clock limits: they exist to end a hung tool, not to police a
# slow one. ECOS_FE_TIMEOUT_<KIND> overrides one limit in seconds; 0 disables it.
_DEFAULT_TIMEOUT_SECONDS = {
    "elab": 30 * 60,
    "lint": 30 * 60,
    "sim_compile": 2 * 60 * 60,
    "program_build": 30 * 60,
    "sim_case": 4 * 60 * 60,
}


@dataclass(frozen=True, slots=True)
class ToolResult:
    returncode: int
    output: str
    timed_out: bool = False


def tool_timeout(kind: str) -> float | None:
    """Return the wall-clock limit for one kind of tool call, or None for no limit."""
    default = _DEFAULT_TIMEOUT_SECONDS[kind]
    raw = os.environ.get(f"ECOS_FE_TIMEOUT_{kind.upper()}", "").strip()
    if not raw:
        return float(default)
    try:
        seconds = float(raw)
    except ValueError:
        return float(default)
    if seconds < 0:
        return float(default)
    return seconds or None


def run_tool(
    cmd: Sequence[str],
    *,
    log_path: str | Path | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    session_dir: str | Path | None = None,
) -> ToolResult:
    """Run *cmd* to completion and return its exit status and combined output.

    stdout and stderr are interleaved into *log_path* as the tool writes them
    (a private temporary file when no path is given). On a timeout the whole
    process group is terminated, a notice is appended to the output, and the
    result carries ``timed_out`` and :data:`TIMEOUT_RETURNCODE`.

    *session_dir* is a directory owned by the calling step. When given, tool
    processes left behind there by a command that was killed are stopped first.

    Raises ``OSError`` when the tool cannot be started.
    """
    session_file = Path(session_dir) / _SESSION_FILE if session_dir else None
    if session_file is not None:
        _stop_abandoned_session(session_file)
    token = uuid.uuid4().hex
    child_env = dict(os.environ if env is None else env)
    child_env[_SESSION_ENV] = token

    with _open_output(log_path) as stream:
        if session_file is not None:
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text(token, encoding="utf-8")
        try:
            process = subprocess.Popen(
                list(cmd),
                cwd=cwd,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            if session_file is not None:
                session_file.unlink(missing_ok=True)
            raise
        timed_out = False
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_group(process)
        except BaseException:
            _terminate_group(process)
            raise
        finally:
            if session_file is not None and process.poll() is not None:
                session_file.unlink(missing_ok=True)
        if timed_out:
            stream.write(
                f"\n[ecc-fe] {Path(cmd[0]).name} exceeded the {timeout:g}s wall-clock "
                "limit; its process group was terminated\n".encode()
            )
        output = _read_bounded(stream)

    returncode = TIMEOUT_RETURNCODE if timed_out else int(process.returncode)
    return ToolResult(returncode=returncode, output=output, timed_out=timed_out)


def _open_output(log_path: str | Path | None) -> IO[bytes]:
    if log_path is None:
        return tempfile.TemporaryFile()
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w+b")


def _read_bounded(stream: IO[bytes]) -> str:
    """Return the captured output, keeping only its head and tail when huge."""
    stream.flush()
    size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size <= _CAPTURE_LIMIT_BYTES:
        return stream.read().decode("utf-8", errors="replace")
    half = _CAPTURE_LIMIT_BYTES // 2
    head = stream.read(half)
    stream.seek(size - half)
    tail = stream.read(half)
    omitted = size - 2 * half
    return (
        head.decode("utf-8", errors="replace")
        + f"\n[ecc-fe] {omitted} bytes of output omitted; see the log file\n"
        + tail.decode("utf-8", errors="replace")
    )


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the tool and every process it started: SIGTERM, then SIGKILL."""
    _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    # Descendants may ignore SIGTERM or outlive the tool that started them.
    _signal_group(process.pid, signal.SIGKILL)
    process.wait()


def _signal_group(group_id: int, signum: int) -> None:
    try:
        os.killpg(group_id, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_abandoned_session(session_file: Path) -> None:
    """Kill tool processes that outlived the command that started them.

    A command killed with SIGKILL cannot clean up, so its tool keeps running
    and would write into the step directory alongside the next run. Each tool
    process tree carries a unique token in its environment; the token recorded
    for the previous run identifies exactly those processes, with no risk of
    hitting an unrelated process that reused a pid.
    """
    try:
        token = session_file.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if token:
        marker = f"{_SESSION_ENV}={token}".encode()
        for pid in _process_ids():
            try:
                environ = Path(f"/proc/{pid}/environ").read_bytes()
            except OSError:
                continue
            if marker in environ.split(b"\0"):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    session_file.unlink(missing_ok=True)


def _process_ids() -> list[int]:
    try:
        names = os.listdir("/proc")
    except OSError:  # no procfs on this platform
        return []
    return [int(name) for name in names if name.isdigit() and int(name) != os.getpid()]


__all__ = ["TIMEOUT_RETURNCODE", "ToolResult", "run_tool", "tool_timeout"]
