"""Measure the netlist Yosys wrote: cells, fanout, fanin and logic depth per module.

The netlist is word-level: Yosys has only run ``proc`` and light clean-up, so
one cell is a whole word, or a whole ``case``. Fanin counts every input bit of
such a cell, depth counts word-level cells, and a synchronous reset lowered
into mux selects counts as fanout. So the numbers are recorded, and one run can
be compared with another, but no limit is applied to them: one that fits a
small core would flag every large one.
"""

from __future__ import annotations

import heapq
import json
import re
from pathlib import Path
from typing import Any

from fecompiler.tools.review.probe_common import _int_or_zero

# How many of the widest nets and cells each module lists, and the design.
_LISTED_PER_MODULE = 8
_LISTED = 20


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _metrics_from_stat(stat: dict[str, Any], log: str) -> dict[str, Any]:
    design = stat.get("design", {}) if isinstance(stat.get("design"), dict) else {}
    modules = stat.get("modules", {}) if isinstance(stat.get("modules"), dict) else {}
    module_values = [item for item in modules.values() if isinstance(item, dict)]

    cell_types: dict[str, int] = {}
    for module in module_values:
        for key, value in _extract_cell_types(module).items():
            cell_types[key] = cell_types.get(key, 0) + value

    num_cells = _int_or_zero(design.get("num_cells"))
    if not num_cells:
        num_cells = sum(_int_or_zero(module.get("num_cells")) for module in module_values)
    if not num_cells and cell_types:
        num_cells = sum(cell_types.values())

    num_wires = _int_or_zero(design.get("num_wires"))
    if not num_wires:
        num_wires = sum(_int_or_zero(module.get("num_wires")) for module in module_values)

    num_ports = _int_or_zero(design.get("num_port_bits"))
    if not num_ports:
        num_ports = sum(_int_or_zero(module.get("num_port_bits")) for module in module_values)

    num_processes = sum(_int_or_zero(module.get("num_processes")) for module in module_values)
    memory_cells = sum(count for name, count in cell_types.items() if "$mem" in name.lower() or "mem" in name.lower())
    mux_cells = sum(count for name, count in cell_types.items() if "$mux" in name.lower() or "$pmux" in name.lower())
    arithmetic_cells = sum(
        count
        for name, count in cell_types.items()
        if any(op in name.lower() for op in ("$add", "$sub", "$mul", "$div", "$mod", "$alu"))
    )

    return {
        "source": "yosys_stat",
        "modules": len(modules),
        "module_names": sorted(str(name).lstrip("\\") for name in modules.keys())[:80],
        "cells": num_cells,
        "wires": num_wires,
        "port_bits": num_ports,
        "processes": num_processes,
        "memory_cells": memory_cells,
        "mux_cells": mux_cells,
        "arithmetic_cells": arithmetic_cells,
        "cell_types": dict(sorted(cell_types.items())[:80]),
        "log_warnings": len(re.findall(r"\bwarning\b", log, flags=re.I)),
        "log_errors": len(re.findall(r"\berror\b", log, flags=re.I)),
    }


def _structural_metrics_from_netlist(netlist: dict[str, Any]) -> dict[str, Any]:
    modules = netlist.get("modules", {}) if isinstance(netlist.get("modules"), dict) else {}
    summaries: list[dict[str, Any]] = []
    high_fanout_nets: list[dict[str, Any]] = []
    high_fanin_cells: list[dict[str, Any]] = []
    deep_comb_paths: list[dict[str, Any]] = []
    cycles: list[str] = []

    for raw_name, raw_module in modules.items():
        if not isinstance(raw_module, dict):
            continue
        module_name = str(raw_name).lstrip("\\")
        summary = _module_graph_summary(module_name, raw_module)
        summaries.append(summary)
        high_fanout_nets.extend(summary.get("high_fanout_nets", []))
        high_fanin_cells.extend(summary.get("high_fanin_cells", []))
        if _int_or_zero(summary.get("max_comb_depth")):
            deep_comb_paths.append({
                "module": module_name,
                "depth": summary.get("max_comb_depth", 0),
                "endpoint": summary.get("deepest_endpoint", ""),
                "source": summary.get("deepest_source", ""),
                "line": summary.get("deepest_line", 0),
                "column": summary.get("deepest_column", 0),
            })
        if summary.get("comb_cycle"):
            cycles.append(module_name)

    summaries.sort(key=lambda item: (
        _int_or_zero(item.get("max_comb_depth")),
        _int_or_zero(item.get("max_fanout")),
        _int_or_zero(item.get("cell_count")),
    ), reverse=True)
    high_fanout_nets.sort(key=lambda item: _int_or_zero(item.get("fanout")), reverse=True)
    high_fanin_cells.sort(key=lambda item: _int_or_zero(item.get("fanin")), reverse=True)
    deep_comb_paths.sort(key=lambda item: _int_or_zero(item.get("depth")), reverse=True)

    # The lists keep their names, but hold the largest of each, however small.
    return {
        "netlist_source": "yosys_write_json",
        "max_fanout": _max_int(summaries, "max_fanout"),
        "max_fanin": _max_int(summaries, "max_fanin"),
        "max_comb_depth": _max_int(summaries, "max_comb_depth"),
        "high_fanout_nets": high_fanout_nets[:_LISTED],
        "high_fanin_cells": high_fanin_cells[:_LISTED],
        "deep_comb_paths": deep_comb_paths[:_LISTED],
        "comb_cycle_modules": cycles[:20],
        "module_structure": summaries[:40],
    }


