# SoC Platform Bundle

This directory contains all non-CPU assets for YSYX AM SoC simulation:

- `ysyxSoCFull.v`: fixed SoC top RTL
- `perip/easy_box/easy_box_core_wrapper.v`: fixed CPU socket that instantiates
  bundled `cpu_top` adapters or the configured custom CPU top
- `perip/`: fixed SoC peripheral RTL
- `filelist.soc.f`: SoC-side RTL file list
- `driver/`: Verilator C++ driver (`main.cpp`, `dpi_mem.*`)
- `scripts/`: build/run helpers
- `tests/`: test programs and artifacts
- `Makefile`: simulation workflow entry

The shipped example CPU is intentionally placed outside this directory at:

- `examples/ysyx_00000000/rtl`
- `examples/ysyx_00000000/filelist.cpu.f`

`build_soc_sim.sh` and `gen_filelists.sh` locate that example by default. You can
override the selection with `CPU_ROOT=<path>`.

## Run Through `ecc-fe` Flow

From repo root:

```bash
python3 -m fecompiler.cli.main \
  --design ysyx_00000000_soc \
  --top ecos_sim_top \
  --cpu-top-module ysyx_00000000 \
  --cpu-filelist examples/ysyx_00000000/filelist.cpu.f \
  --soc-filelist fecompiler/thirdparty/SoC/filelist.soc.f \
  --testbench fecompiler/thirdparty/SoC/driver/main.cpp \
  --sim-cpp fecompiler/thirdparty/SoC/driver/dpi_mem.cpp \
  --sim-cpp fecompiler/thirdparty/SoC/driver/difftest.cpp \
  --sim-cflag=-Ifecompiler/thirdparty/SoC \
  --sim-ldflag=-ldl \
  --sim-compile-march rv32i_zicsr \
  --sim-compile-mabi ilp32 \
  --sim-compile-opt-level=-O2 \
  --sim-program add \
  --sim-arg=--max-cycles \
  --sim-arg=10000000 \
  --sim-arg=--diff \
  --sim-arg=--ref \
  --sim-arg=fecompiler/thirdparty/SoC/tools/riscv32-spike-so \
  --sim-arg=--diff-image-offset \
  --sim-arg=0x100 \
  --sim-arg=--diff-reset-vector \
  --sim-arg=0x80000000 \
  --rerun
```

Notes:

- Use `--sim-arg=...` form for values starting with `--` to avoid argparse ambiguity.
- `prepare` merges CPU+SoC filelists into one manifest, then `elab/lint/sim` consume that normalized input.
- `ysyx_00000000` implements RV32I + Zicsr and exposes the ECC-FE single-retirement difftest contract through `ysyx_00000000_difftest.sv`.
- `+define+ECOS_DIFFTEST` plus a `difftest_step` DPI import is the explicit capability contract. Selecting adapter RTL files directly generates that define automatically. Other custom CPUs remain on `difftest_stub.cpp`.
- A difftest run only passes after the driver reports at least one architectural comparison; a good trap without any comparison is an incomplete run.
- For one run per image, use `--sim-image <path>`.
- For batch run of all prebuilt test images, add `--sim-all-tests` (scans `fecompiler/thirdparty/SoC/tests/out/*.soc.bin`).
- Batch simulation writes per-case logs to `workspace_projects/<design>/sim_verilator/report/cases/<case>/log.txt`.
- For re-testing existing workspace without rerunning prepare/elab/lint, use `--sim-only --sim-reuse-binary`.

## Build and Run All Tests (SoC Makefile)

From this directory:

```bash
# build simulator binary once
make sim

# build all C tests into tests/out/*.soc.bin
make test-all

# run all prebuilt *.soc.bin, one log per case in tests/out/logs/
make run-all-prebuilt
```

Useful targets:

- `make list-tests`: list all test program names.
- `make run-all`: build all tests then run all tests.
