# ecc-fe

`ecc-fe` is a pure Python framework for chip design flow orchestration,
aligned with [ecos-studio/ecc](https://github.com/ecos-studio/ecc) (`chipcompiler/`).

Verilator lint and simulation are integrated as real steps; remaining EDA
steps run as stubs focused on directory structure and state tracking.

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
│   │   └── flow.py              # EngineFlow  (writes log/flow.log)
│   ├── thirdparty/              # Placeholder for external tool submodules
│   ├── tools/
│   │   ├── fe/                  # Step workspace builder & sub-flow
│   │   │   ├── builder.py       # build_step(), build_step_space(), build_step_config()
│   │   │   ├── subflow.py       # EccSubFlowEnum, build_subflow(), init_subflow()
│   │   │   ├── service.py       # get_step_info() — query step resources by ID
│   │   │   └── base.py          # BaseStep interface
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
lint  (verilator)   ← verilator --lint-only  →  report/log.txt
sim   (verilator)   ← compile + simulate     →  report/log.txt  (needs testbench)
step1 (ecc)  ]
step2 (ecc)  ]      ← EDA stubs (placeholder for real tools)
...          ]
step7 (ecc)  ]
```

## How It Works

```
cli/main.py
  └── data/workspace.py     create_workspace()
        ├── parse filelist.f → copy .v files to origin/
        ├── write home/flow.json  (all steps Unstart)
        └── write origin/filelist.f  (absolute paths)
  └── engine/flow.py        EngineFlow
        ├── create_step_workspaces()  →  mkdir lint_verilator/ sim_verilator/ step{1..7}_ecc/
        └── run_all()
              ├── [START]   lint  → VerilatorLintStep.run()  → log.txt
              ├── [SUCCESS] lint  → flow.json updated
              ├── [START]   sim   → VerilatorSimStep.run()   → log.txt (skipped if no testbench)
              ├── [SUCCESS] sim
              └── step1~7   → _run_stub_step()
              → writes log/flow.log on every step
```

## Project Directory Structure

```text
workspace_projects/<design>/
├── home/
│   ├── flow.json           # per-step state (Unstart / Ongoing / Success)
│   ├── parameters.json     # design parameters
│   └── home.json
├── origin/
│   ├── <design>.v          # copied from filelist sources
│   ├── filelist.f          # absolute-path filelist (copied)
│   └── <design>.sdc        # auto-generated SDC
├── log/
│   └── flow.log            # step start / success / failed with timestamps
├── lint_verilator/
│   ├── report/log.txt      # verilator lint output
│   └── subflow.json        # sub-steps: lint → report
├── sim_verilator/
│   ├── report/log.txt      # simulation output (if testbench provided)
│   └── subflow.json        # sub-steps: compile → simulate → report
└── step{1..7}_ecc/         # EDA stub steps
    ├── config/  data/  output/  feature/  report/  log/  script/  analysis/
    └── subflow.json
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

### Bazel

```bash
# Run the built-in adder example
bazel run //:run_adder

# Pass custom arguments
bazel run //:cli -- --design mydesign --top mydesign_top \
    --filelist path/to/filelist.f
```

### How `-m fecompiler.cli.main` works

Python's `-m` flag runs a module as a script. Starting from the current
directory it resolves `fecompiler/cli/main.py` and calls `main()`.
No installation needed — just run from the repo root.

## Building Third-party Tools

Slang and Verilator binaries are **not committed** to the repo. After cloning, you must build them from source once:

```bash
# Slang (elaboration step)
cmake -S fecompiler/thirdparty/slang -B fecompiler/thirdparty/slang/build -DCMAKE_BUILD_TYPE=Release
cmake --build fecompiler/thirdparty/slang/build --parallel
cp fecompiler/thirdparty/slang/build/bin/slang fecompiler/tools/slang/bin/

# Verilator (lint + simulation step)
cmake -S fecompiler/thirdparty/verilator -B fecompiler/thirdparty/verilator/build -DCMAKE_BUILD_TYPE=Release
cmake --build fecompiler/thirdparty/verilator/build --target verilator -j$(nproc)
cp fecompiler/thirdparty/verilator/bin/verilator fecompiler/tools/verilator/bin/
```

## Tests

```bash
cd /home/luyoung/ecc-fe

python3 -m pytest test/           # all tests
python3 -m pytest test/ -v        # verbose
python3 -m pytest test/ -x        # stop on first failure
python3 -m pytest test/test_examples.py  # integration tests (writes to workspace_projects/test_adder/)
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
| Verilator lint step | `fecompiler/tools/verilator/runner.py` |
| Step state enums | `fecompiler/data/step.py` |
| Step registry | `fecompiler/tools/fe/__init__.py` |

## Documentation

- Chinese walkthrough: [`docs/README.zh-CN.md`](docs/README.zh-CN.md)
