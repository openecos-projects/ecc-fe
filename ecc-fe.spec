"""PyInstaller spec for the self-contained ECC-FE runtime."""

# ruff: noqa: F821

import os
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


ECC_FE_DIR = Path(SPECPATH)
RUNTIME_DATA_ROOT = ECC_FE_DIR / "fecompiler"
HIDDENIMPORTS = (
    "fecompiler.cli.rpc",
    "fecompiler.cli.workspace",
    "fecompiler.tools.prepare.runner",
    "fecompiler.tools.review.runner",
    "fecompiler.tools.slang.runner",
    "fecompiler.tools.verilator.runner",
)


def collect_runtime_data():
    datas = []
    for current_root, directories, files in os.walk(RUNTIME_DATA_ROOT):
        directories[:] = [
            name for name in directories if name not in {"__pycache__", "thirdparty"}
        ]
        current_path = Path(current_root)
        destination = Path("fecompiler") / current_path.relative_to(RUNTIME_DATA_ROOT)
        for name in files:
            source = current_path / name
            if source.suffix not in {".py", ".pyc"}:
                datas.append((str(source), str(destination)))
    return datas


datas = collect_runtime_data()
datas.extend(copy_metadata("ecc-fe"))

a = Analysis(
    [str(ECC_FE_DIR / "packaging" / "run_ecc_fe.py")],
    pathex=[str(ECC_FE_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=list(HIDDENIMPORTS),
    excludes=["tkinter", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ecc-fe",
    strip=False,
    upx=False,
    console=True,
)
