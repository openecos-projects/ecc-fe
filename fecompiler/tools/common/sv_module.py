"""Small SystemVerilog module-interface helpers shared by catalog and prepare."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_SIMPLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_SYSTEMVERILOG_RESERVED_WORDS = frozenset(
    """
    accept_on alias always always_comb always_ff always_latch and assert assign assume automatic before begin bind bins
    binsof bit break buf bufif0 bufif1 byte case casex casez cell chandle checker class clocking cmos config const
    constraint context continue cover covergroup coverpoint cross deassign default defparam design disable dist do edge
    else end endchecker endclass endclocking endconfig endfunction endgenerate endgroup endinterface endmodule endpackage
    endprimitive endprogram endproperty endspecify endsequence endtable endtask enum event eventually expect export extends
    extern final first_match for force foreach forever fork forkjoin function generate genvar global highz0 highz1 if iff
    ifnone ignore_bins illegal_bins implements implies import incdir include initial inout input inside int integer interconnect
    intersect join join_any join_none large let liblist library local localparam logic longint macromodule matches medium
    modport module nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or output package packed
    parameter pmos posedge primitive priority program property protected pull0 pull1 pulldown pullup pulsestyle_ondetect
    pulsestyle_onevent pure rand randc randcase randsequence rcmos real realtime ref reg reject_on release repeat restrict
    return rnmos rpmos rtran rtranif0 rtranif1 s_always s_eventually s_nexttime s_until s_until_with scalared sequence
    shortint shortreal showcancelled signed small solve specify specparam static string strong strong0 strong1 struct super
    supply0 supply1 sync_accept_on sync_reject_on table tagged task this throughout time timeprecision timeunit tran tranif0
    tranif1 tri tri0 tri1 triand trior trireg type typedef union unique unique0 unsigned until until_with untyped use uwire
    var vectored virtual void wait wait_order wand weak weak0 weak1 while wildcard wire with within wor xnor xor
    """.split()
)


def is_simple_sv_identifier(value: str) -> bool:
    """Return whether *value* is safe as an unescaped SV identifier."""
    normalized = str(value).strip()
    return (
        _SIMPLE_IDENTIFIER.fullmatch(normalized) is not None
        and normalized not in _SYSTEMVERILOG_RESERVED_WORDS
    )


def module_definitions(files: list[str | Path], module_name: str) -> list[Path]:
    """Return source files that actually define *module_name*."""
    pattern = re.compile(rf"\bmodule\s+{re.escape(module_name)}\b")
    matches: list[Path] = []
    for raw_path in files:
        path = Path(raw_path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(strip_sv_comments(text)):
            matches.append(path)
    return matches


def module_port_contract_from_files(
    files: list[str | Path],
    module_name: str,
    *,
    defined_macros: set[str] | frozenset[str] | None = None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    for path in module_definitions(files, module_name):
        try:
            contract = module_port_contract(
                path.read_text(encoding="utf-8", errors="ignore"),
                module_name,
                defined_macros=defined_macros,
            )
        except OSError:
            continue
        return path, contract
    return None, []


def module_port_contract(
    text: str,
    module_name: str,
    *,
    defined_macros: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Parse an ANSI or legacy-style module port contract."""
    stripped = strip_sv_comments(_apply_sv_conditional_directives(text, defined_macros))
    header = _module_port_header(stripped, module_name)
    if header is None:
        return []

    ports: list[dict[str, Any]] = []
    direction = ""
    width = 1
    for raw_port in _split_top_level_sv_list(header):
        direction_match = re.search(r"\b(input|output|inout)\b", raw_port)
        if direction_match:
            direction = direction_match.group(1)
            width = _packed_width(raw_port)
        name = _port_decl_name(raw_port)
        if name:
            ports.append({"name": name, "direction": direction, "width": width})

    if any(not str(port["direction"]) for port in ports):
        declarations = _legacy_port_declarations(stripped, module_name)
        ports = [declarations.get(str(port["name"]), port) for port in ports]
    return ports


