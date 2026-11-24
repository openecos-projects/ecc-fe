"""A test program is rebuilt only when something it is built from changed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fecompiler.tools.verilator import program_build


@pytest.fixture
def soc(tmp_path) -> Path:
    root = tmp_path / "soc"
    for name, text in {
        "scripts/build_test.sh": "#!/bin/sh\n",
        "tests/common/start.S": "_start:\n",
        "tests/common/include/klib.h": "#define KLIB 1\n",
        "tests/include/trap.h": "#define TRAP 1\n",
        "tests/benchmarks/coremark/core_main.c": "int main(void) { return 0; }\n",
        "tests/programs/add.c": "int main(void) { return 0; }\n",
        "tests/programs/sub.c": "int main(void) { return 1; }\n",
    }.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def _built(soc: Path, out_dir: Path, env: dict[str, str], name: str = "add"):
    """Pretend build_test.sh produced the image of *name* and record it."""
    script = soc / "scripts" / "build_test.sh"
    source = soc / "tests" / "programs" / f"{name}.c"
    command = [str(script), "--src", str(source), "--name", name, "--out_dir", str(out_dir)]
    out_dir.mkdir(parents=True, exist_ok=True)
    image = out_dir / f"{name}.soc.bin"
    image.write_bytes(b"\x13\x00\x00\x00")
    program_build.record(image, command, source, program_build.shared_inputs(script, env))
    return script, source, command, image


def _current(script: Path, source: Path, command: list[str], image: Path, env: dict[str, str]) -> bool:
    return program_build.is_current(
        image, command, source, program_build.shared_inputs(script, env)
    )


def test_an_unchanged_program_is_up_to_date(soc, tmp_path) -> None:
    env = {"PATH": os.environ["PATH"], "ECOS_SIM_OPT_LEVEL": "-O2"}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    assert _current(script, source, command, image, env) is True


@pytest.mark.parametrize(
    "edited",
    [
        "tests/programs/add.c",
        "tests/common/start.S",
        "tests/common/include/klib.h",
        "tests/include/trap.h",
        "tests/benchmarks/coremark/core_main.c",
        "scripts/build_test.sh",
    ],
)
def test_editing_anything_the_build_reads_makes_it_out_of_date(soc, tmp_path, edited) -> None:
    env = {"PATH": os.environ["PATH"]}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    with (soc / edited).open("a", encoding="utf-8") as stream:
        stream.write("/* edited */\n")

    assert _current(script, source, command, image, env) is False


def test_another_programs_source_does_not_matter(soc, tmp_path) -> None:
    env = {"PATH": os.environ["PATH"]}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    (soc / "tests/programs/sub.c").write_text("int main(void) { return 2; }\n", encoding="utf-8")

    assert _current(script, source, command, image, env) is True


@pytest.mark.parametrize(
    "change",
    [
        {"ECOS_SIM_OPT_LEVEL": "-O3"},
        {"ECOS_SIM_MARCH": "rv32i"},
        {"ECOS_COREMARK_ITERATIONS": "10"},
        {"SOC_USE_BOOTLOADER": "1"},
        {"SOC_PROGRAM_LINK_BASE": "0x30000000"},
    ],
)
def test_a_setting_the_script_reads_makes_it_out_of_date(soc, tmp_path, change) -> None:
    env = {"PATH": os.environ["PATH"], "ECOS_SIM_OPT_LEVEL": "-O2"}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    assert _current(script, source, command, image, {**env, **change}) is False
    # A variable the script does not read changes nothing.
    assert _current(script, source, command, image, {**env, "TERM": "dumb"}) is True


def test_another_compiler_makes_it_out_of_date(soc, tmp_path) -> None:
    def toolchain(name: str, body: str) -> str:
        directory = tmp_path / name
        directory.mkdir()
        compiler = directory / "riscv64-unknown-elf-gcc"
        compiler.write_text(body, encoding="utf-8")
        compiler.chmod(0o755)
        return str(directory)

    first = {"PATH": toolchain("gcc-13", "#!/bin/sh\necho 13\n")}
    second = {"PATH": toolchain("gcc-14", "#!/bin/sh\necho 14 with more\n")}
    script, source, command, image = _built(soc, tmp_path / "out", first)

    assert _current(script, source, command, image, first) is True
    assert _current(script, source, command, image, second) is False


def test_an_image_that_is_gone_or_was_replaced_is_out_of_date(soc, tmp_path) -> None:
    env = {"PATH": os.environ["PATH"]}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    image.write_bytes(b"\x00\x00\x00\x00")
    assert _current(script, source, command, image, env) is False
    image.unlink()
    assert _current(script, source, command, image, env) is False


def test_another_output_directory_is_another_build(soc, tmp_path) -> None:
    env = {"PATH": os.environ["PATH"]}
    script, source, command, image = _built(soc, tmp_path / "out", env)

    elsewhere = [*command[:-1], str(tmp_path / "elsewhere")]

    assert _current(script, source, elsewhere, image, env) is False


def test_a_script_of_unknown_layout_is_never_trusted(tmp_path) -> None:
    script = tmp_path / "my_build.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    source = tmp_path / "add.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    image = tmp_path / "out" / "add.soc.bin"
    image.parent.mkdir()
    image.write_bytes(b"\x13")
    command = [str(script), "--src", str(source), "--name", "add"]

    shared = program_build.shared_inputs(script, {"PATH": os.environ["PATH"]})
    program_build.record(image, command, source, shared)

    assert shared is None
    assert not program_build.record_path(image).exists()
    assert program_build.is_current(image, command, source, shared) is False


def test_the_record_lists_what_it_was_judged_by(soc, tmp_path) -> None:
    env = {"PATH": os.environ["PATH"], "ECOS_SIM_MARCH": "rv32im_zicsr", "HOME": "/home/me"}
    _script, source, _command, image = _built(soc, tmp_path / "out", env)

    recorded = json.loads(program_build.record_path(image).read_text(encoding="utf-8"))

    assert recorded["environment"] == {"ECOS_SIM_MARCH": "rv32im_zicsr"}
    assert list(recorded["source"]) == [str(source)]
    assert sorted(Path(path).name for path in recorded["files"]) == [
        "build_test.sh",
        "core_main.c",
        "klib.h",
        "start.S",
        "trap.h",
    ]
