"""Downloads that survive a bad network: continue, retry, mirrors, cache, offline."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli import resource_download
from fecompiler.cli.resource_download import DownloadError
from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.resources import current_registry_platform

CONTENT = bytes(range(256)) * 4096  # about 1 MB, not all one value
SHA256 = hashlib.sha256(CONTENT).hexdigest()


class _Server:
    """A local HTTP server whose failures a test scripts."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        # Per path: how many bytes each of the next answers sends before the
        # connection is cut; None sends everything.
        self.cut_after: dict[str, list[int | None]] = {}
        self.status: dict[str, list[int]] = {}
        self.honour_range = True
        self.requests: list[tuple[str, str | None]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                requested = self.headers.get("Range")
                server.requests.append((self.path, requested))
                forced = server.status.get(self.path)
                if forced:
                    self.send_response(forced.pop(0))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = server.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                if requested and server.honour_range:
                    start = int(requested.removeprefix("bytes=").rstrip("-"))
                    if start >= len(body):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                payload = body[start:]
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                cuts = server.cut_after.get(self.path)
                cut = cuts.pop(0) if cuts else None
                if cut is None:
                    self.wfile.write(payload)
                    return
                self.wfile.write(payload[:cut])
                self.wfile.flush()
                # Fewer bytes than promised, then the connection goes away.
                self.connection.close()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    instance = _Server()
    instance.files["/tool.tar.gz"] = CONTENT
    yield instance
    instance.close()


@pytest.fixture(autouse=True)
def _no_settings(monkeypatch) -> None:
    for name in ("ECOS_RESOURCE_MIRRORS", "ECOS_DOWNLOAD_ATTEMPTS", "ECOS_DOWNLOAD_TIMEOUT", "ECOS_REGISTRY_URL"):
        monkeypatch.delenv(name, raising=False)


def _fetch(url: str, target: Path, events: list | None = None, **options):
    waits: list[float] = []
    result = resource_download.fetch(
        url,
        target,
        size=options.pop("size", len(CONTENT)),
        sha256=options.pop("sha256", SHA256),
        notify=(lambda phase, fields: events.append((phase, fields))) if events is not None else None,
        sleep=waits.append,
        **options,
    )
    return result, waits


# --- one address --------------------------------------------------------------


def test_a_plain_download_is_written_and_checked(server, tmp_path) -> None:
    target = tmp_path / "cache" / "tool.archive"

    result, waits = _fetch(f"{server.url}/tool.tar.gz", target)

    assert result == f"{server.url}/tool.tar.gz"
    assert target.read_bytes() == CONTENT
    assert not target.with_name("tool.archive.part").exists()
    assert waits == []


def test_a_cut_download_continues_from_the_bytes_on_disk(server, tmp_path) -> None:
    server.cut_after["/tool.tar.gz"] = [300_000, 200_000]
    target = tmp_path / "tool.archive"
    events: list = []

    result, waits = _fetch(f"{server.url}/tool.tar.gz", target, events)

    assert target.read_bytes() == CONTENT
    assert [header for _, header in server.requests] == [None, "bytes=300000-", "bytes=500000-"]
    assert waits == [1.0, 3.0]
    phases = [phase for phase, _ in events]
    assert phases.count("retrying") == 2 and phases.count("resuming") == 2
    retry = next(fields for phase, fields in events if phase == "retrying")
    assert (retry["attempt"], retry["of"]) == (2, 4)
    assert "300000 of" in retry["reason"]


def test_what_was_received_is_kept_for_the_next_call(server, tmp_path) -> None:
    server.cut_after["/tool.tar.gz"] = [100_000, 0, 0, 0]
    target = tmp_path / "tool.archive"

    with pytest.raises(DownloadError, match="Download failed"):
        _fetch(f"{server.url}/tool.tar.gz", target)

    partial = target.with_name("tool.archive.part")
    assert partial.stat().st_size == 100_000 and not target.exists()
    server.requests.clear()

    _fetch(f"{server.url}/tool.tar.gz", target)

    assert target.read_bytes() == CONTENT
    assert server.requests == [("/tool.tar.gz", "bytes=100000-")]


def test_a_server_that_ignores_ranges_starts_over(server, tmp_path) -> None:
    server.honour_range = False
    server.cut_after["/tool.tar.gz"] = [400_000]
    target = tmp_path / "tool.archive"

    _fetch(f"{server.url}/tool.tar.gz", target)

    assert target.read_bytes() == CONTENT


def test_a_partial_file_of_something_else_is_discarded(server, tmp_path) -> None:
    target = tmp_path / "tool.archive"
    target.with_name("tool.archive.part").write_bytes(b"x" * 1000)

    _fetch(f"{server.url}/tool.tar.gz", target)

    # Continued from 1000 foreign bytes the checksum is wrong; the retry is clean.
    assert target.read_bytes() == CONTENT
    assert [header for _, header in server.requests] == ["bytes=1000-", None]


def test_a_partial_file_longer_than_the_archive_is_discarded(server, tmp_path) -> None:
    target = tmp_path / "tool.archive"
    target.with_name("tool.archive.part").write_bytes(b"x" * (len(CONTENT) + 5))

    _fetch(f"{server.url}/tool.tar.gz", target)

    assert target.read_bytes() == CONTENT
    assert server.requests == [("/tool.tar.gz", None)]


def test_a_temporary_server_error_is_retried(server, tmp_path) -> None:
    server.status["/tool.tar.gz"] = [503, 502]
    target = tmp_path / "tool.archive"

    _, waits = _fetch(f"{server.url}/tool.tar.gz", target)

    assert target.read_bytes() == CONTENT
    assert waits == [1.0, 3.0]


def test_not_found_is_not_retried(server, tmp_path) -> None:
    with pytest.raises(DownloadError, match="HTTP 404"):
        _, waits = _fetch(f"{server.url}/missing.tar.gz", tmp_path / "tool.archive")

    assert len(server.requests) == 1


def test_wrong_content_is_refused_and_nothing_is_kept(server, tmp_path) -> None:
    target = tmp_path / "tool.archive"

    with pytest.raises(DownloadError, match="SHA256 verification failed"):
        _fetch(f"{server.url}/tool.tar.gz", target, sha256="0" * 64)

    assert not target.exists() and not target.with_name("tool.archive.part").exists()
    assert len(server.requests) == 4


def test_the_number_of_attempts_can_be_set(server, tmp_path) -> None:
    server.status["/tool.tar.gz"] = [503] * 5

    with pytest.raises(DownloadError):
        _fetch(f"{server.url}/tool.tar.gz", tmp_path / "tool.archive", environ={"ECOS_DOWNLOAD_ATTEMPTS": "2"})

    assert len(server.requests) == 2


# --- cache --------------------------------------------------------------------


def test_a_complete_archive_in_the_cache_is_not_fetched_again(server, tmp_path) -> None:
    target = tmp_path / "tool.archive"
    target.write_bytes(CONTENT)
    events: list = []

    result, _ = _fetch(f"{server.url}/tool.tar.gz", target, events)

    assert result == "cached"
    assert server.requests == []
    assert events == [("cached", {"path": str(target)})]


def test_a_damaged_archive_in_the_cache_is_replaced(server, tmp_path) -> None:
    target = tmp_path / "tool.archive"
    target.write_bytes(CONTENT[:-1] + b"\x00")

    _fetch(f"{server.url}/tool.tar.gz", target)

    assert target.read_bytes() == CONTENT


# --- mirrors ------------------------------------------------------------------


def test_mirror_rules_are_read_from_the_environment() -> None:
    env = {"ECOS_RESOURCE_MIRRORS": "https://github.com/=https://mirror.example/gh/, https://a/=http://b/"}

    rules = resource_download.mirror_rules(env)

    assert rules == [("https://github.com/", "https://mirror.example/gh/"), ("https://a/", "http://b/")]
    assert resource_download.candidate_urls("https://github.com/o/r/tool.tgz", rules) == [
        "https://mirror.example/gh/o/r/tool.tgz",
        "https://github.com/o/r/tool.tgz",
    ]
    assert resource_download.candidate_urls("https://elsewhere/tool.tgz", rules) == ["https://elsewhere/tool.tgz"]
    assert resource_download.mirror_rules({}) == []


@pytest.mark.parametrize("value", ["nonsense", "=https://x/", "https://x/="])
def test_a_malformed_mirror_rule_is_an_error(value) -> None:
    with pytest.raises(DownloadError, match="ECOS_RESOURCE_MIRRORS"):
        resource_download.mirror_rules({"ECOS_RESOURCE_MIRRORS": value})


def test_the_mirror_is_used_first(server, tmp_path) -> None:
    server.files["/mirror/tool.tar.gz"] = CONTENT
    events: list = []
    env = {"ECOS_RESOURCE_MIRRORS": f"http://origin.invalid/={server.url}/mirror/"}

    result, _ = _fetch("http://origin.invalid/tool.tar.gz", tmp_path / "tool.archive", events, environ=env)

    assert result == f"{server.url}/mirror/tool.tar.gz"
    assert events[0] == ("mirror", {"url": f"{server.url}/mirror/tool.tar.gz"})


def test_a_mirror_without_the_file_falls_back_to_the_original(server, tmp_path) -> None:
    env = {"ECOS_RESOURCE_MIRRORS": f"{server.url}/={server.url}/mirror/"}

    result, _ = _fetch(f"{server.url}/tool.tar.gz", tmp_path / "tool.archive", environ=env)

    assert result == f"{server.url}/tool.tar.gz"
    assert [path for path, _ in server.requests] == ["/mirror/tool.tar.gz", "/tool.tar.gz"]


def test_a_mirror_serving_other_content_is_not_believed(server, tmp_path) -> None:
    server.files["/mirror/tool.tar.gz"] = b"z" * len(CONTENT)
    env = {"ECOS_RESOURCE_MIRRORS": f"{server.url}/={server.url}/mirror/", "ECOS_DOWNLOAD_ATTEMPTS": "1"}
    target = tmp_path / "tool.archive"

    result, _ = _fetch(f"{server.url}/tool.tar.gz", target, environ=env)

    assert result == f"{server.url}/tool.tar.gz"
    assert target.read_bytes() == CONTENT


# --- through the resource manager ---------------------------------------------


def _tool_archive(tmp_path: Path) -> tuple[bytes, str]:
    root = tmp_path / "payload" / "slang-1" / "bin"
    root.mkdir(parents=True)
    slang = root / "slang"
    slang.write_text("#!/bin/sh\necho slang 1\n", encoding="utf-8")
    slang.chmod(0o755)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        bundle.add(root.parent, arcname="slang-1")
    content = buffer.getvalue()
    return content, hashlib.sha256(content).hexdigest()


def _registry_payload(url: str, sha256: str, size: int, **asset: object) -> dict:
    return {
        "schema_version": 2,
        "tools": [
            {
                "name": "slang",
                "display_name": "Slang",
                "versions": [
                    {
                        "version": "1",
                        "requires": [],
                        "platforms": {
                            current_registry_platform(): {
                                "url": url,
                                "sha256": sha256,
                                "size": size,
                                "strip_prefix": "slang-1",
                                **asset,
                            }
                        },
                    }
                ],
            }
        ],
    }


def _manager(tmp_path: Path, registry_url: str) -> ResourceManager:
    return ResourceManager(
        registry_url=registry_url,
        manifest_path=tmp_path / "state" / "manifest.json",
        tools_dir=tmp_path / "tools",
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture
def served_tool(server, tmp_path):
    content, sha256 = _tool_archive(tmp_path)
    server.files["/slang.tar.gz"] = content
    server.files["/registry.json"] = json.dumps(
        _registry_payload(f"{server.url}/slang.tar.gz", sha256, len(content))
    ).encode()
    return content, sha256


def test_install_downloads_and_leaves_nothing_in_the_cache_but_the_registry(server, served_tool, tmp_path) -> None:
    manager = _manager(tmp_path, f"{server.url}/registry.json")

    (record,) = manager.install("slang")

    assert record["status"] == "installed"
    assert (tmp_path / "tools" / "slang" / "1" / "bin" / "slang").is_file()
    assert sorted(path.name for path in (tmp_path / "cache").iterdir()) == ["registry.json"]


def test_an_install_that_fails_after_the_download_keeps_the_archive(server, served_tool, tmp_path, monkeypatch) -> None:
    content, sha256 = served_tool
    manager = _manager(tmp_path, f"{server.url}/registry.json")
    from fecompiler.cli import resource_manager as module

    def broken(archive, destination):
        raise ResourceManagerError("disk full")

    monkeypatch.setattr(module, "_extract_archive", broken)
    with pytest.raises(ResourceManagerError, match="disk full"):
        manager.install("slang")
    kept = tmp_path / "cache" / f"slang-1-{sha256[:16]}.archive"
    assert kept.read_bytes() == content
    monkeypatch.undo()
    downloads = [path for path, _ in server.requests if path == "/slang.tar.gz"]

    (record,) = manager.install("slang")

    assert record["status"] == "installed"
    # The second attempt did not download the archive again.
    assert [path for path, _ in server.requests if path == "/slang.tar.gz"] == downloads
    assert not kept.exists()


def test_install_reports_retries_through_progress(server, served_tool, tmp_path, monkeypatch) -> None:
    server.cut_after["/slang.tar.gz"] = [50]
    monkeypatch.setattr(resource_download.time, "sleep", lambda seconds: None)
    events: list[dict] = []

    _manager(tmp_path, f"{server.url}/registry.json").install("slang", progress=events.append)

    phases = [event["phase"] for event in events]
    assert "retrying" in phases and "resuming" in phases and phases[-1] == "done"
    retry = next(event for event in events if event["phase"] == "retrying")
    assert retry["resource_id"] == "tool:slang" and retry["attempt"] == 2


def test_install_from_a_local_archive_needs_no_download(server, served_tool, tmp_path) -> None:
    content, _ = served_tool
    local = tmp_path / "downloads" / "slang.tar.gz"
    local.parent.mkdir()
    local.write_bytes(content)
    manager = _manager(tmp_path, f"{server.url}/registry.json")

    (record,) = manager.install("slang", archive=local)

    assert record["status"] == "installed"
    assert [path for path, _ in server.requests] == ["/registry.json"]
    assert local.read_bytes() == content


def test_a_local_archive_that_is_not_the_named_one_is_refused(server, served_tool, tmp_path) -> None:
    local = tmp_path / "other.tar.gz"
    local.write_bytes(b"not the archive")
    manager = _manager(tmp_path, f"{server.url}/registry.json")

    with pytest.raises(ResourceManagerError, match="is not the archive of slang 1"):
        manager.install("slang", archive=local)
    with pytest.raises(ResourceManagerError, match="Archive not found"):
        manager.install("slang", archive=tmp_path / "gone.tar.gz")

    assert not (tmp_path / "tools" / "slang" / "1").exists()


def test_the_registry_can_be_a_local_file(served_tool, tmp_path) -> None:
    content, sha256 = served_tool
    local = tmp_path / "slang.tar.gz"
    local.write_bytes(content)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(_registry_payload("http://unreachable.invalid/slang.tar.gz", sha256, len(content))))
    manager = _manager(tmp_path, str(registry))

    (record,) = manager.install("slang", archive=local)

    assert record["status"] == "installed"
    assert manager.registry_note is None
    # A local registry is not saved as the network copy.
    assert not (tmp_path / "cache" / "registry.json").exists()


def test_without_a_network_the_saved_registry_is_used_and_said_so(server, served_tool, tmp_path) -> None:
    content, _ = served_tool
    manager = _manager(tmp_path, f"{server.url}/registry.json")
    manager.registry()
    assert manager.registry_note is None
    local = tmp_path / "slang.tar.gz"
    local.write_bytes(content)
    server.status["/registry.json"] = [503] * 10
    server.status["/slang.tar.gz"] = [503] * 10

    (record,) = manager.install("slang", archive=local)

    assert record["status"] == "installed"
    assert "using the copy saved at" in manager.registry_note


def test_without_a_network_and_without_a_saved_registry_it_fails(server, tmp_path) -> None:
    server.status["/registry.json"] = [503]

    with pytest.raises(ResourceManagerError, match="Unable to read resource registry"):
        _manager(tmp_path, f"{server.url}/registry.json").registry()


def test_a_saved_registry_that_is_damaged_is_not_used(server, tmp_path) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "registry.json").write_text("{broken", encoding="utf-8")
    server.status["/registry.json"] = [503]

    with pytest.raises(ResourceManagerError, match="Unable to read resource registry"):
        _manager(tmp_path, f"{server.url}/registry.json").registry()


