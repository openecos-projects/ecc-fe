# ecc-fe

`ecc-fe` is a pure Python framework for chip design flow orchestration,
aligned with [ecos-studio/ecc](https://github.com/ecos-studio/ecc) (`chipcompiler/`).

All EDA steps run as stubs — the focus is on directory structure, state
tracking, and flow orchestration rather than real EDA execution.

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
│   │   └── flow.py              # EngineFlow
│   ├── thirdparty/              # Placeholder for external tool submodules
│   ├── tools/
│   │   └── fe/                  # Step workspace builder & sub-flow
│   │       ├── builder.py       # build_step(), build_step_space(), build_step_config()
│   │       ├── subflow.py       # EccSubFlowEnum, build_subflow(), init_subflow()
│   │       ├── base.py          # BaseStep interface
│   │       └── copyfiles.py     # CopyFilesStep (example step implementation)
│   └── utility/                 # Shared utilities (json, log, file, filelist)
├── test/                        # Tests
├── workspace_projects/          # Default project output directory (git-ignored)
└── BUILD.bazel
```

## How It Works

```
cli/main.py
  └── data/workspace.py     create_workspace()  →  builds origin/ home/ log/
  └── engine/flow.py        EngineFlow
        └── tools/fe/       build_step()        →  defines all paths for a step
                            build_step_space()  →  creates directories on disk
                            init_subflow()      →  writes subflow.json
        └── _run_stub_step()                    →  writes placeholder output files
        └── set_state()                         →  updates flow.json
```

Each project directory looks like:

```text
workspace_projects/<design>/
├── home/
│   ├── flow.json           # per-step state (Unstart / Ongoing / Success)
│   ├── parameters.json     # design parameters
│   └── home.json
├── origin/
│   ├── <design>.def        # placeholder DEF
│   ├── <design>.v          # placeholder Verilog
│   └── <design>.sdc        # auto-generated SDC
├── log/
└── step{1..7}_fe/
    ├── config/             # flow_config.json, db/cts/drc/... configs
    ├── data/               # fp/ pl/ cts/ no/ to/ rt/ sta/ drc/
    ├── output/             # .def.gz  .v  .gds  .json  .png
    ├── feature/            # step.json  db.json  map.json
    ├── report/             # step.rpt  db.rpt  sta/
    ├── log/                # step.log
    ├── script/             # step_main.tcl
    ├── analysis/           # metrics.json  statis.csv
    ├── subflow.json        # ordered sub-step list with per-sub-step state
    └── checklist.json
```

## Quick Start

```bash
cd /home/luyoung/ecc-fe

# Create a project and run all steps
python3 -m fecompiler.cli.main --design demo1 --top demo1_top

# Specify a custom workspace path
python3 -m fecompiler.cli.main --design demo1 --top demo1_top \
    --workspace /path/to/demo1

# Re-run all steps even if already successful
python3 -m fecompiler.cli.main --design demo1 --top demo1_top --rerun
```

Projects are created under `workspace_projects/<design>/` by default
(defined in `fecompiler/config.py`).

### How `-m fecompiler.cli.main` works

Python's `-m` flag runs a module as a script. Starting from the current
directory, Python looks for a package named `fecompiler`, then follows the
dotted path `cli.main` to find `fecompiler/cli/main.py` and calls its
`main()` function. No installation needed — just run from the repo root.

## Tests

```bash
cd /home/luyoung/ecc-fe

# Run all tests
python3 -m pytest test/

# Run a single file
python3 -m pytest test/test_engine_flow.py

# Run a single test function
python3 -m pytest test/test_engine_flow.py::test_run_all_succeeds

# Verbose output
python3 -m pytest test/ -v

# Stop on first failure
python3 -m pytest test/ -x
```

### Bazel

```bash
bazel test //:all_tests
bazel test //:test_engine_flow
```

## Documentation

- Chinese walkthrough: [`docs/README.zh-CN.md`](docs/README.zh-CN.md)
