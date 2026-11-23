from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fecompiler.tools.common import process as tool_process
from fecompiler.tools.common.process import TIMEOUT_RETURNCODE, run_tool, tool_timeout

_REPOSITORY = Path(__file__).resolve().parents[1]


def _python(code: str, *args: str) -> list[str]:
    return [sys.executable, "-c", code, *args]


def _alive(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return status.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_until(condition, *, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _read_pid(path: Path) -> int:
    assert _wait_until(lambda: path.is_file() and path.read_text().strip() != "")
    return int(path.read_text())


# A tool that starts a long-lived child, records its pid, and then blocks.
_SPAWNS_CHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open(sys.argv[1], "w").write(str(child.pid))
print("started", flush=True)
time.sleep(600)
"""


def test_output_is_interleaved_returned_and_written_to_the_log(tmp_path) -> None:
    log_path = tmp_path / "report" / "log.txt"
    code = (
        "import sys\n"
        "print('to stdout', flush=True)\n"
        "print('to stderr', file=sys.stderr, flush=True)\n"
        "sys.exit(3)\n"
    )

    result = run_tool(_python(code), log_path=log_path)

    assert result.returncode == 3
    assert result.timed_out is False
    assert result.output == "to stdout\nto stderr\n"
    assert log_path.read_text(encoding="utf-8") == result.output


def test_log_can_be_followed_while_the_tool_is_still_running(tmp_path) -> None:
    log_path = tmp_path / "log.txt"
    release = tmp_path / "release"
    code = (
        "import pathlib, sys, time\n"
        "print('first line', flush=True)\n"
        "while not pathlib.Path(sys.argv[1]).exists():\n"
        "    time.sleep(0.02)\n"
        "print('second line', flush=True)\n"
    )
    seen_while_running: list[str] = []

    def follow() -> None:
        _wait_until(lambda: log_path.is_file() and "first line" in log_path.read_text())
        seen_while_running.append(log_path.read_text(encoding="utf-8"))
        release.write_text("go", encoding="utf-8")

    follower = threading.Thread(target=follow)
    follower.start()
    result = run_tool(_python(code, str(release)), log_path=log_path)
    follower.join()

    assert seen_while_running == ["first line\n"]
    assert result.output == "first line\nsecond line\n"


def test_tool_does_not_inherit_stdin_and_uses_the_given_environment(tmp_path) -> None:
    code = "import os, sys; print(repr(sys.stdin.read()), os.environ['MARKER'])"

    result = run_tool(
        _python(code), cwd=tmp_path, env={**os.environ, "MARKER": "from-caller"}
    )

    assert result.output == "'' from-caller\n"


def test_timeout_terminates_the_whole_process_group(tmp_path) -> None:
    pid_file = tmp_path / "child.pid"
    log_path = tmp_path / "log.txt"

    result = run_tool(
        _python(_SPAWNS_CHILD, str(pid_file)), log_path=log_path, timeout=1.0
    )

    assert result.timed_out is True
    assert result.returncode == TIMEOUT_RETURNCODE
    assert result.output.startswith("started\n")
    assert "exceeded the 1s wall-clock limit" in result.output
    assert log_path.read_text(encoding="utf-8") == result.output
    assert _wait_until(lambda: not _alive(int(pid_file.read_text())))


def test_interruption_terminates_the_process_group_and_propagates(
    tmp_path, monkeypatch
) -> None:
    pid_file = tmp_path / "child.pid"
    original_wait = subprocess.Popen.wait
    interrupted = False

    def interrupt_first_wait(self, timeout=None):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            _read_pid(pid_file)
            raise KeyboardInterrupt
        return original_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_first_wait)

    with pytest.raises(KeyboardInterrupt):
        run_tool(_python(_SPAWNS_CHILD, str(pid_file)), session_dir=tmp_path)

    assert _wait_until(lambda: not _alive(int(pid_file.read_text())))
    assert not (tmp_path / ".tool-session").exists()


def test_tools_abandoned_by_a_killed_command_are_stopped_before_the_next_run(
    tmp_path,
) -> None:
    pid_file = tmp_path / "child.pid"
    tool_pid_file = tmp_path / "tool.pid"
    tool = (
        "import os, sys\n"
        "open(sys.argv[2], 'w').write(str(os.getpid()))\n" + _SPAWNS_CHILD
    )
    command = (
        "import sys\n"
        "from fecompiler.tools.common.process import run_tool\n"
        "run_tool([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]],"
        " session_dir=sys.argv[4])\n"
    )
    killed_command = subprocess.Popen(
        _python(command, tool, str(pid_file), str(tool_pid_file), str(tmp_path)),
        cwd=_REPOSITORY,
    )
    child_pid = _read_pid(pid_file)
    tool_pid = _read_pid(tool_pid_file)
    os.kill(killed_command.pid, signal.SIGKILL)
    killed_command.wait()
    assert _alive(tool_pid) and _alive(child_pid)

    result = run_tool(_python("print('next run')"), session_dir=tmp_path)

    assert result.output == "next run\n"
    assert _wait_until(lambda: not _alive(tool_pid) and not _alive(child_pid))
    assert not (tmp_path / ".tool-session").exists()


def test_missing_tool_raises_and_leaves_no_session_record(tmp_path) -> None:
    with pytest.raises(OSError):
        run_tool([str(tmp_path / "no-such-tool")], session_dir=tmp_path)

    assert not (tmp_path / ".tool-session").exists()


def test_huge_output_keeps_head_and_tail_in_memory_and_everything_on_disk(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(tool_process, "_CAPTURE_LIMIT_BYTES", 40)
    log_path = tmp_path / "log.txt"
    code = "import sys; sys.stdout.write('H' * 20 + 'x' * 1000 + 'T' * 20)"

    result = run_tool(_python(code), log_path=log_path)

    assert result.output == (
        "H" * 20 + "\n[ecc-fe] 1000 bytes of output omitted; see the log file\n" + "T" * 20
    )
    assert log_path.stat().st_size == 1040


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 1800.0), ("90", 90.0), ("0", None), ("-5", 1800.0), ("soon", 1800.0)],
)
def test_timeout_limit_can_be_overridden_or_disabled(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("ECOS_FE_TIMEOUT_LINT", raising=False)
    else:
        monkeypatch.setenv("ECOS_FE_TIMEOUT_LINT", value)

    assert tool_timeout("lint") == expected
