# SoC Platform Bundle

This directory contains all non-CPU assets for CL3/SoC simulation:

- `ysyxSoCFull.v`: fixed SoC top RTL
- `ysyx_00000000.sv`: CPU-to-SoC adapter
- `perip/`: fixed SoC peripheral RTL
- `filelist.soc.f`: SoC-side RTL file list
- `driver/`: Verilator C++ driver (`main.cpp`, `dpi_mem.*`)
- `scripts/`: build/run helpers
- `tests/`: test programs and artifacts
- `Makefile`: simulation workflow entry

CPU code is intentionally placed outside this directory at:

- `/home/luyoung/ecc-fe/docs/examples/cl3/cl3_verilog`
- `/home/luyoung/ecc-fe/docs/examples/cl3/filelist.cpu.f`

`build_soc_sim.sh` and `gen_filelists.sh` will read CPU files from that path by default.
You can override with `CPU_ROOT=<path>`.

## Run Through `ecc-fe` Flow

From repo root:

```bash
python3 -m fecompiler.cli.main \
  --design cl3_soc \
  --top ysyxSoCTop \
  --cpu-filelist /home/luyoung/ecc-fe/docs/examples/cl3/filelist.cpu.f \
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
