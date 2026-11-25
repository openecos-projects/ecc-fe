"""Settings of the CPU sim step that change how it runs."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fecompiler.engine.provenance import build_step_provenance
from fecompiler.tools.verilator import cases as verilator_cases
from fecompiler.tools.verilator import parallel as verilator_parallel
from fecompiler.tools.verilator import regression as verilator_regression


def _wave(tmp_path: Path, args: list[str], *, name: str = "add.soc", dump: bool = True):
    return verilator_cases._apply_case_wave_arg(
        name, f"/images/{name}.bin", args, tmp_path / "wave.vcd", dump=dump
    )


def test_a_case_dumps_a_waveform_by_default(tmp_path) -> None:
    args, wave = _wave(tmp_path, ["--max-cycles", "10"])

    assert args == ["--max-cycles", "10", "--wave", str(tmp_path / "wave.vcd")]
    assert wave == str(tmp_path / "wave.vcd")


def test_no_waveform_is_dumped_when_switched_off(tmp_path) -> None:
    assert _wave(tmp_path, ["--max-cycles", "10"], dump=False) == (["--max-cycles", "10"], "")


def test_a_waveform_named_in_the_run_arguments_is_always_kept(tmp_path) -> None:
    explicit = str(tmp_path / "mine.vcd")

    args, wave = _wave(tmp_path, ["--wave", explicit], dump=False)

    assert (args, wave) == (["--wave", explicit], explicit)


def test_coremark_dumps_no_waveform(tmp_path) -> None:
    assert _wave(tmp_path, [], name="coremark.soc") == ([], "")


def test_only_the_newest_runs_are_kept(tmp_path) -> None:
    for name in ("20260101_000000_000001", "20260102_000000_000001", "20260103_000000_000001"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "cases.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a run", encoding="utf-8")

    verilator_regression._prune_run_history(tmp_path, 2)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "20260102_000000_000001",
        "20260103_000000_000001",
        "notes.txt",
    ]


@pytest.mark.parametrize("jobs", [1, 4])
def test_results_keep_the_order_of_the_items(jobs) -> None:
    release = threading.Event()

    def work(item: int) -> int:
        # The first item finishes last when the others are allowed to overtake.
        if item == 0 and jobs > 1:
            release.wait(timeout=5)
        elif item == 3:
            release.set()
        return item * 10

    assert verilator_parallel._map_in_order([0, 1, 2, 3], work, jobs, lambda item: Path(str(item))) == [
        0,
        10,
        20,
        30,
    ]


def test_tools_still_running_are_stopped_when_one_item_fails(monkeypatch, tmp_path) -> None:
    stopped: list[Path] = []
    monkeypatch.setattr(verilator_parallel, "stop_session", stopped.append)

    def work(item: str) -> str:
        if item == "b":
            raise RuntimeError("build failed")
        return item

    with pytest.raises(RuntimeError, match="build failed"):
        verilator_parallel._map_in_order(["a", "b", "c"], work, 2, lambda item: tmp_path / item)

    assert stopped == [tmp_path / "a", tmp_path / "b", tmp_path / "c"]


def _signature(tmp_path: Path, **parameters) -> str:
    workspace = {"directory": str(tmp_path), "design": "demo", **parameters}
    return build_step_provenance(workspace, "sim", "verilator", None)["signature"]


def test_how_the_step_runs_is_not_part_of_its_signature(tmp_path) -> None:
    baseline = _signature(tmp_path)

    # More jobs, a shorter history or reusing a program give the same result.
    assert _signature(tmp_path, sim_build_jobs=4) == baseline
    assert _signature(tmp_path, sim_case_jobs=8) == baseline
    assert _signature(tmp_path, sim_history_limit=3) == baseline
    assert _signature(tmp_path, sim_reuse_binary=True) == baseline
    # Whether waveforms exist is part of what the step produces.
    assert _signature(tmp_path, sim_dump_waveform=False) != baseline
