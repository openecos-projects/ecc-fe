from __future__ import annotations

from pathlib import Path

import pytest

from fecompiler.catalog import cpu_filelist_supports_difftest
from fecompiler.tools.common.filelist import (
    filelist_tokens,
    is_rtl_path_token,
    parse_filelist,
)
from fecompiler.tools.prepare.runner import PrepareStep


def _write(path: Path, text: str = "module m; endmodule\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_managed_resources(monkeypatch, tmp_path) -> None:
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("ECOS_FE_RESOURCE_ROOTS", raising=False)


def test_entries_options_and_comments(tmp_path) -> None:
    top = _write(tmp_path / "rtl" / "top.sv")
    core = _write(tmp_path / "rtl" / "core.v")
    header = _write(tmp_path / "rtl" / "defs.svh", "`define X 1\n")
    spaced = _write(tmp_path / "rtl" / "with space.sv")
    filelist = _write(
        tmp_path / "rtl" / "design.f",
        "\n".join(
            [
                "# a comment",
                "// another comment",
                "`ifdef IGNORED",
                "+incdir+include+../shared",
                "+define+SYNTHESIS+WIDTH=32",
                "top.sv core.v   # two entries on one line",
                "defs.svh",
                '"with space.sv"',
                "-y libdir -v lib.v +libext+.v",
                "notes.txt",
                "$ENV_PATH/skipped.sv",
                "",
            ]
        ),
    )

    parsed = parse_filelist(filelist)

    assert parsed.path == filelist
    assert parsed.filelists == (filelist,)
    assert parsed.files == (top, core, header, spaced)
    assert parsed.sources == (top, core, spaced)
    assert parsed.missing == (tmp_path / "rtl" / "lib.v",)
    assert parsed.incdirs == (tmp_path / "rtl" / "include", tmp_path / "shared")
    assert parsed.defines == ("SYNTHESIS", "WIDTH=32")


@pytest.mark.parametrize("option", ["-f nested/inner.f", "-F nested/inner.f", "-fnested/inner.f", "-Fnested/inner.f"])
def test_nested_filelists_resolve_relative_to_their_own_directory(tmp_path, option) -> None:
    inner_source = _write(tmp_path / "nested" / "inner.sv")
    inner = _write(tmp_path / "nested" / "inner.f", "+define+INNER\ninner.sv\n")
    outer_source = _write(tmp_path / "outer.sv")
    outer = _write(tmp_path / "outer.f", f"{option}\nouter.sv\n")

    parsed = parse_filelist(outer)

    assert parsed.filelists == (outer, inner)
    assert parsed.files == (inner_source, outer_source)
    assert parsed.defines == ("INNER",)
    assert parsed.missing == ()


def test_cycles_and_missing_entries_are_reported_not_raised(tmp_path) -> None:
    a = tmp_path / "a.f"
    b = tmp_path / "b.f"
    _write(a, "-f b.f\nabsent.sv\n-f gone.f\n")
    _write(b, "-f a.f\n")

    parsed = parse_filelist(a)

    assert parsed.filelists == (a, b)
    assert parsed.files == ()
    assert parsed.missing == (tmp_path / "absent.sv", tmp_path / "gone.f")
    assert parse_filelist(tmp_path / "nowhere.f").missing == (tmp_path / "nowhere.f",)


def test_token_helpers() -> None:
    assert filelist_tokens("  a.sv 'b c.v'  # trailing") == ["a.sv", "b c.v"]
    assert filelist_tokens("`include \"x.svh\"") == []
    assert filelist_tokens("a.sv 'unbalanced") == ["a.sv", "'unbalanced"]
    assert is_rtl_path_token("core.SV")
    assert is_rtl_path_token("defs.vh")
    assert not is_rtl_path_token("+incdir+x.v")
    assert not is_rtl_path_token("-fnested.v")
    assert not is_rtl_path_token("$ROOT/a.v")
    assert not is_rtl_path_token("readme.md")


def test_difftest_capability_reads_defines_through_nested_filelists(tmp_path) -> None:
    _write(
        tmp_path / "adapter.sv",
        'module adapter;\n  import "DPI-C" function int difftest_step();\nendmodule\n',
    )
    _write(tmp_path / "inner.f", "+define+ECOS_DIFFTEST\nadapter.sv\n")
    outer = _write(tmp_path / "outer.f", "-F inner.f\n")
    plain = _write(tmp_path / "plain.f", "adapter.sv\n")

    assert parse_filelist(outer).defines == ("ECOS_DIFFTEST",)
    assert cpu_filelist_supports_difftest(outer) is True
    assert cpu_filelist_supports_difftest(plain) is False


def test_agrees_with_the_prepare_step_on_one_entry_per_line_filelists(tmp_path) -> None:
    """Prepare still has its own reader; both must see the same design."""
    _write(tmp_path / "rtl" / "a.sv")
    _write(tmp_path / "rtl" / "b.v")
    _write(tmp_path / "rtl" / "pkg.svh", "`define P 1\n")
    _write(tmp_path / "sub" / "c.sv")
    _write(tmp_path / "sub" / "sub.f", "+incdir+inc\n+define+SUB=2\nc.sv\n")
    filelist = _write(
        tmp_path / "rtl" / "top.f",
        "+incdir+include\n+define+TOP\na.sv\nb.v\npkg.svh\n-f ../sub/sub.f\n",
    )

    shared = parse_filelist(filelist)
    prepared = PrepareStep._parse_sv_filelist(str(filelist))

    assert [str(path) for path in shared.sources] == [
        str(path) for path in prepared["rtl_files"]
    ]
    assert [str(path) for path in shared.incdirs] == [
        str(path) for path in prepared["incdirs"]
    ]
    assert list(shared.defines) == prepared["defines"]
