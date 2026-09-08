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
import hashlib
import json
import os
import platform
import shutil
import stat
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
tools_dir = data_home / "ecos-studio" / "tools"
destination = tools_dir / "ecc-fe" / str(version["version"])
manifest_path = state_home / "ecos-studio" / "resources" / "manifest.json"
cache_dir = cache_home / "ecos-studio" / "downloads"
cache_dir.mkdir(parents=True, exist_ok=True)
destination.parent.mkdir(parents=True, exist_ok=True)

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
    backup = destination.parent / f".backup-{destination.name}-{uuid.uuid4().hex}"
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(payload, destination)
        manifest = {
            "schema_version": 3,
            "resources_dir": str(manifest_path.parent),
            "tools_dir": str(tools_dir),
            "pdks_dir": str(data_home / "ecos-studio" / "pdks"),
            "mpcs_dir": str(data_home / "ecos-studio" / "mpcs"),
            "installed": {},
            "pdk_references": [],
        }
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = max(int(manifest.get("schema_version", 1)), 3)
        manifest["resources_dir"] = str(manifest_path.parent)
        manifest["tools_dir"] = str(tools_dir)
        manifest.setdefault("pdks_dir", str(data_home / "ecos-studio" / "pdks"))
        manifest.setdefault("mpcs_dir", str(data_home / "ecos-studio" / "mpcs"))
        manifest.setdefault("pdk_references", [])
        manifest["required_resources"] = list(dict.fromkeys(str(item) for item in version.get("requires", []) if str(item).strip()))
        installed = manifest.setdefault("installed", {})
        executable = destination / "bin" / "ecc-fe"
        detected = [str(path.resolve()) for path in sorted((destination / "bin").iterdir()) if path.is_file() and os.access(path, os.X_OK)]
        installed["tool:ecc-fe"] = {
            "type": "tool", "name": "ecc-fe", "version": str(version["version"]),
            "path": str(destination.resolve()), "installed_at": datetime.now(timezone.utc).isoformat(),
            "sha256": sha256, "size": size, "detected_executables": detected,
            "executable": str(executable.resolve()), "active": True, "managed": True,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_manifest = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
        temp_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_manifest, manifest_path)
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        if backup.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)

bin_dir = Path(os.environ["ECC_FE_BIN_DIR"]).expanduser()
bin_dir.mkdir(parents=True, exist_ok=True)
for name in ("ecc-fe", "fecompiler"):
    target = destination / "bin" / name
    if not target.exists():
        target = destination / "bin" / "ecc-fe"
    link = bin_dir / name
    temporary_link = bin_dir / f".{name}.{uuid.uuid4().hex}.tmp"
    temporary_link.symlink_to(target)
    os.replace(temporary_link, link)
print(f"Installed ecc-fe {version['version']} at {destination}")
PY

if [ "$RUNTIME_ONLY" -eq 0 ]; then
  echo "Installing required frontend resources"
  "$ECC_FE_BIN_DIR/ecc-fe" resource install --required
fi

"$ECC_FE_BIN_DIR/ecc-fe" --version
if ! printf '%s' ":$PATH:" | grep -F ":$ECC_FE_BIN_DIR:" >/dev/null 2>&1; then
  echo "Add this directory to PATH: $ECC_FE_BIN_DIR"
fi