def _module_graph_summary(module_name: str, module: dict[str, Any]) -> dict[str, Any]:
    cells = module.get("cells", {}) if isinstance(module.get("cells"), dict) else {}
    ports = module.get("ports", {}) if isinstance(module.get("ports"), dict) else {}
    netnames = module.get("netnames", {}) if isinstance(module.get("netnames"), dict) else {}
    drivers: dict[int, list[str]] = {}
    consumers: dict[int, list[tuple[str, str]]] = {}
    cell_inputs: dict[str, set[int]] = {}
    cell_outputs: dict[str, set[int]] = {}
    cell_locations: dict[str, dict[str, Any]] = {}
    bit_locations: dict[int, dict[str, Any]] = {}
    comb_cells: set[str] = set()
    seq_cells: set[str] = set()

    for raw_net in netnames.values():
        if not isinstance(raw_net, dict):
            continue
        location = _source_location_from_attrs(raw_net.get("attributes"))
        if not location.get("source"):
            continue
        for bit in _connection_bits(raw_net.get("bits")):
            bit_locations.setdefault(bit, location)

    for port_name, raw_port in ports.items():
        if not isinstance(raw_port, dict):
            continue
        direction = str(raw_port.get("direction", ""))
        bits = _connection_bits(raw_port.get("bits"))
        if direction == "input":
            for bit in bits:
                drivers.setdefault(bit, []).append(f"port:{port_name}")
        elif direction == "output":
            for bit in bits:
                consumers.setdefault(bit, []).append((f"port:{port_name}", "out"))

    for raw_cell_name, raw_cell in cells.items():
        if not isinstance(raw_cell, dict):
            continue
        cell_name = str(raw_cell_name).lstrip("\\")
        cell_type = str(raw_cell.get("type", ""))
        location = _source_location_from_attrs(raw_cell.get("attributes"))
        if location.get("source"):
            cell_locations[cell_name] = location
        connections = raw_cell.get("connections", {}) if isinstance(raw_cell.get("connections"), dict) else {}
        directions = raw_cell.get("port_directions", {}) if isinstance(raw_cell.get("port_directions"), dict) else {}
        inputs: set[int] = set()
        outputs: set[int] = set()

        for port, raw_bits in connections.items():
            direction = str(directions.get(port, "")).lower()
            if not direction:
                direction = _infer_port_direction(cell_type, str(port))
            bits = _connection_bits(raw_bits)
            if direction == "output":
                outputs.update(bits)
                for bit in bits:
                    drivers.setdefault(bit, []).append(cell_name)
            elif direction == "input":
                inputs.update(bits)
                for bit in bits:
                    consumers.setdefault(bit, []).append((cell_name, str(port)))
            elif direction == "inout":
                inputs.update(bits)
                outputs.update(bits)
                for bit in bits:
                    consumers.setdefault(bit, []).append((cell_name, str(port)))
                    drivers.setdefault(bit, []).append(cell_name)

        cell_inputs[cell_name] = inputs
        cell_outputs[cell_name] = outputs
        if _is_sequential_cell(cell_type):
            seq_cells.add(cell_name)
        elif cell_type.startswith("$"):
            comb_cells.add(cell_name)
        # Anything else is an instance of another module. What happens between
        # its inputs and outputs is that module's business, so it is neither a
        # combinational cell here nor part of a path or a cycle through it.

    fanouts = {
        # A clock or reset reaches every register by design; counting those
        # pins would make the register count the fanout of every module.
        bit: len(
            [
                sink
                for sink, port in sinks
                if not sink.startswith("port:")
                and not (sink in seq_cells and port.lstrip("\\").upper() in _CLOCK_RESET_PORTS)
            ]
        )
        for bit, sinks in consumers.items()
    }
    high_fanout = [
        {
            "module": module_name,
            "net": _bit_name(module, bit),
            "bit": bit,
            "fanout": fanout,
            "drivers": drivers.get(bit, [])[:6],
            **(
                bit_locations.get(bit)
                or _first_cell_location(drivers.get(bit, []), cell_locations)
                or _first_cell_location([sink for sink, _ in consumers[bit]], cell_locations)
            ),
        }
        for bit, fanout in _largest(fanouts)
    ]

    fanins = {cell_name: len(bits) for cell_name, bits in cell_inputs.items()}
    high_fanin = [
        {
            "module": module_name,
            "cell": cell_name,
            "fanin": fanin,
            **cell_locations.get(cell_name, {}),
        }
        for cell_name, fanin in _largest(fanins)
    ]

    graph: dict[str, set[str]] = {cell: set() for cell in comb_cells}
    indegree: dict[str, int] = {cell: 0 for cell in comb_cells}
    for source in comb_cells:
        for bit in cell_outputs.get(source, set()):
            for sink, _ in consumers.get(bit, []):
                if sink == source or sink not in comb_cells:
                    continue
                if sink not in graph[source]:
                    graph[source].add(sink)
                    indegree[sink] += 1

    queue = [cell for cell, degree in indegree.items() if degree == 0]
    depth = {cell: 1 for cell in comb_cells}
    visited = 0
    deepest_endpoint = ""
    while queue:
        cell = queue.pop(0)
        visited += 1
        if depth.get(cell, 0) >= depth.get(deepest_endpoint, 0):
            deepest_endpoint = cell
        for sink in graph.get(cell, set()):
            depth[sink] = max(depth.get(sink, 1), depth.get(cell, 1) + 1)
            indegree[sink] -= 1
            if indegree[sink] == 0:
                queue.append(sink)

    has_cycle = bool(comb_cells) and visited < len(comb_cells)
    deepest_location = cell_locations.get(deepest_endpoint, {})
    return {
        "module": module_name,
        "cell_count": len(cells),
        "comb_cells": len(comb_cells),
        "sequential_cells": len(seq_cells),
        "max_fanout": max(fanouts.values(), default=0),
        "max_fanin": max(fanins.values(), default=0),
        "max_comb_depth": max(depth.values()) if depth else 0,
        "deepest_endpoint": deepest_endpoint,
        "deepest_source": deepest_location.get("source", ""),
        "deepest_line": deepest_location.get("line", 0),
        "deepest_column": deepest_location.get("column", 0),
        "comb_cycle": has_cycle,
        "high_fanout_nets": high_fanout,
        "high_fanin_cells": high_fanin,
    }


