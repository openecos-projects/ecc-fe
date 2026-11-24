from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli import main as cli_main
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace
from fecompiler.runtime.recovery import recover_interrupted_operations
from fecompiler.utility.workspace_lock import (
    WorkspaceBusyError,
    workspace_is_busy,
    workspace_lock,
    workspace_lock_owner,
    workspace_lock_path,
)

_REPOSITORY = Path(__file__).resolve().parents[1]
_HOLDER = """
import sys
from fecompiler.utility.workspace_lock import workspace_lock

with workspace_lock(sys.argv[1]):
    print("locked", flush=True)
    sys.stdin.read()
"""


@contextmanager
def _held_by_another_process(directory: Path) -> Iterator[subprocess.Popen[str]]:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(directory)],
        cwd=_REPOSITORY,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        yield holder
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.wait()
        for stream in (holder.stdin, holder.stdout):
            if stream is not None:
                stream.close()


def _workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "workspace"
    create_workspace(
        CreateWorkspaceData(
            directory=str(directory),
            parameters={"Design": "demo", "Top module": "chip_top"},
        ),
    )
    return directory


def _mark_first_step_ongoing(directory: Path) -> Path:
    flow_path = directory / "home" / "flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["steps"][0]["state"] = "Ongoing"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    return flow_path


def _first_step_state(flow_path: Path) -> str:
    return json.loads(flow_path.read_text(encoding="utf-8"))["steps"][0]["state"]


def test_lock_file_sits_beside_the_workspace_and_is_reentrant(tmp_path) -> None:
    directory = tmp_path / "runs" / "default"
    directory.parent.mkdir()

    with workspace_lock(directory):
        with workspace_lock(str(directory) + "/"):
            assert workspace_is_busy(directory) is False
        assert workspace_lock_path(directory) == tmp_path / "runs" / ".default.lock"
        assert workspace_lock_path(directory).is_file()
        assert not directory.exists()

    with workspace_lock(directory):
        pass


def test_lock_held_by_another_process_reports_its_owner(tmp_path) -> None:
    directory = tmp_path / "workspace"

    with _held_by_another_process(directory) as holder:
        assert workspace_is_busy(directory) is True
        with pytest.raises(WorkspaceBusyError) as caught:
            with workspace_lock(directory):
                pytest.fail("entered a workspace owned by another process")

    assert caught.value.directory == directory.resolve()
    assert caught.value.owner["pid"] == holder.pid
    assert f"pid {holder.pid}" in str(caught.value)


def test_lock_is_released_when_its_owner_is_killed(tmp_path) -> None:
    directory = tmp_path / "workspace"

    with _held_by_another_process(directory) as holder:
        os.kill(holder.pid, signal.SIGKILL)
        holder.wait()
        assert workspace_is_busy(directory) is False


def test_lock_excludes_other_threads_of_the_same_process(tmp_path) -> None:
    directory = tmp_path / "workspace"
    outcome: list[str] = []

    def contender() -> None:
        try:
            with workspace_lock(directory):
                outcome.append("entered")
        except WorkspaceBusyError:
            outcome.append("busy")

    with workspace_lock(directory):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()

    assert outcome == ["busy"]


def test_lock_does_not_create_missing_parent_directories(tmp_path) -> None:
    directory = tmp_path / "absent" / "workspace"

    with workspace_lock(directory):
        pass

    assert not (tmp_path / "absent").exists()


def test_application_refuses_to_mutate_a_busy_workspace(tmp_path) -> None:
    directory = _workspace(tmp_path)
    flow_before = (directory / "home" / "flow.json").read_text(encoding="utf-8")

    with _held_by_another_process(directory) as holder:
        results = [
            workspace_application.execute_payload(command, {"directory": str(directory)})
            for command in ("reset-flow", "refresh-config", "run-flow")
        ]

    for result in results:
        assert result.response == "failed"
        assert result.data == {
            "directory": str(directory),
            "error": "workspace_busy",
            "owner_pid": holder.pid,
        }
    assert (directory / "home" / "flow.json").read_text(encoding="utf-8") == flow_before


def test_cli_run_reports_busy_workspace_without_touching_it(
    tmp_path, capsys, monkeypatch
) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    directory = tmp_path / "workspace"
    directory.mkdir()

    with _held_by_another_process(directory) as holder:
        exit_code = cli_main.run(["run", "--workspace", str(directory), "--json"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "records": [
            {
                "kind": "error",
                "error": "workspace_busy",
                "workspace": str(directory),
                "owner_pid": holder.pid,
            }
        ]
    }
    assert list(directory.iterdir()) == []


def test_load_recovers_running_steps_only_when_no_owner_is_alive(tmp_path) -> None:
    directory = _workspace(tmp_path)
    flow_path = _mark_first_step_ongoing(directory)

    with _held_by_another_process(directory):
        live = workspace_application.execute_payload(
            "load", {"directory": str(directory)}
        )
        assert live.data["recovered_stale_ongoing"] is False
        assert _first_step_state(flow_path) == "Ongoing"
        assert recover_interrupted_operations(
            directory, active_operation_ids=set()
        ) == {"recovered": []}
        assert _first_step_state(flow_path) == "Ongoing"

    dead = workspace_application.execute_payload("load", {"directory": str(directory)})
    assert dead.data["recovered_stale_ongoing"] is True
    assert _first_step_state(flow_path) == "Incomplete"


def test_owner_probe_is_read_only(tmp_path) -> None:
    directory = _workspace(tmp_path)

    assert workspace_lock_owner(directory) is None
    assert not workspace_lock_path(directory).exists()

    with _held_by_another_process(directory) as holder:
        assert workspace_lock_owner(directory)["pid"] == holder.pid


def _status(capsys, directory: Path) -> tuple[int, list[dict[str, object]]]:
    exit_code = cli_main.run(["status", "--workspace", str(directory), "--json"])
    return exit_code, json.loads(capsys.readouterr().out)["records"]


def test_status_tells_a_running_step_from_an_interrupted_one(
    tmp_path, capsys, monkeypatch
) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    directory = _workspace(tmp_path)
    flow_path = _mark_first_step_ongoing(directory)
    stored = flow_path.read_text(encoding="utf-8")

    with _held_by_another_process(directory) as holder:
        running_exit, running = _status(capsys, directory)
    interrupted_exit, interrupted = _status(capsys, directory)

    assert running_exit == 0
    assert running[0]["running"] is True
    assert running[0]["owner_pid"] == holder.pid
    assert "resume_cmd" not in running[0]
    assert running[1]["state"] == "ongoing"

    # An interrupted flow is flow state, not a failure of `status` itself.
    assert interrupted_exit == 0
    assert interrupted[0]["running"] is False
    assert "owner_pid" not in interrupted[0]
    assert interrupted[0]["resume_cmd"] == (
        f"ecc-fe run --resume --workspace {directory}"
    )
    assert interrupted[1]["state"] == "interrupted"
    assert flow_path.read_text(encoding="utf-8") == stored
