"""Prepare reads the whole filelist and judges the top module correctly."""

from __future__ import annotations

from pathlib import Path

import pytest

from fecompiler.tools.common.filelist import parse_filelist
from fecompiler.tools.common.rtl_inputs import defines, incdirs, rtl_files
from fecompiler.tools.common.sv_module import (
    compare_port_contracts,
    macro_names,
    module_definitions,
    module_port_contract,
    strip_sv_comments,
)
from fecompiler.tools.prepare.runner import PrepareStep


def _write(path: Path, text: str = "module m; endmodule\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- filelist -------------------------------------------------------------


def test_option_forms_of_incdir_and_define(tmp_path) -> None:
    for name in ("a", "b", "c", "d"):
        (tmp_path / name).mkdir()
    top = _write(tmp_path / "top.sv")
    filelist = _write(
        tmp_path / "design.f",
        "+incdir+a\n-I b\n-Ic\n-incdir d\n+define+P\n-D Q=1\n-DR\ntop.sv\n",
    )

    parsed = parse_filelist(filelist)

    assert parsed.incdirs == tuple(tmp_path / name for name in ("a", "b", "c", "d"))
    assert parsed.defines == ("P", "Q=1", "R")
    assert parsed.files == (top,)
    assert parsed.unsupported == ()
    assert parsed.missing_incdirs == ()


def test_trailing_slash_comment_does_not_add_files(tmp_path) -> None:
    a = _write(tmp_path / "a.v")
    _write(tmp_path / "b.v")
    (tmp_path / "inc").mkdir()
    filelist = _write(tmp_path / "design.f", "a.v // see b.v\n+incdir+inc // headers\n")

    parsed = parse_filelist(filelist)

    assert parsed.files == (a,)
    assert parsed.incdirs == (tmp_path / "inc",)
    assert parsed.unsupported == ()


@pytest.mark.parametrize("spelling", ["$RTL_ROOT", "${RTL_ROOT}", "$(RTL_ROOT)"])
def test_environment_variables_are_expanded(tmp_path, monkeypatch, spelling) -> None:
    source = _write(tmp_path / "rtl" / "core.sv")
    (tmp_path / "rtl" / "inc").mkdir()
    monkeypatch.setenv("RTL_ROOT", str(tmp_path / "rtl"))
    filelist = _write(
        tmp_path / "work" / "design.f", f"{spelling}/core.sv\n+incdir+{spelling}/inc\n"
    )

    parsed = parse_filelist(filelist)

    assert parsed.files == (source,)
    assert parsed.incdirs == (tmp_path / "rtl" / "inc",)
    assert parsed.missing == ()


def test_undefined_variable_is_reported_as_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NO_SUCH_ROOT", raising=False)
    filelist = _write(
        tmp_path / "design.f", "$NO_SUCH_ROOT/core.sv\n+incdir+$NO_SUCH_ROOT/inc\n"
    )

    parsed = parse_filelist(filelist)

    assert parsed.missing == (Path("$NO_SUCH_ROOT/core.sv"),)
    assert parsed.missing_incdirs == (Path("$NO_SUCH_ROOT/inc"),)


def test_upper_case_suffixes_and_headers(tmp_path) -> None:
    upper = _write(tmp_path / "CORE.V")
    package = _write(tmp_path / "pkg" / "types.svh", "`define T 1\n")
    filelist = _write(tmp_path / "design.f", "CORE.V\npkg/types.svh\n")

    parsed = parse_filelist(filelist)

    assert parsed.sources == (upper,)
    assert parsed.headers == (package,)


def test_entries_that_are_not_acted_on_are_listed(tmp_path) -> None:
    _write(tmp_path / "top.sv")
    filelist = _write(
        tmp_path / "design.f",
        "top.sv\n-y lib\n+libext+.v+.sv\n-Wno-fatal\nwaive.vlt\n--top-module top\n",
    )

    parsed = parse_filelist(filelist)

    assert parsed.unsupported == ("-Wno-fatal", "waive.vlt", "--top-module top")
    # Library directories and suffixes are passed on to the tools.
    assert parsed.libdirs == (tmp_path / "lib",)
    assert parsed.libexts == (".v", ".sv")


def test_missing_incdir_is_listed_and_kept(tmp_path) -> None:
    _write(tmp_path / "top.sv")
    filelist = _write(tmp_path / "design.f", "+incdir+gone\ntop.sv\n")

    parsed = parse_filelist(filelist)

    assert parsed.incdirs == (tmp_path / "gone",)
    assert parsed.missing_incdirs == (tmp_path / "gone",)


def test_prepare_reports_what_it_did_not_use(tmp_path) -> None:
    _write(tmp_path / "top.sv")
    header = _write(tmp_path / "hdr" / "defs.svh", "`define X 1\n")
    filelist = _write(
        tmp_path / "design.f", "top.sv\nhdr/defs.svh\n-y lib\nnotes.txt\n+incdir+gone\n"
    )
    (tmp_path / "lib").mkdir()

    data = PrepareStep._parse_sv_filelist(str(filelist))
    notes = PrepareStep._filelist_notes("input_filelist", data)

    assert data["header_files"] == [header]
    assert [(note["kind"], note["entry"]) for note in notes] == [
        ("unsupported_entry", "notes.txt"),
        ("missing_incdir", str(tmp_path / "gone")),
    ]
    assert data["libdirs"] == [tmp_path / "lib"]
    info = PrepareStep._filelist_info(str(filelist), data)
    assert (info["header_files"], info["unsupported"], info["missing_incdirs"]) == (1, 1, 1)


# --- module detection -----------------------------------------------------


def test_line_comment_does_not_open_a_block_comment() -> None:
    text = "// see /* the notes\nmodule top; endmodule\n/* gone */ wire w; // tail\n"

    stripped = strip_sv_comments(text)

    assert "module top" in stripped
    assert "gone" not in stripped and "tail" not in stripped
    assert stripped.count("\n") == text.count("\n")


def test_comment_markers_inside_strings_are_kept() -> None:
    text = 'initial $display("// not a comment /* either");\nmodule top; endmodule\n'

    assert strip_sv_comments(text) == text


@pytest.mark.parametrize(
    "declaration",
    ["module top;", "module automatic top;", "module static top;", "macromodule top;"],
)
def test_module_keyword_forms_are_recognised(tmp_path, declaration) -> None:
    source = _write(tmp_path / "top.sv", f"{declaration}\nendmodule\n")

    assert module_definitions([source], "top") == [source]
    assert module_definitions([source], "to") == []


def test_module_in_a_comment_is_not_a_definition(tmp_path) -> None:
    source = _write(tmp_path / "x.sv", "// module top;\n/* module top; */\nmodule other; endmodule\n")

    assert module_definitions([source], "top") == []


def test_top_defined_once_per_ifdef_branch_counts_once(tmp_path) -> None:
    fpga = _write(tmp_path / "top_fpga.sv", "`ifdef FPGA\nmodule top; endmodule\n`endif\n")
    asic = _write(tmp_path / "top_asic.sv", "`ifndef FPGA\nmodule top; endmodule\n`endif\n")
    files = [fpga, asic]

    assert module_definitions(files, "top") == files
    assert module_definitions(files, "top", defined_macros=macro_names(["FPGA=1"])) == [fpga]
    assert module_definitions(files, "top", defined_macros=frozenset()) == [asic]
    prepared = {"rtl_files": [str(path) for path in files], "defines": ["FPGA"]}
    assert PrepareStep._top_definitions(prepared, "top") == [fpga]


def test_top_selection_falls_back_when_defines_select_nothing(tmp_path) -> None:
    # The macro comes from an included header this parser does not follow.
    source = _write(tmp_path / "top.sv", "`ifdef FROM_HEADER\nmodule top; endmodule\n`endif\n")

    prepared = {"rtl_files": [str(source)], "defines": []}

    assert PrepareStep._top_definitions(prepared, "top") == [source]


# --- port contract --------------------------------------------------------

_PARAMETERISED = """
module cpu_top #(
  parameter XLEN = 32,
  parameter int AW = $clog2(XLEN * 2),
  localparam BYTES = XLEN / 8
) (
  input  clock,
  input  [XLEN-1:0] data_i,
  output logic [AW:0] addr_o,
  output [BYTES-1:0] strb_o,
  output [3:0][7:0] lanes_o,
  input  [UNKNOWN-1:0] odd_i
`ifdef WITH_DEBUG
  , output debug_o
`endif
);
endmodule
"""


def test_parameterised_widths_are_evaluated() -> None:
    ports = {port["name"]: port for port in module_port_contract(_PARAMETERISED, "cpu_top")}

    assert ports["data_i"]["width"] == 32
    assert ports["addr_o"]["width"] == 7
    assert ports["strb_o"]["width"] == 4
    assert ports["lanes_o"]["width"] == 32
    assert ports["odd_i"]["width"] == 0
    assert "debug_o" not in ports


def test_ports_under_ifdef_follow_the_defines() -> None:
    ports = module_port_contract(
        _PARAMETERISED, "cpu_top", defined_macros=macro_names(["WITH_DEBUG"])
    )

    assert "debug_o" in [port["name"] for port in ports]


def test_unevaluated_width_is_not_a_mismatch_but_a_wrong_one_is() -> None:
    expected = [
        {"name": "a", "direction": "input", "width": 32},
        {"name": "b", "direction": "input", "width": 8},
        {"name": "c", "direction": "output", "width": 1},
    ]
    actual = [
        {"name": "a", "direction": "input", "width": 0},
        {"name": "b", "direction": "input", "width": 16},
        {"name": "c", "direction": "input", "width": 1},
    ]

    differences = compare_port_contracts(expected, actual)

    assert [item["name"] for item in differences["mismatches"]] == ["b", "c"]


# --- unprepared fallback --------------------------------------------------


def test_unprepared_single_filelist_keeps_incdirs_and_defines(tmp_path) -> None:
    top = _write(tmp_path / "rtl" / "top.sv")
    leaf = _write(tmp_path / "rtl" / "sub" / "leaf.sv")
    _write(tmp_path / "rtl" / "sub" / "inner.f", "leaf.sv\n+define+INNER\n")
    (tmp_path / "rtl" / "inc").mkdir()
    filelist = _write(
        tmp_path / "rtl" / "design.f", "top.sv\n-f sub/inner.f\n+incdir+inc\n+define+OUTER=2\n"
    )
    workspace = {"frontend_design_kind": "generic_rtl", "input_filelist": str(filelist)}

    assert rtl_files(workspace) == [str(top), str(leaf)]
    assert defines(workspace) == ["INNER", "OUTER=2"]
    assert incdirs(workspace)[0] == str(tmp_path / "rtl" / "inc")


def test_unprepared_cpu_workspace_still_reads_nothing(tmp_path) -> None:
    filelist = _write(tmp_path / "cpu.f", "+define+X\n")
    workspace = {"cpu_filelist": str(filelist), "input_filelist": str(filelist)}

    assert rtl_files(workspace) == []
    assert defines(workspace) == []


# --- prepare bookkeeping --------------------------------------------------


def _persist(tmp_path, workspace: dict) -> dict:
    import json

    parameters = tmp_path / "parameters.json"
    parameters.write_text(json.dumps({"input_filelist": workspace.get("input_filelist", "")}))
    workspace["parameters_path"] = str(parameters)
    PrepareStep()._persist_workspace_input_filelist(
        workspace, tmp_path / "merged_rtl.f", tmp_path / "prepared_inputs.json"
    )
    return json.loads(parameters.read_text())


def test_prepare_keeps_a_user_supplied_single_filelist(tmp_path) -> None:
    """Overwriting it dropped the filelist's incdirs and defines on the next run."""
    original = str(tmp_path / "design.f")
    workspace = {"input_filelist": original}

    stored = _persist(tmp_path, workspace)

    assert workspace["input_filelist"] == original
    assert stored["input_filelist"] == original
    assert stored["prepared_manifest"] == str(tmp_path / "prepared_inputs.json")


def test_prepare_still_points_cpu_workspaces_at_the_merged_list(tmp_path) -> None:
    workspace = {"cpu_filelist": str(tmp_path / "cpu.f"), "input_filelist": ""}

    stored = _persist(tmp_path, workspace)

    assert stored["input_filelist"] == str(tmp_path / "merged_rtl.f")


def test_step_log_records_why_a_step_stopped(tmp_path) -> None:
    from types import SimpleNamespace

    from fecompiler.engine.flow import _append_step_log

    log = tmp_path / "log.txt"
    log.write_text("[BEGIN]\n", encoding="utf-8")
    step = SimpleNamespace(log={"file": str(log)})

    _append_step_log(step, "[ERROR] FileNotFoundError: files listed in x.f were not found: a.sv")
    _append_step_log(SimpleNamespace(log={}), "ignored")

    assert log.read_text(encoding="utf-8").splitlines() == [
        "[BEGIN]",
        "[ERROR] FileNotFoundError: files listed in x.f were not found: a.sv",
    ]