def _largest(counts: dict[Any, int]) -> list[tuple[Any, int]]:
    """The entries with the largest nonzero counts, largest first; ties keep their order."""
    return [
        (key, count)
        for key, count in heapq.nlargest(_LISTED_PER_MODULE, counts.items(), key=lambda item: item[1])
        if count
    ]


def _first_cell_location(names: list[str], locations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for name in names:
        if name.startswith("port:"):
            continue
        location = locations.get(name)
        if location:
            return location
    return {}


def _source_location_from_attrs(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, dict):
        return {}
    raw = str(attrs.get("src", "")).strip()
    if not raw:
        return {}
    first = raw.split("|", maxsplit=1)[0]
    match = re.search(
        r"(?P<source>.*?\.(?:sv|svh|v|vh)):(?P<line>\d+)(?:\.(?P<column>\d+))?",
        first,
    )
    if not match:
        return {}
    return {
        "source": match.group("source"),
        "line": _int_or_zero(match.group("line")),
        "column": _int_or_zero(match.groupdict().get("column")) or 1,
    }


def _connection_bits(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    bits: list[int] = []
    for item in value:
        if isinstance(item, int):
            bits.append(item)
    return bits


def _infer_port_direction(cell_type: str, port: str) -> str:
    cell = cell_type.lower()
    name = port.upper().lstrip("\\")
    if _is_sequential_cell(cell):
        return "output" if name in {"Q"} else "input"
    if name in {"Y", "Q", "O", "OUT", "C"}:
        return "output"
    return "input"


# Clock, reset and set pins of Yosys register and memory cells.
_CLOCK_RESET_PORTS = frozenset(
    {
        "CLK",
        "ARST",
        "SRST",
        "ALOAD",
        "SET",
        "CLR",
        "RD_CLK",
        "WR_CLK",
        "RD_ARST",
        "RD_SRST",
    }
)


def _is_sequential_cell(cell_type: str) -> bool:
    text = cell_type.lower()
    return any(token in text for token in ("$dff", "$adff", "$sdff", "$dlatch", "$mem", "dff", "latch"))


def _bit_name(module: dict[str, Any], bit: int) -> str:
    netnames = module.get("netnames", {}) if isinstance(module.get("netnames"), dict) else {}
    for raw_name, raw_net in netnames.items():
        if not isinstance(raw_net, dict):
            continue
        bits = _connection_bits(raw_net.get("bits"))
        if bit in bits:
            return str(raw_name).lstrip("\\")
    return f"bit[{bit}]"


def _max_int(items: list[dict[str, Any]], key: str) -> int:
    return max((_int_or_zero(item.get(key)) for item in items), default=0)


def _extract_cell_types(module: dict[str, Any]) -> dict[str, int]:
    cells = module.get("num_cells_by_type")
    if isinstance(cells, dict):
        return {str(key): _int_or_zero(value) for key, value in cells.items()}
    cells = module.get("cells")
    if isinstance(cells, dict):
        out: dict[str, int] = {}
        for value in cells.values():
            if isinstance(value, dict):
                cell_type = str(value.get("type", ""))
                if cell_type:
                    out[cell_type] = out.get(cell_type, 0) + 1
        return out
    return {}
