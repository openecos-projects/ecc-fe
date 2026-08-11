"""Tests for the compatibility CLI's custom CPU options."""

from argparse import Namespace

from fecompiler.cli import main as cli_main


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "workspace": "",
        "design": "ysyx_example",
        "top": "ecos_sim_top",
        "clock": "clock",
        "freq": 100.0,
        "rtl": "",
        "filelist": "",
        "cpu_filelist": "examples/ysyx_00000000/filelist.cpu.f",
        "cpu_top_module": "ysyx_00000000",
        "soc_filelist": "fecompiler/thirdparty/SoC/filelist.soc.f",
        "testbench": "fecompiler/thirdparty/SoC/driver/main.cpp",
        "sim_cpp": [],
        "sim_cflag": [],
        "sim_ldflag": [],
        "sim_arg": ["--max-cycles", "50000000"],
        "sim_image": [],
        "sim_all_tests": False,
        "sim_tests_dir": "",
        "sim_build_all_programs": False,
        "sim_program": ["add"],
        "sim_programs_dir": "fecompiler/thirdparty/SoC/tests/programs",
        "sim_tests_out_dir": "",
        "sim_compile_march": "rv32i_zicsr",
        "sim_compile_mabi": "ilp32",
        "sim_compile_opt_level": "-O2",
        "sim_only": False,
        "sim_reuse_binary": False,
        "rerun": True,
    }
    values.update(overrides)
    return Namespace(**values)


def test_legacy_cli_persists_custom_cpu_top_and_isa(monkeypatch, tmp_path) -> None:
    captured = None

    def fake_create_workspace(spec):
        nonlocal captured
        captured = spec
        return {"directory": spec.directory}

    monkeypatch.setattr(cli_main, "create_workspace", fake_create_workspace)

    result = cli_main._create_workspace(_args(), str(tmp_path / "workspace"), [])

    assert result is not None
    assert captured is not None
    assert captured.parameters["frontend_core_id"] == "custom-filelist"
    assert captured.parameters["required_cpu_top_module"] == "ysyx_00000000"
    assert captured.cpu_supports_difftest is True
    assert captured.sim_compile_march == "rv32i_zicsr"
    assert captured.sim_compile_mabi == "ilp32"
    assert captured.sim_compile_opt_level == "-O2"


def test_legacy_cli_runtime_overrides_preserve_difftest_capability() -> None:
    updates = cli_main._runtime_overrides(
        _args(cpu_filelist=""),
        [],
        {"cpu_supports_difftest": False, "soc_supports_difftest": True},
    )

    assert updates["cpu_supports_difftest"] is False
    assert updates["soc_supports_difftest"] is True


def test_legacy_cli_keeps_unadapted_custom_filelist_difftest_disabled(
    monkeypatch,
    tmp_path,
) -> None:
    cpu_rtl = tmp_path / "cpu_top.sv"
    cpu_rtl.write_text("module cpu_top; endmodule\n", encoding="utf-8")
    cpu_filelist = tmp_path / "filelist.cpu.f"
    cpu_filelist.write_text("cpu_top.sv\n", encoding="utf-8")
    captured = None

    def fake_create_workspace(spec):
        nonlocal captured
        captured = spec
        return {"directory": spec.directory}

    monkeypatch.setattr(cli_main, "create_workspace", fake_create_workspace)

    cli_main._create_workspace(
        _args(cpu_filelist=str(cpu_filelist), cpu_top_module="cpu_top"),
        str(tmp_path / "workspace"),
        [],
    )

    assert captured is not None
    assert captured.cpu_supports_difftest is False