def test_unreachable_release_metadata_does_not_stop_an_install(server, tmp_path) -> None:
    content, sha256 = _tool_archive(tmp_path)
    server.files["/slang.tar.gz"] = content
    server.files["/registry.json"] = json.dumps(
        _registry_payload(
            f"{server.url}/slang.tar.gz", sha256, len(content),
            metadata_url="http://127.0.0.1:9/metadata.json",
        )
    ).encode()

    (record,) = _manager(tmp_path, f"{server.url}/registry.json").install("slang")

    assert record["status"] == "installed"


def test_release_metadata_that_disagrees_still_stops_an_install(server, tmp_path) -> None:
    content, sha256 = _tool_archive(tmp_path)
    server.files["/slang.tar.gz"] = content
    server.files["/metadata.json"] = json.dumps({"sha256": "1" * 64, "size": len(content)}).encode()
    server.files["/registry.json"] = json.dumps(
        _registry_payload(
            f"{server.url}/slang.tar.gz", sha256, len(content),
            metadata_url=f"{server.url}/metadata.json",
        )
    ).encode()

    with pytest.raises(ResourceManagerError, match="does not match the registry"):
        _manager(tmp_path, f"{server.url}/registry.json").install("slang")


# --- the command --------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch, tmp_path, server, served_tool):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("ECOS_REGISTRY_URL", f"{server.url}/registry.json")
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)
    monkeypatch.setattr(resource_download.time, "sleep", lambda seconds: None)
    return served_tool


