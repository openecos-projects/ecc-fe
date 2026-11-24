"""The name `ecc-fe` prints for itself follows ECC_FE_PROGRAM."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import fecompiler
from fecompiler.cli import main as cli_main
from fecompiler.cli.core.records import disclosure_cmd
from fecompiler.utility.program import PROGRAM_ENV, invoked, program_name

_COMMAND_WORD = re.compile(r"(?<![\w./-])ecc-fe(?![\w./-])")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)
    monkeypatch.delenv(PROGRAM_ENV, raising=False)


@pytest.fixture
def as_ecc_fe(monkeypatch) -> None:
    monkeypatch.setenv(PROGRAM_ENV, "ecc fe")


def _records(capsys) -> list[dict]:
    return json.loads(capsys.readouterr().out)["records"]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def test_default_name_is_unchanged() -> None:
    assert program_name() == "ecc-fe"
    assert invoked("ecc-fe run --resume") == "ecc-fe run --resume"
    assert disclosure_cmd("ecc-fe status", project="p") == "ecc-fe status --project p"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ecc fe", "ecc fe"),
        ("  ecc   fe  ", "ecc fe"),
        ("/opt/ecos/bin/ecc fe", "/opt/ecos/bin/ecc fe"),
        ("", "ecc-fe"),
        ("ecc fe; rm -rf x", "ecc-fe"),
        ("ecc $(id)", "ecc-fe"),
        ("ecc 'fe'", "ecc-fe"),
        ("-x", "ecc-fe"),
        ("a b c d e", "ecc-fe"),
    ],
)
def test_only_plain_words_are_accepted_as_a_name(monkeypatch, value, expected) -> None:
    monkeypatch.setenv(PROGRAM_ENV, value)

    assert program_name() == expected


def test_only_the_command_word_is_rewritten(as_ecc_fe) -> None:
    assert invoked("ecc-fe run --resume") == "ecc fe run --resume"
    assert invoked("created by `ecc-fe init <name>`") == "created by `ecc fe init <name>`"
    for untouched in (
        "/home/user/ecc-fe/bin/ecc-fe",
        ".ecc-fe/template",
        "ecc-fe.toml",
        "ecc-fe-handoff",
        "my-ecc-fe",
    ):
        assert invoked(untouched) == untouched


def test_user_values_are_not_rewritten(as_ecc_fe) -> None:
    command = disclosure_cmd("ecc-fe status", project="ecc-fe", run_id="ecc-fe")

    assert command == "ecc fe status --project ecc-fe --run-id ecc-fe"


def test_suggested_commands_use_the_name(as_ecc_fe, tmp_path, capsys) -> None:
    project = tmp_path / "ecc-fe"
    assert cli_main.run(["init", str(project), "--json"]) == 0
    created = _records(capsys)
    assert cli_main.run(["status", "--project", str(project), "--json"]) in (0, 1)
    status = _records(capsys)
    assert cli_main.run(["log", "sim", "--project", str(project), "--json"]) == 1
    log = _records(capsys)
    assert cli_main.run(["handoff", "--project", str(project), "--json"]) == 1
    handoff = _records(capsys)
    assert cli_main.run(["runs", "list", "--project", str(tmp_path / "none"), "--json"]) == 1
    runs = _records(capsys)

    commands = [
        value
        for record in created + status + log + handoff + runs
        for key, value in record.items()
        if key.endswith("_cmd") and isinstance(value, str)
    ]
    assert commands
    assert all(command.startswith("ecc fe ") for command in commands)
    # The project directory is itself named `ecc-fe`; it must survive intact.
    assert any(str(project) in command for command in commands)
    assert "`ecc fe init <name>`" in runs[0]["reason"]


def test_text_messages_use_the_name(as_ecc_fe, tmp_path, capsys) -> None:
    assert cli_main.run(["status", "--workspace", str(tmp_path / "none")]) == 1

    assert capsys.readouterr().err.startswith("ecc fe: error: workspace_not_found")


def test_usage_and_help_use_the_name(as_ecc_fe, capsys) -> None:
    assert cli_main.run(["run", "--help"]) == 0
    assert "Usage: ecc fe run" in capsys.readouterr().out

    assert cli_main.run(["workspace", "--help"]) == 0
    assert "ecc fe workspace" in capsys.readouterr().out

    assert cli_main.run(["--design", "x", "--top", "y"]) == 2
    error = capsys.readouterr().err
    assert "ecc fe init <dir>" in error
    assert not _COMMAND_WORD.search(error)


def test_files_do_not_depend_on_the_name(monkeypatch, tmp_path, capsys) -> None:
    plain = tmp_path / "plain" / "demo"
    wrapped = tmp_path / "wrapped" / "demo"
    assert cli_main.run(["init", str(plain), "--json"]) == 0
    monkeypatch.setenv(PROGRAM_ENV, "ecc fe")
    assert cli_main.run(["init", str(wrapped), "--json"]) == 0
    capsys.readouterr()

    for name in ("ecc-fe.toml",):
        left = (plain / name).read_text(encoding="utf-8")
        right = (wrapped / name).read_text(encoding="utf-8")
        assert left.replace(str(plain), "<project>") == right.replace(
            str(wrapped), "<project>"
        )
        assert "ecc fe" not in right


def test_version_line_keeps_the_product_name(as_ecc_fe, capsys) -> None:
    assert cli_main.run(["--version"]) == 0

    assert capsys.readouterr().out.startswith("ecc-fe ")


# Text written to disk keeps the product name: files must not depend on how the
# program was started.
_WRITTEN_TO_FILES = {
    ("cli/project/config.py", "# with `ecc-fe param set`."),
    ("cli/handlers/handoff.py", "# Generated by ecc-fe handoff."),
}
_NAMING_CALLS = {"invoked", "disclosure_cmd", "_target_cmd"}


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _unnamed_literals(path: Path, relative: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = _docstrings(tree)
    named: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if name in _NAMING_CALLS:
                named.update(id(inner) for inner in ast.walk(node))
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skipped or id(node) in named:
            continue
        if not _COMMAND_WORD.search(node.value):
            continue
        if node.value.strip() == "ecc-fe":
            # The product itself: package, resource and generator names.
            continue
        if any(relative == name and text in node.value for name, text in _WRITTEN_TO_FILES):
            continue
        problems.append(f"{relative}:{node.lineno}: {node.value!r}")
    return problems


def test_every_printed_mention_goes_through_the_name() -> None:
    root = Path(fecompiler.__file__).resolve().parent
    problems = []
    for path in sorted((root / "cli").rglob("*.py")):
        problems += _unnamed_literals(path, path.relative_to(root).as_posix())

    # `_REMOVED_DIRECT_FORM` is a module constant rewritten where it is printed.
    problems = [item for item in problems if not item.startswith("cli/main.py:")]
    assert problems == []
