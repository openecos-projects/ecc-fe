#!/usr/bin/env python3
"""Prepare rt-thread-am BSP files needed by the ECOS frontend SoC smoke run.

The upstream BSP normally generates rtconfig.h and files.mk through SCons.
ECOS GUI users often run the simulator from a minimal runtime environment, so
this helper provides a deterministic fallback for the known abstract-machine BSP
configuration used by the frontend RT-Thread test.
"""

from __future__ import annotations

import argparse
from pathlib import Path


RTTHREAD_SOURCE_FILES = (
    "components/dfs/dfs_v1/filesystems/devfs/devfs.c",
    "components/dfs/dfs_v1/filesystems/elmfat/dfs_elm.c",
    "components/dfs/dfs_v1/filesystems/elmfat/ff.c",
    "components/dfs/dfs_v1/filesystems/elmfat/ffunicode.c",
    "components/dfs/dfs_v1/filesystems/romfs/dfs_romfs.c",
    "components/dfs/dfs_v1/filesystems/romfs/romfs.c",
    "components/dfs/dfs_v1/src/dfs.c",
    "components/dfs/dfs_v1/src/dfs_file.c",
    "components/dfs/dfs_v1/src/dfs_fs.c",
    "components/dfs/dfs_v1/src/dfs_posix.c",
    "components/drivers/cputime/cputime.c",
    "components/drivers/cputime/cputimer.c",
    "components/drivers/ipc/completion.c",
    "components/drivers/ipc/dataqueue.c",
    "components/drivers/ipc/pipe.c",
    "components/drivers/ipc/ringblk_buf.c",
    "components/drivers/ipc/ringbuffer.c",
    "components/drivers/ipc/waitqueue.c",
    "components/drivers/ipc/workqueue.c",
    "components/drivers/misc/rt_null.c",
    "components/drivers/misc/rt_random.c",
    "components/drivers/misc/rt_zero.c",
    "components/drivers/rtc/rtc.c",
    "components/drivers/serial/serial.c",
    "components/finsh/cmd.c",
    "components/finsh/msh.c",
    "components/finsh/msh_file.c",
    "components/finsh/msh_parse.c",
    "components/finsh/shell.c",
    "components/libc/compilers/common/cctype.c",
    "components/libc/compilers/common/cstdio.c",
    "components/libc/compilers/common/cstdlib.c",
    "components/libc/compilers/common/cstring.c",
    "components/libc/compilers/common/ctime.c",
    "components/libc/compilers/common/cwchar.c",
    "components/utilities/libadt/avl.c",
    "components/utilities/utest/utest.c",
    "src/clock.c",
    "src/components.c",
    "src/device.c",
    "src/idle.c",
    "src/ipc.c",
    "src/irq.c",
    "src/kservice.c",
    "src/mem.c",
    "src/mempool.c",
    "src/object.c",
    "src/scheduler_up.c",
    "src/thread.c",
    "src/timer.c",
)


RTTHREAD_INCLUDE_DIRS = (
    ".",
    "include",
    "components/libc/posix/io/poll",
    "components/libc/posix/io/stdio",
    "components/libc/posix/ipc",
    "components/libc/compilers/common/include",
    "components/drivers/include",
    "components/dfs/dfs_v1/include",
    "components/dfs/dfs_v1/filesystems/elmfat",
    "components/dfs/dfs_v1/filesystems/devfs",
    "components/dfs/dfs_v1/filesystems/romfs",
    "components/utilities/libadt",
    "components/utilities/utest",
    "components/finsh",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare rt-thread-am BSP fallback files")
    parser.add_argument("--bsp", required=True, help="rt-thread-am/bsp/abstract-machine path")
    parser.add_argument("--arch", default="riscv32-nemu", help="AbstractMachine arch name")
    parser.add_argument(
        "--am-apps-mk",
        default="",
        help="optional path where an empty AM apps makefile should be written",
    )
    parser.add_argument(
        "--am-apps-only",
        action="store_true",
        help="only write --am-apps-mk; kept for script compatibility",
    )
    args = parser.parse_args(argv)

    bsp = Path(args.bsp).expanduser().resolve()
    if not bsp.is_dir():
        raise SystemExit(f"rt-thread-am BSP not found: {bsp}")

    if not args.am_apps_only:
        write_rtconfig_h(bsp)
        write_files_mk(bsp)
    if args.am_apps_mk:
        write_empty_am_apps_mk(Path(args.am_apps_mk).expanduser())
    return 0


def write_rtconfig_h(bsp: Path) -> None:
    config_path = bsp / ".config"
    if not config_path.is_file():
        raise SystemExit(f"rt-thread-am .config not found: {config_path}")

    lines = [
        "#ifndef RT_CONFIG_H__",
        "#define RT_CONFIG_H__",
        '#include "extra.h"',
        "",
    ]
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not key.startswith("CONFIG_"):
            continue
        name = key[len("CONFIG_") :]
        if value == "y":
            lines.append(f"#define {name}")
        elif value == "n":
            continue
        else:
            lines.append(f"#define {name} {value}")

    lines.extend(["", "#endif", ""])
    (bsp / "rtconfig.h").write_text("\n".join(lines), encoding="utf-8")


def write_files_mk(bsp: Path) -> None:
    rtthread_root = bsp.parents[1]
    lines: list[str] = []
    for rel in RTTHREAD_SOURCE_FILES:
        path = rtthread_root / rel
        if path.is_file():
            lines.append(f"SRCS += {path}")

    for rel in RTTHREAD_INCLUDE_DIRS:
        if rel == ".":
            lines.append("CFLAGS += -I.")
            continue
        path = rtthread_root / rel
        if path.is_dir():
            lines.append(f"CFLAGS += -I{path}")

    (bsp / "files.mk").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_empty_am_apps_mk(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# ECOS frontend RT-Thread smoke test uses no bundled AM apps.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