def compare_port_contracts(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> dict[str, list[Any]]:
    expected_by_name = {str(port.get("name", "")): port for port in expected if str(port.get("name", ""))}
    actual_by_name = {str(port.get("name", "")): port for port in actual if str(port.get("name", ""))}
    missing = [name for name in expected_by_name if name not in actual_by_name]
    extra = [name for name in actual_by_name if name not in expected_by_name]
    mismatches: list[dict[str, Any]] = []
    for name in expected_by_name.keys() & actual_by_name.keys():
        expected_port = expected_by_name[name]
        actual_port = actual_by_name[name]
        if (
            str(expected_port.get("direction", "")) != str(actual_port.get("direction", ""))
            or int(expected_port.get("width", 0) or 0) != int(actual_port.get("width", 0) or 0)
        ):
            mismatches.append({
                "name": name,
                "expected": expected_port,
                "actual": actual_port,
            })
    return {
        "missing": missing,
        "extra": extra,
        "mismatches": sorted(mismatches, key=lambda item: str(item["name"])),
    }


def strip_sv_comments(text: str) -> str:
    without_block = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//.*", "", without_block)


def _apply_sv_conditional_directives(
    text: str,
    defined_macros: set[str] | frozenset[str] | None,
) -> str:
    """Select simple `ifdef branches before parsing a module declaration."""
    macros = set(defined_macros or ())
    output: list[str] = []
    # Each frame stores (parent active, a prior branch matched, current active).
    stack: list[tuple[bool, bool, bool]] = []
    directive = re.compile(
        r"^\s*`(?P<kind>ifdef|ifndef|elsif|else|endif|define|undef)"
        r"(?:\s+(?P<name>[A-Za-z_][A-Za-z0-9_$]*))?"
    )

    for line in text.splitlines(keepends=True):
        match = directive.match(line)
        active = stack[-1][2] if stack else True
        if match is None:
            if active:
                output.append(line)
            continue

        kind = match.group("kind")
        name = match.group("name") or ""
        if kind in {"define", "undef"}:
            if active and name:
                if kind == "define":
                    macros.add(name)
                else:
                    macros.discard(name)
            continue
        if kind in {"ifdef", "ifndef"}:
            matched = name in macros
            if kind == "ifndef":
                matched = not matched
            stack.append((active, matched, active and matched))
            continue
        if not stack:
            continue

        parent_active, branch_taken, _ = stack[-1]
        if kind == "elsif":
            matched = not branch_taken and name in macros
            stack[-1] = (parent_active, branch_taken or matched, parent_active and matched)
        elif kind == "else":
            matched = not branch_taken
            stack[-1] = (parent_active, True, parent_active and matched)
        else:
            stack.pop()

    return "".join(output)


def _module_port_header(text: str, module_name: str) -> str | None:
    match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", text)
    if match is None:
        return None
    index = match.end()
    while index < len(text) and text[index].isspace():
        index += 1
    if index < len(text) and text[index] == "#":
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != "(":
            return None
        end = _matching_delimiter(text, index, "(", ")")
        if end is None:
            return None
        index = end + 1
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != "(":
        return "" if index < len(text) and text[index] == ";" else None
    end = _matching_delimiter(text, index, "(", ")")
    return text[index + 1:end] if end is not None else None


def _module_body(text: str, module_name: str) -> str:
    match = re.search(rf"\bmodule\s+{re.escape(module_name)}\b", text)
    if match is None:
        return ""
    end = re.search(r"\bendmodule\b", text[match.end():])
    return text[match.end():match.end() + end.start()] if end is not None else text[match.end():]


def _legacy_port_declarations(text: str, module_name: str) -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    body = _module_body(text, module_name)
    for match in re.finditer(r"\b(?P<direction>input|output|inout)\b(?P<body>[^;]*);", body):
        direction = match.group("direction")
        declaration = match.group("body")
        width = _packed_width(declaration)
        for raw_name in _split_top_level_sv_list(declaration):
            name = _port_decl_name(raw_name)
            if name:
                declarations[name] = {"name": name, "direction": direction, "width": width}
    return declarations


def _matching_delimiter(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_top_level_sv_list(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(text):
        if char in depths:
            depths[char] += 1
        elif char in pairs:
            opening = pairs[char]
            depths[opening] = max(0, depths[opening] - 1)
        elif char == "," and not any(depths.values()):
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _packed_width(port_declaration: str) -> int:
    raw_ranges = re.findall(r"\[([^\]]+)\]", port_declaration)
    if not raw_ranges:
        return 1
    width = 1
    for raw_range in raw_ranges:
        match = re.fullmatch(r"\s*([0-9]+)\s*:\s*([0-9]+)\s*", raw_range)
        if match is None:
            return 0
        width *= abs(int(match.group(1)) - int(match.group(2))) + 1
    return width


def _port_decl_name(raw_port: str) -> str:
    text = raw_port.strip()
    if not text:
        return ""
    text = re.sub(r"\[[^\]]+\]", " ", text)
    tokens = [
        token
        for token in re.split(r"\s+", text)
        if token
        and token not in {
            "input", "output", "inout", "wire", "reg", "logic", "signed", "unsigned",
        }
    ]
    return tokens[-1].split("=")[0].strip() if tokens else ""
