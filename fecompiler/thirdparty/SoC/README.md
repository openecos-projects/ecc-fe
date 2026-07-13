# SoC Platform Bundle

This directory contains all non-CPU assets for CL3/SoC simulation:

- `ysyxSoCFull.v`: fixed SoC top RTL
- `perip/easy_box/easy_box_core_wrapper.v`: fixed CPU socket that instantiates `cpu_top`
- `perip/`: fixed SoC peripheral RTL
- `filelist.soc.f`: SoC-side RTL file list
- `driver/`: Verilator C++ driver (`main.cpp`, `dpi_mem.*`)
- `scripts/`: build/run helpers
- `tests/`: test programs and artifacts
- `Makefile`: simulation workflow entry

CPU code is intentionally placed outside this directory at:

- `/home/luyoung/ecc-fe/examples/cl3/cl3_verilog`
- `/home/luyoung/ecc-fe/examples/cl3/filelist.cpu.f`

`build_soc_sim.sh` and `gen_filelists.sh` will read CPU files from that path by default.
You can override with `CPU_ROOT=<path>`.

## Run Through `ecc-fe` Flow

From repo root:

```bash
python3 -m fecompiler.cli.main \
  --design cl3_soc \
  --top ecos_sim_top \
  --cpu-filelist /home/luyoung/ecc-fe/examples/cl3/filelist.cpu.f \
  --soc-filelist /home/luyoung/ecc-fe/fecompiler/thirdparty/SoC/filelist.soc.f \
  --testbench /home/luyoung/ecc-fe/fecompiler/thirdparty/SoC/driver/main.cpp \
  --sim-cpp /home/luyoung/ecc-fe/fecompiler/thirdparty/SoC/driver/dpi_mem.cpp \
  --sim-cflag=-I/home/luyoung/ecc-fe/fecompiler/thirdparty/SoC \
  --sim-arg=--image \
  --sim-arg=/home/luyoung/ecc-fe/fecompiler/thirdparty/SoC/tests/out/min2.soc.bin \
  --sim-arg=--max-cycles \
  --sim-arg=2000000
```

Notes:

- Use `--sim-arg=...` form for values starting with `--` to avoid argparse ambiguity.
- `prepare` merges CPU+SoC filelists into one manifest, then `elab/lint/sim` consume that normalized input.
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
