"""Small SystemVerilog module-interface helpers shared by catalog and prepare."""

from __future__ import annotations

import re
from collections.abc import Iterable
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


def _module_pattern(module_name: str) -> re.Pattern[str]:
    # `module top`, `macromodule top`, `module automatic top`, `module static top`.
    return re.compile(
        rf"\b(?:macro)?module\s+(?:(?:automatic|static)\s+)?{re.escape(module_name)}\b"
    )


def macro_names(defines: Any) -> frozenset[str]:
    """Return the macro names of ``NAME`` / ``NAME=value`` define entries."""
    return frozenset(
        name
        for name in (str(item).split("=", 1)[0].strip() for item in defines or ())
        if name
    )


def module_definitions(
    files: list[str | Path],
    module_name: str,
    *,
    defined_macros: set[str] | frozenset[str] | None = None,
) -> list[Path]:
    """Return source files that actually define *module_name*.

    With *defined_macros*, only the `` `ifdef `` branches those macros select
    are searched, so a module defined once per branch counts once. Without
    it every branch is searched.
    """
    pattern = _module_pattern(module_name)
    matches: list[Path] = []
    for raw_path in files:
        path = Path(raw_path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = strip_sv_comments(text)
        if defined_macros is not None:
            text = _apply_sv_conditional_directives(text, defined_macros)
        if pattern.search(text):
            matches.append(path)
    return matches


def module_port_contract_from_files(
    files: list[str | Path],
    module_name: str,
    *,
    defined_macros: set[str] | frozenset[str] | None = None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    for path in module_definitions(files, module_name, defined_macros=defined_macros):
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
    stripped = _apply_sv_conditional_directives(strip_sv_comments(text), defined_macros)
    header = _module_port_header(stripped, module_name)
    if header is None:
        return []
    constants = _module_constants(stripped, module_name)

    ports: list[dict[str, Any]] = []
    direction = ""
    width = 1
    for raw_port in _split_top_level_sv_list(header):
        direction_match = re.search(r"\b(input|output|inout)\b", raw_port)
        if direction_match:
            direction = direction_match.group(1)
            width = _packed_width(raw_port, constants)
        name = _port_decl_name(raw_port)
        if name:
            ports.append({"name": name, "direction": direction, "width": width})

    if any(not str(port["direction"]) for port in ports):
        declarations = _legacy_port_declarations(stripped, module_name, constants)
        ports = [declarations.get(str(port["name"]), port) for port in ports]
    return ports


def module_parameter_names(
    text: str,
    module_name: str,
    *,
    defined_macros: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Return the parameters of a module that can be overridden from outside.

    These are the ``parameter`` declarations of the ``#(...)`` list and of the
    module body; ``localparam`` is not one of them.
    """
    stripped = _apply_sv_conditional_directives(strip_sv_comments(text), defined_macros)
    # A string default may hold commas and names of its own.
    stripped = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', stripped)
    match = _module_pattern(module_name).search(stripped)
    if match is None:
        return []
    names: list[str] = []
    index = match.end()
    while index < len(stripped) and stripped[index].isspace():
        index += 1
    body_start = index
    if stripped.startswith("#", index):
        opening = stripped.find("(", index)
        end = _matching_delimiter(stripped, opening, "(", ")") if opening >= 0 else None
        if end is not None:
            names.extend(_declared_parameters(stripped[opening + 1 : end], True))
            body_start = end + 1
    end_module = re.search(r"\bendmodule\b", stripped[body_start:])
    body = stripped[body_start : body_start + end_module.start()] if end_module else stripped[body_start:]
    for statement in re.finditer(r"\bparameter\b[^;]*;", body):
        names.extend(_declared_parameters(statement.group(0)[:-1], False))
    return list(dict.fromkeys(names))


def _declared_parameters(declarations: str, overridable_by_default: bool) -> list[str]:
    names: list[str] = []
    overridable = overridable_by_default
    for item in _split_top_level_sv_list(declarations):
        keyword = re.match(r"\s*(parameter|localparam)\b", item)
        if keyword is not None:
            overridable = keyword.group(1) == "parameter"
        # The name is the last identifier before the default value.
        declared = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", re.sub(r"\[[^\]]*\]", " ", item.split("=", 1)[0]))
        if overridable and declared and declared[-1] not in {"parameter", "localparam"}:
            names.append(declared[-1])
    return names


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
        actual_width = int(actual_port.get("width", 0) or 0)
        if (
            str(expected_port.get("direction", "")) != str(actual_port.get("direction", ""))
            # Width 0 means the range could not be evaluated here; elaboration
            # checks it, so it is not reported as a mismatch.
            or (
                actual_width != 0
                and int(expected_port.get("width", 0) or 0) != actual_width
            )
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


def outputs_beyond(expected_names: Iterable[str], actual: list[dict[str, Any]]) -> list[str]:
    """Name the outputs of *actual* that an interface of *expected_names* lacks.

    A socket built for the interface leaves such a port unconnected. That is
    harmless for an output; any other port beyond the interface would be left
    undriven.
    """
    known = set(expected_names)
    return [
        str(port["name"])
        for port in actual
        if port.get("name") and port["name"] not in known and port.get("direction") == "output"
    ]


# The words both checks of a custom CPU top use for ports it cannot have.
UNDRIVEN_PORTS_TEXT = "extra ports the SoC socket would leave undriven"


def strip_sv_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments, leaving string literals alone.

    Scans once from the start, so ``// see /*`` is a line comment and does
    not open a block comment. Line breaks are kept.
    """
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if pair == "//":
            end = text.find("\n", index)
            index = length if end < 0 else end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            block = text[index : length if end < 0 else end + 2]
            output.append("\n" * block.count("\n") or " ")
            index += len(block)
        elif char == '"':
            end = index + 1
            while end < length and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            output.append(text[index : end + 1])
            index = end + 1
        else:
            output.append(char)
            index += 1
    return "".join(output)


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
    match = _module_pattern(module_name).search(text)
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
    match = _module_pattern(module_name).search(text)
    if match is None:
        return ""
    end = re.search(r"\bendmodule\b", text[match.end():])
    return text[match.end():match.end() + end.start()] if end is not None else text[match.end():]


def _legacy_port_declarations(
    text: str, module_name: str, constants: dict[str, int] | None = None
) -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    body = _module_body(text, module_name)
    for match in re.finditer(r"\b(?P<direction>input|output|inout)\b(?P<body>[^;]*);", body):
        direction = match.group("direction")
        declaration = match.group("body")
        width = _packed_width(declaration, constants)
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


def _packed_width(port_declaration: str, constants: dict[str, int] | None = None) -> int:
    """Return the packed width of a port, or 0 when it cannot be evaluated."""
    raw_ranges = re.findall(r"\[([^\]]+)\]", port_declaration)
    if not raw_ranges:
        return 1
    width = 1
    for raw_range in raw_ranges:
        left, separator, right = raw_range.partition(":")
        high = _constant_value(left, constants or {})
        low = _constant_value(right, constants or {}) if separator else None
        if high is None or low is None:
            return 0
        width *= abs(high - low) + 1
    return width


_PARAMETER_ASSIGNMENT = re.compile(
    r"\b(?:parameter|localparam)\b[^=;,()]*?\b([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*([^;,]+)"
)
_SIZED_LITERAL = re.compile(r"(?:\d+\s*)?'\s*s?([bodhBODH])\s*([0-9a-fA-F_xXzZ?]+)")
_CONSTANT_TOKEN = re.compile(r"\s*(\$clog2|[A-Za-z_][A-Za-z0-9_$]*|\d[\d_]*|\*\*|[-+*/%()])")


def _module_constants(text: str, module_name: str) -> dict[str, int]:
    """Return the integer parameters of a module at their default values."""
    match = _module_pattern(module_name).search(text)
    if match is None:
        return {}
    end = re.search(r"\bendmodule\b", text[match.end():])
    scope = text[match.end() : match.end() + end.start()] if end else text[match.end():]
    constants: dict[str, int] = {}
    for name, expression in _PARAMETER_ASSIGNMENT.findall(scope):
        # A default may itself end at the `)` that closes the parameter list.
        value = _constant_value(_balanced_prefix(expression), constants)
        if value is not None:
            constants.setdefault(name, value)
    return constants


def _balanced_prefix(expression: str) -> str:
    depth = 0
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return expression[:index]
            depth -= 1
    return expression


def _constant_value(expression: str, constants: dict[str, int]) -> int | None:
    """Evaluate an integer constant expression: literals, parameters,
    ``+ - * / % **``, parentheses, and ``$clog2``. ``None`` if anything else."""
    text = _SIZED_LITERAL.sub(_sized_literal_value, expression).strip()
    if not text or "?" in text:
        return None
    tokens: list[str] = []
    position = 0
    while position < len(text):
        match = _CONSTANT_TOKEN.match(text, position)
        if match is None:
            return None
        tokens.append(match.group(1))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    try:
        value, rest = _parse_sum(tokens, constants)
    except (ValueError, ZeroDivisionError, IndexError, OverflowError):
        return None
    return value if not rest else None


def _sized_literal_value(match: re.Match[str]) -> str:
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group(1).lower()]
    try:
        return str(int(match.group(2).replace("_", ""), base))
    except ValueError:
        return "?"


def _parse_sum(tokens: list[str], constants: dict[str, int]) -> tuple[int, list[str]]:
    value, tokens = _parse_product(tokens, constants)
    while tokens and tokens[0] in "+-":
        operator = tokens[0]
        right, tokens = _parse_product(tokens[1:], constants)
        value = value + right if operator == "+" else value - right
    return value, tokens


def _parse_product(tokens: list[str], constants: dict[str, int]) -> tuple[int, list[str]]:
    value, tokens = _parse_power(tokens, constants)
    while tokens and tokens[0] in ("*", "/", "%"):
        operator = tokens[0]
        right, tokens = _parse_power(tokens[1:], constants)
        if operator == "*":
            value *= right
        elif operator == "/":
            value = int(value / right)  # Verilog integer division truncates
        else:
            value = value - right * int(value / right)
    return value, tokens


def _parse_power(tokens: list[str], constants: dict[str, int]) -> tuple[int, list[str]]:
    value, tokens = _parse_atom(tokens, constants)
    if tokens and tokens[0] == "**":
        exponent, tokens = _parse_power(tokens[1:], constants)
        if exponent < 0 or exponent > 64:
            raise ValueError("exponent out of range")
        value **= exponent
    return value, tokens


def _parse_atom(tokens: list[str], constants: dict[str, int]) -> tuple[int, list[str]]:
    token = tokens[0]
    if token in "+-" and len(token) == 1:
        value, rest = _parse_atom(tokens[1:], constants)
        return (value if token == "+" else -value), rest
    if token == "(":
        value, rest = _parse_sum(tokens[1:], constants)
        if rest[0] != ")":
            raise ValueError("unbalanced parenthesis")
        return value, rest[1:]
    if token == "$clog2":
        if tokens[1] != "(":
            raise ValueError("$clog2 needs an argument")
        value, rest = _parse_sum(tokens[2:], constants)
        if rest[0] != ")" or value < 0:
            raise ValueError("bad $clog2 argument")
        return max(value - 1, 0).bit_length(), rest[1:]
    if token[0].isdigit():
        return int(token.replace("_", "")), tokens[1:]
    if token in constants:
        return constants[token], tokens[1:]
    raise ValueError(f"unknown name {token}")


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
