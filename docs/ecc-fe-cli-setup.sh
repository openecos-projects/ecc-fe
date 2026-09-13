#!/bin/sh
# Install the published ECC-FE runtime and its managed resources.
set -eu

RUNTIME_ONLY=0
CHECK_ONLY=0

usage() {
  echo "Usage: $0 [--runtime-only | --check-only]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --runtime-only) RUNTIME_ONLY=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ "$RUNTIME_ONLY" -eq 1 ] && [ "$CHECK_ONLY" -eq 1 ]; then
  echo "--runtime-only and --check-only cannot be combined" >&2
  exit 2
fi

command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required to install ECC-FE" >&2
  exit 1
}

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
ECC_FE_BIN_DIR="${ECC_FE_BIN_DIR:-$HOME/.local/bin}"
ECOS_REGISTRY_URL="${ECOS_REGISTRY_URL:-https://emin017.github.io/ecos-registry/tool-registry.json}"
export XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME ECC_FE_BIN_DIR ECOS_REGISTRY_URL
ECC_FE_SETUP_RUNTIME_ONLY="$RUNTIME_ONLY"
export ECC_FE_SETUP_RUNTIME_ONLY

if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ -x "$ECC_FE_BIN_DIR/ecc-fe" ]; then
    exec "$ECC_FE_BIN_DIR/ecc-fe" doctor
  fi
  command -v ecc-fe >/dev/null 2>&1 || {
      echo "ecc-fe is not installed in $ECC_FE_BIN_DIR or on PATH" >&2
      exit 1
    }
  exec ecc-fe doctor
fi

echo "Installing ECC-FE runtime from $ECOS_REGISTRY_URL"
python3 - <<'PY'
import copy
from contextlib import contextmanager
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def fail(message):
    raise SystemExit(message)


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        fail(f"Unsafe archive path: {value}")
    return path


def extract(archive, destination):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = safe_relative(member.filename)
                target = destination.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    fail("Zip symlinks are not accepted")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if mode:
                    target.chmod(mode & 0o777)
        return
    with tarfile.open(archive, "r:*") as bundle:
        members = bundle.getmembers()
        for member in members:
            relative = safe_relative(member.name)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                fail(f"Unsupported special file: {member.name}")
            if member.issym() or member.islnk():
                safe_relative(str(PurePosixPath(*relative.parts).parent / member.linkname))
        bundle.extractall(destination, members=members)


registry_url = os.environ["ECOS_REGISTRY_URL"]
with urllib.request.urlopen(registry_url, timeout=30) as response:
    registry = json.load(response)
if registry.get("schema_version") != 2:
    fail("Unsupported resource registry schema")
tool = next((item for item in registry.get("tools", []) if item.get("name") == "ecc-fe"), None)
if not tool or not tool.get("versions"):
    fail("ECC-FE runtime is missing from the registry")
version = tool["versions"][0]
machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
platform_id = f"{platform.system().lower()}-{machine}"
asset = version.get("platforms", {}).get(platform_id) or version.get("platforms", {}).get("all-platform")
if not asset:
    fail(f"ECC-FE has no published asset for {platform_id}")
sha256 = str(asset.get("sha256", "")).lower()
size = asset.get("size")
if (
    len(sha256) != 64
    or any(character not in "0123456789abcdef" for character in sha256)
    or not isinstance(size, int)
    or isinstance(size, bool)
    or size <= 0
):
    fail("ECC-FE asset has no valid SHA256 and size lock")
if asset.get("metadata_url"):
    with urllib.request.urlopen(asset["metadata_url"], timeout=30) as response:
        metadata = json.load(response)
    metadata_sha256 = str(metadata.get("sha256", "")).lower()
    metadata_size = metadata.get("size")
    if metadata_sha256 != sha256 or metadata_size != size:
        fail("ECC-FE release metadata does not match the registry SHA256 and size lock")

data_home = Path(os.environ["XDG_DATA_HOME"]).expanduser()
state_home = Path(os.environ["XDG_STATE_HOME"]).expanduser()
cache_home = Path(os.environ["XDG_CACHE_HOME"]).expanduser()
bin_dir = Path(os.environ["ECC_FE_BIN_DIR"]).expanduser()
tools_dir = data_home / "ecos-studio" / "tools"
destination = tools_dir / "ecc-fe" / str(version["version"])
manifest_path = state_home / "ecos-studio" / "resources" / "manifest.json"
cache_dir = cache_home / "ecos-studio" / "downloads"
cache_dir.mkdir(parents=True, exist_ok=True)
destination.parent.mkdir(parents=True, exist_ok=True)