def _cli(capsys, *args: str) -> tuple[int, list[dict], str]:
    exit_code = cli_main.run(["resource", *args, "--json"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)["records"], captured.err


def test_resource_install_with_a_local_archive(cli_env, tmp_path, capsys) -> None:
    content, _ = cli_env
    local = tmp_path / "slang.tar.gz"
    local.write_bytes(content)

    exit_code, records, _ = _cli(capsys, "install", "slang", "--archive", str(local))

    assert exit_code == 0
    assert records[0]["status"] == "installed"


def test_archive_cannot_be_combined_with_required(cli_env, tmp_path, capsys) -> None:
    exit_code, records, _ = _cli(capsys, "install", "--required", "--archive", str(tmp_path / "x.tgz"))

    assert exit_code == 2
    assert records[0]["error"] == "invalid_resource_selection"


def test_resource_install_says_when_it_retries(cli_env, server, capsys) -> None:
    server.cut_after["/slang.tar.gz"] = [50]

    exit_code, records, stderr = _cli(capsys, "install", "slang")

    assert exit_code == 0 and records[0]["status"] == "installed"
    assert "tool:slang: retrying (attempt 2 of 4 in 1s: the download ended at 50 of" in stderr
    assert "tool:slang: resuming" in stderr


def test_resource_install_says_when_the_saved_registry_is_used(cli_env, server, tmp_path, capsys) -> None:
    content, _ = cli_env
    assert _cli(capsys, "list")[0] == 0
    local = tmp_path / "slang.tar.gz"
    local.write_bytes(content)
    server.status["/registry.json"] = [503] * 10

    exit_code, records, stderr = _cli(capsys, "install", "slang", "--archive", str(local))

    assert exit_code == 0 and records[0]["status"] == "installed"
    assert "ecc-fe: the resource registry could not be read" in stderr
