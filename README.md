# ecc-fe

`ecc-fe` is a pure Python framework for chip design flow orchestration,
aligned with [ecos-studio/ecc](https://github.com/ecos-studio/ecc) (`chipcompiler/`).

Current default flow is fully focused on front-end execution:
`prepare -> elab -> lint -> sim`.

## Repository Layout

```text
ecc-fe/
├── fecompiler/                  # Core library (mirrors chipcompiler/)
│   ├── config.py                # Global config (DEFAULT_PROJECTS_ROOT)
│   ├── allflow/                 # Flow step definitions (mirrors rtl2gds/)
│   │   └── builder.py           # DEFAULT_FLOW_STEPS, build_allflow()
│   ├── analysis/                # Step metrics analysis
│   │   └── step.py              # StepMetricsBuilder
│   ├── cli/                     # CLI entry point (mirrors chipcompiler/cli/)
│   │   └── main.py
│   ├── data/                    # Data layer
│   │   ├── step.py              # StepEnum, StateEnum, StepMetrics
│   │   └── workspace.py         # WorkspaceStep, CreateWorkspaceData, create/load_workspace
│   ├── engine/                  # Flow orchestration
│   │   └── flow.py              # EngineFlow  (writes log/log.txt)
│   ├── thirdparty/              # Placeholder for external tool submodules
│   ├── tools/
│   │   ├── fe/                  # Step workspace builder & sub-flow
│   │   │   ├── builder.py       # build_step(), build_step_space(), build_step_config()
│   │   │   ├── subflow.py       # generic subflow helpers (init/update)
│   │   │   ├── service.py       # get_step_info() — query step resources by ID
│   │   │   └── base.py          # BaseStep interface
│   │   ├── common/
│   │   │   └── rtl_inputs.py    # shared RTL/incdir/define parsing helpers
│   │   └── verilator/           # Verilator tool integration
│   │       ├── runner.py        # VerilatorLintStep, VerilatorSimStep
│   │       └── subflow.py       # LintSubFlowEnum, SimSubFlowEnum
│   └── utility/                 # Shared utilities (json, log, file, filelist)
├── docs/
│   └── examples/                # Example RTL (adder.v, mux.v, filelist.f)
├── test/                        # pytest tests
├── workspace_projects/          # Default project output directory (git-ignored)
└── BUILD.bazel
```

## Flow Steps

```
prepare (fe)        ← merge CPU/SoC/filelist inputs
elab   (slang)      ← slang --lint-only      →  report/log.txt
lint   (verilator)  ← verilator --lint-only  →  report/log.txt
sim    (verilator)  ← compile + simulate     →  report/log.txt / report/cases/*
```

## How It Works

```
cli/main.py
  └── data/workspace.py     create_workspace()
        ├── parse filelist.f → copy .v files to origin/
        ├── write home/flow.json  (all steps Unstart)
        └── write origin/filelist.f  (absolute paths)
  └── engine/flow.py        EngineFlow
        ├── sync flow.json         → keep only DEFAULT_FLOW_STEPS
        ├── create_step_workspaces()  →  mkdir prepare_fe/ elab_slang/ lint_verilator/ sim_verilator/
        └── run_all()
              ├── [START]   prepare  → merge rtl inputs
              ├── [START]   elab     → slang checks
              ├── [START]   lint     → verilator lint
              ├── [START]   sim      → compile + run (single image or multi-image cases)
              → writes log/log.txt on every step
```

## Project Directory Structure

```text
workspace_projects/<design>/
├── prepare_fe/
│   └── report/log.txt      # prepare step logs / summary
├── elab_slang/
│   └── report/log.txt      # slang elaboration output
├── lint_verilator/
│   ├── data/               # empty by default
│   ├── report/log.txt      # verilator lint output
│   └── subflow.json        # sub-steps: lint → report
├── sim_verilator/
│   ├── output/cases/       # wave.vcd per case
│   │   └── <case>/wave.vcd
│   ├── report/log.txt      # latest simulation summary
│   ├── report/cases/       # latest per-case logs (single or multi case)
│   │   └── <case>/log.txt
│   ├── report/cases.json   # machine-readable latest case results
│   ├── report/runs/        # per-run history (not overwritten)
│   │   └── <run_id>/
│   │       ├── log.txt
│   │       ├── cases.json
│   │       └── cases/<case>/log.txt
│   ├── report/build_programs.log.txt  # build_test.sh output when building programs/*.c
│   └── subflow.json        # sub-steps: compile → simulate → report
├── home/
│   ├── flow.json           # per-step state (Unstart / Ongoing / Success)
│   ├── parameters.json     # design parameters
│   └── home.json
├── origin/
│   ├── <design>.v          # copied from filelist sources
│   ├── filelist.f          # absolute-path filelist (copied)
│   └── <design>.sdc        # auto-generated SDC
├── log/
│   └── log.txt             # step start / success / failed with timestamps
```

## Quick Start

```bash
cd /home/luyoung/ecc-fe

# Create project from filelist and run all steps
python3 -m fecompiler.cli.main \
    --design adder --top adder \
    --filelist docs/examples/filelist.f

# Custom workspace path
python3 -m fecompiler.cli.main \
    --design adder --top adder \
    --filelist docs/examples/filelist.f \
    --workspace /path/to/adder

# Re-run all steps
python3 -m fecompiler.cli.main --design adder --top adder \
    --filelist docs/examples/filelist.f --rerun
```

Projects are created under `workspace_projects/<design>/` by default
(defined in `fecompiler/config.py`).

### CPU+SoC Example (CLI)

```bash
# Full flow with one image
python3 -m fecompiler.cli.main \
    --design cl3_soc_phase2_full \
    --top ysyxSoCTop \
    --cpu-filelist docs/examples/cl3/filelist.cpu.f \
    --soc-filelist fecompiler/thirdparty/SoC/filelist.soc.f \
    --testbench fecompiler/thirdparty/SoC/driver/main.cpp \
    --sim-cpp fecompiler/thirdparty/SoC/driver/dpi_mem.cpp \
    --sim-cflag=-Ifecompiler/thirdparty/SoC \
    --sim-image fecompiler/thirdparty/SoC/tests/out/min2.soc.bin \
    --sim-arg=--max-cycles \
    --sim-arg=2000000 \
    --rerun

# Re-run only sim on existing workspace and reuse compiled sim binary
python3 -m fecompiler.cli.main \
    --design cl3_soc_phase2_full \
    --top ysyxSoCTop \
    --workspace workspace_projects/cl3_soc_phase2_full \
    --sim-only \
    --sim-reuse-binary \
    --sim-all-tests \
    --sim-arg=--max-cycles \
    --sim-arg=2000000
```

### Bazel

```bash
# Run CL3 CPU+SoC flow (single image)
bazel run //:run_cl3_soc

# Run CL3 CPU+SoC flow for all prebuilt *.soc.bin tests
bazel run //:run_cl3_soc_all_tests

# Re-run only sim on existing workspace (reuse compiled sim binary)
bazel run //:run_cl3_soc_sim_only_all_tests

# Pass custom arguments
bazel run //:cli -- --design mydesign --top mydesign_top \
    --filelist path/to/filelist.f
```

### Bazel Regression Commands (CPU+SoC)

Use these two commands in daily regression. The `--test_env=PATH="$PATH"` is
important so Bazel test sandbox can find local `slang/verilator` and RISC-V toolchain.

```bash
# 1) Full CPU+SoC end-to-end test
bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"

# 2) All tests
bazel test //:all_tests --test_output=errors --test_env=PATH="$PATH"
```

Main logs for `test_cpu_soc_flow`:

- `workspace_projects/cpu_soc_test/log/log.txt` (flow-level step status)
- `workspace_projects/cpu_soc_test/sim_verilator/log/log.txt` (compile stage log)
- `workspace_projects/cpu_soc_test/sim_verilator/report/log.txt` (latest simulation summary)
- `workspace_projects/cpu_soc_test/sim_verilator/report/cases/<case>/log.txt` (latest per-case log)
- `workspace_projects/cpu_soc_test/sim_verilator/report/runs/<run_id>/cases/<case>/log.txt` (history per run, retained)

### How `-m fecompiler.cli.main` works

Python's `-m` flag runs a module as a script. Starting from the current
directory it resolves `fecompiler/cli/main.py` and calls `main()`.
No installation needed — just run from the repo root.

## Building Third-party Tools

`fecompiler/tools/slang/bin/slang` and `fecompiler/tools/verilator/bin/*` are provided as the default tool binaries in this repository.

If you want to rebuild/update them from `fecompiler/thirdparty/*`, use:

```bash
# Slang (elaboration step, limit CPU cores with -j8)
cmake -S fecompiler/thirdparty/slang \
      -B fecompiler/thirdparty/slang/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/home/luyoung/ecc-fe/fecompiler/tools/slang
cmake --build fecompiler/thirdparty/slang/build -j8
cmake --install fecompiler/thirdparty/slang/build

# Verilator (lint + simulation step, limit CPU cores with -j8)
cmake -S fecompiler/thirdparty/verilator \
      -B fecompiler/thirdparty/verilator/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/home/luyoung/ecc-fe/fecompiler/tools/verilator
cmake --build fecompiler/thirdparty/verilator/build -j8
cmake --install fecompiler/thirdparty/verilator/build
```

## Tests

```bash
cd /home/luyoung/ecc-fe

python3 -m pytest test/           # all tests
python3 -m pytest test/ -v        # verbose
python3 -m pytest test/ -x        # stop on first failure
python3 -m pytest test/test_examples.py  # integration tests (writes to workspace_projects/test_adder/)
python3 -m pytest test/test_cpu_soc_flow.py  # CPU+SoC end-to-end API flow tests
```

### Bazel

```bash
bazel test //:all_tests
bazel test //:test_engine_flow
```

## Key Files

| Feature | File |
|---------|------|
| CLI entry point | `fecompiler/cli/main.py` |
| Global config | `fecompiler/config.py` |
| Flow step definitions | `fecompiler/allflow/builder.py` |
| Flow orchestration + logging | `fecompiler/engine/flow.py` |
| Workspace create / load | `fecompiler/data/workspace.py` |
| Step path structure | `fecompiler/tools/fe/builder.py` |
| Step resource query | `fecompiler/tools/fe/service.py` |
| Prepare step | `fecompiler/tools/prepare/runner.py` |
| Slang elab step | `fecompiler/tools/slang/runner.py` |
| Verilator lint step | `fecompiler/tools/verilator/runner.py` |
| Step state enums | `fecompiler/data/step.py` |
| Step registry | `fecompiler/tools/fe/__init__.py` |

## Documentation

- Chinese walkthrough: [`docs/README.zh-CN.md`](docs/README.zh-CN.md)
- Test-suite details by file: [`test/README.md`](test/README.md)
