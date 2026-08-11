"""Tests for lightweight SystemVerilog module-interface parsing."""

from fecompiler.tools.common.sv_module import module_port_contract


CONDITIONAL_PORTS = """
module cpu_top (
  input logic clock,
  output logic ready
`ifdef SIM_DEBUG
  ,
  output logic debug_valid
`else
`ifdef FALLBACK_DEBUG
  ,
  output logic fallback_valid
`endif
`endif
);
endmodule
"""


def test_module_port_contract_excludes_undefined_conditional_ports() -> None:
    assert module_port_contract(CONDITIONAL_PORTS, "cpu_top") == [
        {"name": "clock", "direction": "input", "width": 1},
        {"name": "ready", "direction": "output", "width": 1},
    ]


def test_module_port_contract_includes_defined_conditional_ports() -> None:
    assert module_port_contract(
        CONDITIONAL_PORTS,
        "cpu_top",
        defined_macros={"SIM_DEBUG"},
    ) == [
        {"name": "clock", "direction": "input", "width": 1},
        {"name": "ready", "direction": "output", "width": 1},
        {"name": "debug_valid", "direction": "output", "width": 1},
    ]


def test_module_port_contract_honors_active_source_defines() -> None:
    source = "`define FALLBACK_DEBUG\n" + CONDITIONAL_PORTS

    assert module_port_contract(source, "cpu_top")[-1] == {
        "name": "fallback_valid",
        "direction": "output",
        "width": 1,
    }