def remove_path(path):
    if not os.path.lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def write_json_atomically(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        remove_path(temporary)


@contextmanager
def manifest_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield


def parse_existing_manifest(raw_bytes):
    manifest = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(manifest, dict):
        fail("ECC-FE resource manifest root must be an object")
    if not isinstance(manifest.get("installed", {}), dict):
        fail("ECC-FE resource manifest installed field must be an object")
    return manifest


def build_manifest(
    candidate_root,
    candidate_requires,
    existing_manifest,
    *,
    data_root=data_home,
    state_root=state_home,
):
    tools_root = data_root / "ecos-studio" / "tools"
    resources_root = state_root / "ecos-studio" / "resources"
    manifest = copy.deepcopy(existing_manifest) if existing_manifest else {
        "schema_version": 3,
        "resources_dir": str(resources_root),
        "tools_dir": str(tools_root),
        "pdks_dir": str(data_root / "ecos-studio" / "pdks"),
        "mpcs_dir": str(data_root / "ecos-studio" / "mpcs"),
        "installed": {},
        "pdk_references": [],
    }
    manifest["schema_version"] = max(int(manifest.get("schema_version", 1)), 3)
    manifest["resources_dir"] = str(resources_root)
    manifest["tools_dir"] = str(tools_root)
    manifest.setdefault("pdks_dir", str(data_root / "ecos-studio" / "pdks"))
    manifest.setdefault("mpcs_dir", str(data_root / "ecos-studio" / "mpcs"))
    manifest.setdefault("pdk_references", [])
    manifest["required_resources"] = list(
        dict.fromkeys(str(item) for item in candidate_requires if str(item).strip())
    )
    installed = manifest.setdefault("installed", {})
    executable = candidate_root / "bin" / "ecc-fe"
    detected = [
        str(path.resolve())
        for path in sorted((candidate_root / "bin").iterdir())
        if path.is_file() and os.access(path, os.X_OK)
    ]
    installed["tool:ecc-fe"] = {
        "type": "tool",
        "name": "ecc-fe",
        "version": str(version["version"]),
        "path": str(candidate_root.resolve()),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256,
        "size": size,
        "detected_executables": detected,
        "executable": str(executable.resolve()),
        "active": True,
        "managed": True,
    }
    return manifest


def build_required_resource_manifest(existing_manifest, candidate_requires):
    manifest = copy.deepcopy(existing_manifest) if existing_manifest is not None else {
        "schema_version": 3,
        "resources_dir": str(manifest_path.parent),
        "tools_dir": str(tools_dir),
        "pdks_dir": str(data_home / "ecos-studio" / "pdks"),
        "mpcs_dir": str(data_home / "ecos-studio" / "mpcs"),
        "installed": {},
        "pdk_references": [],
    }
    manifest["schema_version"] = max(int(manifest.get("schema_version", 1)), 3)
    manifest["resources_dir"] = str(manifest_path.parent)
    manifest["tools_dir"] = str(tools_dir)
    manifest.setdefault("pdks_dir", str(data_home / "ecos-studio" / "pdks"))
    manifest.setdefault("mpcs_dir", str(data_home / "ecos-studio" / "mpcs"))
    manifest.setdefault("pdk_references", [])
    manifest.setdefault("installed", {})
    manifest["required_resources"] = list(
        dict.fromkeys(str(item) for item in candidate_requires if str(item).strip())
    )
    return manifest


def record_required_resource_snapshot(candidate_requires):
    with manifest_lock(manifest_path):
        if os.path.lexists(manifest_path) and not manifest_path.is_file():
            fail(f"ECC-FE manifest path is not a regular file: {manifest_path}")
        previous_exists = manifest_path.is_file()
        previous_bytes = manifest_path.read_bytes() if previous_exists else b""
        previous_manifest = (
            parse_existing_manifest(previous_bytes)
            if previous_exists
            else None
        )
        snapshot = build_required_resource_manifest(
            previous_manifest, candidate_requires
        )
        write_json_atomically(manifest_path, snapshot)
        return previous_exists, previous_bytes, previous_manifest


def merge_candidate_manifest(current_manifest, candidate_manifest, base_manifest):
    manifest = build_manifest(
        payload,
        version.get("requires", []),
        current_manifest,
        data_root=data_home,
        state_root=state_home,
    )
    current_installed = manifest.setdefault("installed", {})
    candidate_installed = candidate_manifest.get("installed", {})
    base_installed = (base_manifest or {}).get("installed", {})
    if isinstance(candidate_installed, dict) and isinstance(base_installed, dict):
        for resource_id, entry in candidate_installed.items():
            if resource_id not in base_installed or entry != base_installed[resource_id]:
                current_installed[resource_id] = copy.deepcopy(entry)

    def required_resources(value):
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    base_required = required_resources((base_manifest or {}).get("required_resources"))
    current_required = required_resources(
        current_manifest.get("required_resources") if current_manifest else None
    )
    candidate_required = required_resources(candidate_manifest.get("required_resources"))
    concurrent_required = [
        item for item in current_required if item not in base_required
    ]
    manifest["required_resources"] = list(
        dict.fromkeys([*candidate_required, *concurrent_required])
    )
    return manifest


def restore_links(link_backups, links, modified_links):
    for name in modified_links:
        link = links[name]
        remove_path(link)
        backup = link_backups.get(name)
        if backup is not None and os.path.lexists(backup):
            os.replace(backup, link)


def restore_manifest(previous_manifest_exists, previous_manifest_bytes):
    if previous_manifest_exists:
        temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(previous_manifest_bytes)
            os.replace(temporary, manifest_path)
        finally:
            remove_path(temporary)
    else:
        if not os.path.lexists(manifest_path):
            return
        if manifest_path.is_dir() and not manifest_path.is_symlink():
            raise RuntimeError(
                f"Refusing to remove unexpected manifest directory: {manifest_path}"
            )
        manifest_path.unlink()


def map_candidate_path(value, candidate_tools_dir):
    try:
        relative = Path(value).expanduser().resolve().relative_to(
            candidate_tools_dir.resolve()
        )
    except (OSError, ValueError):
        return value
    return str((tools_dir / relative).resolve())


def promote_candidate_resources(candidate_manifest, candidate_tools_dir):
    promotions = []
    installed = candidate_manifest.get("installed", {})
    if not isinstance(installed, dict):
        return promotions
    resource_backup_root = temporary / "resource-backups"
    try:
        for entry in installed.values():
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate_path = Path(raw_path).expanduser()
            try:
                relative = candidate_path.resolve().relative_to(candidate_tools_dir.resolve())
            except (OSError, ValueError):
                continue
            destination_path = tools_dir / relative
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path = resource_backup_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            had_backup = os.path.lexists(destination_path)
            if had_backup:
                os.replace(destination_path, backup_path)
            try:
                os.replace(candidate_path, destination_path)
            except Exception:
                if (
                    had_backup
                    and os.path.lexists(backup_path)
                    and not os.path.lexists(destination_path)
                ):
                    os.replace(backup_path, destination_path)
                raise
            promotions.append((destination_path, backup_path if had_backup else None))
            entry["path"] = map_candidate_path(raw_path, candidate_tools_dir)
            if isinstance(entry.get("executable"), str):
                entry["executable"] = map_candidate_path(
                    entry["executable"], candidate_tools_dir
                )
            detected = entry.get("detected_executables")
            if isinstance(detected, list):
                entry["detected_executables"] = [
                    map_candidate_path(item, candidate_tools_dir)
                    if isinstance(item, str)
                    else item
                    for item in detected
                ]
    except Exception:
        rollback_resource_promotions(promotions)
        raise
    return promotions


def rollback_resource_promotions(promotions):
    for destination_path, backup_path in reversed(promotions):
        remove_path(destination_path)
        if backup_path is not None and os.path.lexists(backup_path):
            os.replace(backup_path, destination_path)


def finalize_resource_promotions(promotions):
    for _, backup_path in promotions:
        if backup_path is not None:
            remove_path(backup_path)

with tempfile.TemporaryDirectory(prefix="ecc-fe-setup-", dir=cache_dir) as temporary:
    temporary = Path(temporary)
    archive = temporary / "runtime.download"
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(asset["url"], timeout=60) as response, archive.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            received += len(chunk)
    if received != size:
        fail(f"ECC-FE download size mismatch: expected {size}, received {received}")
    if digest.hexdigest() != sha256:
        fail("ECC-FE download SHA256 verification failed")
    extracted = temporary / "extracted"
    extracted.mkdir()
    extract(archive, extracted)
    prefix = asset.get("strip_prefix")
    payload = extracted.joinpath(*safe_relative(prefix).parts) if prefix else extracted
    if not (payload / "bin" / "ecc-fe").is_file() or not (payload / "fecompiler").is_dir():
        fail("ECC-FE runtime health check failed after extraction")
    previous_manifest_exists = manifest_path.is_file()
    previous_manifest_bytes = manifest_path.read_bytes() if previous_manifest_exists else b""
    existing_manifest = (
        parse_existing_manifest(previous_manifest_bytes)
        if previous_manifest_exists
        else None
    )
    candidate_state_home = temporary / "state"
    candidate_data_home = temporary / "data"
    candidate_tools_dir = candidate_data_home / "ecos-studio" / "tools"
    candidate_manifest_path = candidate_state_home / "ecos-studio" / "resources" / "manifest.json"
    candidate_manifest = build_manifest(
        payload,
        version.get("requires", []),
        existing_manifest,
        data_root=candidate_data_home,
        state_root=candidate_state_home,
    )
    write_json_atomically(candidate_manifest_path, candidate_manifest)

    if os.environ.get("ECC_FE_SETUP_RUNTIME_ONLY") != "1":
        (
            previous_manifest_exists,
            previous_manifest_bytes,
            existing_manifest,
        ) = record_required_resource_snapshot(version.get("requires", []))
        resource_env = os.environ.copy()
        resource_env["XDG_STATE_HOME"] = str(candidate_state_home)
        resource_env["XDG_DATA_HOME"] = str(candidate_data_home)
        resource_env["XDG_CACHE_HOME"] = str(cache_home)
        resource_env["ECC_FE_BIN_DIR"] = str(bin_dir)
        resource_env["ECOS_FE_COMPILER_ROOT"] = str(payload)
        subprocess.run(
            [str(payload / "bin" / "ecc-fe"), "resource", "install", "--required"],
            env=resource_env,
            check=True,
        )
        candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))

    destination_backup = destination.parent / f".backup-{destination.name}-{uuid.uuid4().hex}"
    link_backups = {}
    modified_links = set()
    links = {name: bin_dir / name for name in ("ecc-fe", "fecompiler")}
    temporary_links = []
    resource_promotions = []
    destination_backed_up = False
    destination_published = False
    committed = False
    rollback_snapshot_ready = False
    rollback_manifest_exists = False
    rollback_manifest_bytes = b""
    try:
        with manifest_lock(manifest_path):
            if os.path.lexists(manifest_path) and not manifest_path.is_file():
                fail(f"ECC-FE manifest path is not a regular file: {manifest_path}")
            for name, link in links.items():
                if os.path.lexists(link) and link.is_dir() and not link.is_symlink():
                    fail(f"Refusing to replace directory at ECC-FE link path: {link}")
            rollback_manifest_exists = manifest_path.is_file()
            rollback_manifest_bytes = (
                manifest_path.read_bytes() if rollback_manifest_exists else b""
            )
            rollback_snapshot_ready = True
            current_manifest = (
                parse_existing_manifest(rollback_manifest_bytes)
                if rollback_manifest_exists
                else None
            )
            try:
                resource_promotions = promote_candidate_resources(
                    candidate_manifest, candidate_tools_dir
                )
                final_manifest = merge_candidate_manifest(
                    current_manifest, candidate_manifest, existing_manifest
                )
                final_manifest["installed"]["tool:ecc-fe"]["path"] = str(
                    destination.resolve()
                )
                final_manifest["installed"]["tool:ecc-fe"]["executable"] = str(
                    (destination / "bin" / "ecc-fe").resolve()
                )
                final_manifest["installed"]["tool:ecc-fe"]["detected_executables"] = [
                    str((destination / "bin" / path.name).resolve())
                    for path in sorted((payload / "bin").iterdir())
                    if path.is_file() and os.access(path, os.X_OK)
                ]
                if os.path.lexists(destination):
                    os.replace(destination, destination_backup)
                    destination_backed_up = True
                os.replace(payload, destination)
                destination_published = True
                write_json_atomically(manifest_path, final_manifest)
                bin_dir.mkdir(parents=True, exist_ok=True)
                for name, link in links.items():
                    if os.path.lexists(link):
                        backup = bin_dir / f".backup-{name}-{uuid.uuid4().hex}"
                        os.replace(link, backup)
                        link_backups[name] = backup
                        modified_links.add(name)
                    target = destination / "bin" / name
                    if not target.exists():
                        target = destination / "bin" / "ecc-fe"
                    temporary_link = bin_dir / f".{name}.{uuid.uuid4().hex}.tmp"
                    temporary_links.append(temporary_link)
                    temporary_link.symlink_to(target)
                    modified_links.add(name)
                    os.replace(temporary_link, link)
                committed = True
            except Exception:
                for temporary_link in temporary_links:
                    remove_path(temporary_link)
                if destination_published:
                    remove_path(destination)
                if destination_backed_up and os.path.lexists(destination_backup):
                    os.replace(destination_backup, destination)
                if rollback_snapshot_ready:
                    restore_manifest(rollback_manifest_exists, rollback_manifest_bytes)
                restore_links(link_backups, links, modified_links)
                rollback_resource_promotions(resource_promotions)
                raise
    finally:
        if committed:
            remove_path(destination_backup)
            for backup in link_backups.values():
                remove_path(backup)
            finalize_resource_promotions(resource_promotions)
print(f"Installed ecc-fe {version['version']} at {destination}")
PY

"$ECC_FE_BIN_DIR/ecc-fe" --version
if ! printf '%s' ":$PATH:" | grep -F ":$ECC_FE_BIN_DIR:" >/dev/null 2>&1; then
  echo "Add this directory to PATH: $ECC_FE_BIN_DIR"
fi
