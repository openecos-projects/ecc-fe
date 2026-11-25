"""Archive links and size, post-install output, and a damaged manifest."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli import resource_manager as module
from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.resources import (
    ResourceManifestError,
    current_registry_platform,
    read_resource_manifest,
    resource_manifest_problem,
)


def _tar(path: Path, entries: list[tuple]) -> Path:
    """Write a tar.gz from (kind, name, value[, mode]) entries."""
    with tarfile.open(path, "w:gz") as bundle:
        for kind, name, value, *rest in entries:
            member = tarfile.TarInfo(name)
            if kind == "dir":
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
                bundle.addfile(member)
            elif kind == "file":
                data = value.encode()
                member.size = len(data)
                member.mode = rest[0] if rest else 0o644
                bundle.addfile(member, io.BytesIO(data))
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = value
                bundle.addfile(member)
            elif kind == "hardlink":
                member.type = tarfile.LNKTYPE
                member.linkname = value
                bundle.addfile(member)
    return path


_TOOL = [
    ("dir", "tool", None),
    ("dir", "tool/bin", None),
    ("dir", "tool/lib", None),
    ("file", "tool/lib/slang-real", "#!/bin/sh\necho slang\n", 0o755),
]


def _extract(tmp_path: Path, entries: list[tuple]) -> Path:
    archive = _tar(tmp_path / "a.tar.gz", entries)
    destination = tmp_path / "out"
    destination.mkdir()
    module._extract_archive(archive, destination)
    return destination


# --- R7: links ------------------------------------------------------------------


def test_a_link_that_goes_up_and_stays_inside_is_extracted(tmp_path) -> None:
    out = _extract(tmp_path, [*_TOOL, ("symlink", "tool/bin/slang", "../lib/slang-real")])

    link = out / "tool" / "bin" / "slang"
    assert link.is_symlink()
    assert link.resolve() == (out / "tool" / "lib" / "slang-real").resolve()
    assert link.read_text(encoding="utf-8").endswith("echo slang\n")


def test_a_link_between_directories_at_the_top_is_extracted(tmp_path) -> None:
    out = _extract(tmp_path, [*_TOOL, ("symlink", "tool/latest", "../tool/lib")])

    assert (out / "tool" / "latest" / "slang-real").is_file()


@pytest.mark.parametrize(
    "target",
    ["../../../etc/passwd", "../../..", "/etc/passwd", "../lib/../../../outside"],
)
def test_a_link_that_leaves_the_archive_is_refused(tmp_path, target) -> None:
    with pytest.raises(ResourceManagerError, match="Archive link escapes destination"):
        _extract(tmp_path, [*_TOOL, ("symlink", "tool/bin/slang", target)])

    assert list((tmp_path / "out").iterdir()) == []


def test_a_hard_link_names_a_member_from_the_archive_root(tmp_path) -> None:
    out = _extract(tmp_path, [*_TOOL, ("hardlink", "tool/bin/slang", "tool/lib/slang-real")])

    assert (out / "tool" / "bin" / "slang").read_text(encoding="utf-8").endswith("echo slang\n")


def test_a_hard_link_that_leaves_the_archive_is_refused(tmp_path) -> None:
    with pytest.raises(ResourceManagerError, match="Archive link escapes destination"):
        _extract(tmp_path, [*_TOOL, ("hardlink", "tool/bin/slang", "../outside")])


def test_a_member_outside_the_archive_is_still_refused(tmp_path) -> None:
    with pytest.raises(ResourceManagerError, match="Archive path escapes destination"):
        _extract(tmp_path, [("file", "../escaped", "x")])


# --- R8: filter and size --------------------------------------------------------


def test_executable_bits_are_kept_and_set_id_bits_are_dropped(tmp_path) -> None:
    out = _extract(tmp_path, [*_TOOL, ("file", "tool/bin/su", "x", 0o4755)])

    mode = (out / "tool" / "bin" / "su").stat().st_mode
    assert mode & stat.S_IXUSR
    assert (out / "tool" / "lib" / "slang-real").stat().st_mode & stat.S_IXUSR
    if hasattr(tarfile, "data_filter"):
        assert not mode & stat.S_ISUID


def test_a_tar_that_unpacks_beyond_the_limit_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECOS_EXTRACT_MAX_BYTES", "100")

    with pytest.raises(ResourceManagerError, match="unpacks to 500 bytes, more than the limit of 100"):
        _extract(tmp_path, [("file", "big", "x" * 500)])

    assert list((tmp_path / "out").iterdir()) == []


def test_a_zip_that_unpacks_beyond_the_limit_is_refused(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("big", "x" * 100_000)
    assert archive.stat().st_size < 1000
    monkeypatch.setenv("ECOS_EXTRACT_MAX_BYTES", "50000")
    (tmp_path / "out").mkdir()

    with pytest.raises(ResourceManagerError, match="unpacks to 100000 bytes"):
        module._extract_archive(archive, tmp_path / "out")

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.parametrize("value", ["", "nonsense", "0", "-5"])
def test_an_unusable_limit_falls_back_to_the_default(monkeypatch, value) -> None:
    monkeypatch.setenv("ECOS_EXTRACT_MAX_BYTES", value)

    assert module._extract_limit() == module.DEFAULT_EXTRACT_LIMIT


# --- R9: post-install output ----------------------------------------------------


def _installable(tmp_path: Path, post_install: list[dict]) -> ResourceManager:
    archive = _tar(
        tmp_path / "slang.tar.gz",
        [("dir", "slang", None), ("dir", "slang/bin", None), ("file", "slang/bin/slang", "#!/bin/sh\n", 0o755)],
    )
    content = archive.read_bytes()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tools": [
                    {
                        "name": "slang",
                        "versions": [
                            {
                                "version": "1",
                                "requires": [],
                                "platforms": {
                                    current_registry_platform(): {
                                        "url": archive.as_uri(),
                                        "sha256": hashlib.sha256(content).hexdigest(),
                                        "size": len(content),
                                        "strip_prefix": "slang",
                                        "post_install": post_install,
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ResourceManager(
        registry_url=str(registry),
        manifest_path=tmp_path / "state" / "manifest.json",
        tools_dir=tmp_path / "tools",
        cache_dir=tmp_path / "cache",
    )


def _python(code: str) -> dict:
    return {"command": [sys.executable, "-c", code]}


def test_post_install_output_goes_to_stderr(tmp_path, capfd) -> None:
    manager = _installable(
        tmp_path, [_python("import sys; print('to stdout'); print('to stderr', file=sys.stderr)")]
    )

    (record,) = manager.install("slang")

    captured = capfd.readouterr()
    assert record["status"] == "installed"
    assert captured.out == ""
    assert "to stdout" in captured.err and "to stderr" in captured.err


def test_a_failing_post_install_command_says_what_it_printed(tmp_path, capfd) -> None:
    manager = _installable(
        tmp_path, [_python("import sys; print('line 1'); print('patchelf: not found'); sys.exit(3)")]
    )

    with pytest.raises(
        ResourceManagerError,
        match=r"Post-install command failed with status 3: .*: line 1 \| patchelf: not found",
    ):
        manager.install("slang")

    assert capfd.readouterr().out == ""
    assert not (tmp_path / "tools" / "slang" / "1").exists()


def test_a_post_install_command_that_cannot_be_started_is_reported(tmp_path) -> None:
    manager = _installable(tmp_path, [{"command": ["/nonexistent/tool"]}])

    with pytest.raises(ResourceManagerError, match="Post-install command failed: /nonexistent/tool"):
        manager.install("slang")


def test_json_output_of_install_is_one_document_with_post_install_output(tmp_path, monkeypatch, capfd) -> None:
    manager = _installable(tmp_path, [_python("print('noise from the installer')")])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xstate"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xcache"))
    monkeypatch.setenv("ECOS_REGISTRY_URL", manager.registry_url)

    assert cli_main.run(["resource", "install", "slang", "--json"]) == 0

    captured = capfd.readouterr()
    assert json.loads(captured.out)["records"][0]["status"] == "installed"
    assert "noise from the installer" in captured.err


# --- R13: a damaged manifest ----------------------------------------------------


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("", "it is empty"),
        ("   \n", "it is empty"),
        ("{broken", "it is not valid JSON"),
        ("[]", "its content is not a JSON object"),
        ('{"installed": []}', 'its "installed" field is not an object'),
    ],
)
def test_a_damaged_manifest_is_named_with_its_path_and_reason(tmp_path, content, reason) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ResourceManifestError) as raised:
        read_resource_manifest(path, strict=True)

    error = raised.value
    assert error.path == path and reason in error.reason
    assert str(error).startswith(f"resource manifest {path} cannot be used: {reason}")
    assert str(path) in error.remediation
    # Read leniently it is empty, and the problem can be asked for.
    assert read_resource_manifest(path)["installed"] == {}
    assert reason in resource_manifest_problem(path).reason


def test_a_missing_or_sound_manifest_has_no_problem(tmp_path) -> None:
    path = tmp_path / "manifest.json"

    assert resource_manifest_problem(path) is None
    assert read_resource_manifest(path, strict=True)["installed"] == {}
    path.write_text('{"installed": {}}', encoding="utf-8")
    assert resource_manifest_problem(path) is None


def test_a_manifest_that_is_a_directory_cannot_be_read(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.mkdir()

    with pytest.raises(ResourceManifestError, match="it cannot be read"):
        read_resource_manifest(path, strict=True)


@pytest.fixture
def damaged(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)
    path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    return path


def _cli(capsys, *args: str) -> tuple[int, list[dict], str]:
    exit_code = cli_main.run([*args, "--json"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)["records"], captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["resource", "status"],
        ["resource", "list", "--installed"],
        ["resource", "prune"],
        ["resource", "uninstall", "slang"],
    ],
    ids=lambda arguments: " ".join(arguments),
)
def test_resource_commands_report_a_damaged_manifest_with_context(damaged, capsys, arguments) -> None:
    exit_code, (record,), stderr = _cli(capsys, *arguments)

    assert exit_code == 1
    assert record["error"] == "invalid_resource_manifest"
    assert record["path"] == str(damaged)
    assert "it is not valid JSON" in record["reason"]
    assert str(damaged) in record["message"] and str(damaged) in record["remediation"]
    # The command reports it itself; no second warning.
    assert stderr == ""


def test_install_reports_a_damaged_manifest_instead_of_an_internal_error(damaged, tmp_path, monkeypatch, capsys) -> None:
    manager = _installable(tmp_path, [])
    monkeypatch.setenv("ECOS_REGISTRY_URL", manager.registry_url)

    exit_code, (record,), _ = _cli(capsys, "resource", "install", "slang")

    assert exit_code == 1
    assert record["error"] == "invalid_resource_manifest"
    assert record["path"] == str(damaged)


def test_other_commands_warn_that_the_manifest_is_ignored(damaged, capsys) -> None:
    assert cli_main.run(["version"]) == 0
    stderr = capsys.readouterr().err

    assert stderr.startswith(f"ecc-fe: warning: resource manifest {damaged} cannot be used: it is not valid JSON")
    assert "the tools it lists are not used" in stderr
    assert "ecc-fe resource status" in stderr


def test_a_sound_manifest_gives_no_warning(damaged, capsys) -> None:
    damaged.write_text('{"installed": {}}', encoding="utf-8")

    assert cli_main.run(["version"]) == 0
    assert capsys.readouterr().err == ""


def test_doctor_names_the_repair_for_a_damaged_manifest(damaged, capsys) -> None:
    _, records, _ = _cli(capsys, "doctor")

    check = next(record for record in records if record.get("check") == "resource_manifest")
    assert check["status"] == "attention"
    assert str(damaged) in check["error"] and str(damaged) in check["remediation"]
