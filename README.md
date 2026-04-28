# ecc-fe

`ecc-fe` is a Python framework for front-end chip design flow orchestration,
aligned with [ecos-studio/ecc](https://github.com/ecos-studio/ecc)
(`chipcompiler/`).

The default flow is:

```text
prepare -> elab -> lint -> sim
```

## Repository Layout

```text
ecc-fe/
├── BUILD.bazel                 # Bazel targets: CLI, examples, tests
├── MODULE.bazel                # Bazel module config
├── docs/
│   └── examples/
│       ├── cl3/                # CL3 CPU example
│       ├── cl3_1/              # CL3 variant
│       └── cl3_2/              # CL3 variant
├── fecompiler/
│   ├── allflow/                # DEFAULT_FLOW_STEPS
│   ├── cli/                    # python -m fecompiler.cli.main
│   ├── data/                   # workspace/flow/state persistence
│   ├── engine/                 # EngineFlow orchestration
│   ├── tools/
│   │   ├── prepare/            # merge CPU/SoC/filelist inputs
│   │   ├── slang/              # slang elaboration step
│   │   ├── verilator/          # lint + simulation step
│   │   ├── common/             # shared RTL parsing helpers
│   │   └── fe/                 # step workspace/subflow helpers
│   ├── thirdparty/
│   │   ├── SoC/                # SoC variant 1, tests, driver, difftest
│   │   ├── SoC2/               # SoC variant 2
│   │   ├── SoC3/               # SoC variant 3
│   │   ├── rt-thread-am/       # RT-Thread AM submodule
│   │   ├── slang/              # slang source
│   │   └── verilator/          # verilator source
│   └── utility/                # JSON/file/log helpers
├── test/                       # unit and integration tests
└── workspace_projects/         # generated workspaces, git-ignored
```

## Flow Steps

```text
prepare (fe)        merge RTL/filelist inputs
elab   (slang)      slang --lint-only
lint   (verilator)  verilator --lint-only
sim    (verilator)  compile simulator, build/run simulation cases
```

## Workspace Output

Each project is created under `workspace_projects/<design>/` by default.

```text
workspace_projects/<design>/
├── home/
│   ├── parameters.json
│   ├── flow.json
│   └── home.json
├── origin/                         # copied or generated inputs
├── log/log.txt                     # flow-level status
├── prepare_fe/output/
│   ├── merged_rtl.f
│   └── prepared_inputs.json
├── elab_slang/report/log.txt
├── lint_verilator/report/log.txt
└── sim_verilator/
    ├── output/<design>_sim         # compiled simulator
    ├── output/cases/<case>/
    │   ├── <case>.bin              # built image, e.g. rtthread.soc.bin
    │   ├── log.txt                 # latest case log
    │   └── wave.vcd
    ├── report/cases.json           # latest machine-readable case summary
    ├── report/log.txt              # latest simulation summary
    ├── report/cases/<case>/log.txt
    └── report/runs/<run_id>/       # retained run history
```

RT-Thread is treated as a normal simulation case:

```text
<workspace>/sim_verilator/output/cases/rtthread.soc/rtthread.soc.bin
<workspace>/sim_verilator/output/cases/rtthread.soc/log.txt
```

## Python API

Most backend usage goes through workspace creation plus `EngineFlow`.

```python
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow

spec = CreateWorkspaceData(
    directory="/home/luyoung/ecc-fe/workspace_projects/cpu_soc_test",
    parameters={"Design": "cpu_soc_test", "Top module": "ysyxSoCTop"},

    # RTL inputs
    cpu_filelist="/path/to/cpu/filelist.cpu.f",
    soc_filelist="/path/to/SoC/filelist.soc.f",

    # Verilator simulator
    testbench="/path/to/SoC/driver/main.cpp",
    sim_cpp_sources=[
        "/path/to/SoC/driver/dpi_mem.cpp",
        "/path/to/SoC/driver/difftest.cpp",
    ],
    sim_cflags=["-I/path/to/SoC"],
    sim_ldflags=["-ldl"],

    # Runtime args
    sim_run_args=[
        "--max-cycles", "10000000",
        "--diff",
        "--ref", "/path/to/SoC/tools/riscv32-spike-so",
        "--diff-image-offset", "0x100",
        "--diff-reset-vector", "0x80000000",
    ],

    # Case selection
    sim_build_all_programs=True,
    sim_programs_dir="/path/to/SoC/tests/programs",
    sim_program_names=["rtthread"],  # optional extra case
    sim_tests_out_dir="",            # default: sim_verilator/output/cases/<case>/
)

ws = create_workspace(spec)
engine = EngineFlow(ws)
engine.create_step_workspaces()
ok, reports = engine.run_all(rerun=True)
```

Useful step calls:

```python
engine.run_step("prepare", rerun=True)
engine.run_step("elab", rerun=True)
engine.run_step("lint", rerun=True)
engine.run_step("sim", rerun=True)
```

Step states are `Invalid`, `Unstart`, `Success`, `Ongoing`, `Pending`, and
`Incomplete`.

## CLI

```bash
cd /home/luyoung/ecc-fe

python3 -m fecompiler.cli.main \
  --design cl3_soc \
  --top ysyxSoCTop \
  --cpu-filelist docs/examples/cl3/filelist.cpu.f \
  --soc-filelist fecompiler/thirdparty/SoC/filelist.soc.f \
  --testbench fecompiler/thirdparty/SoC/driver/main.cpp \
  --sim-cpp fecompiler/thirdparty/SoC/driver/dpi_mem.cpp \
  --sim-cpp fecompiler/thirdparty/SoC/driver/difftest.cpp \
  --sim-cflag=-Ifecompiler/thirdparty/SoC \
  --sim-ldflag=-ldl \
  --sim-program rtthread \
  --sim-arg=--max-cycles \
  --sim-arg=10000000 \
  --rerun
```

## Bazel Commands

```bash
# CL3 CPU + SoC examples
bazel run //:run_cl3_soc
bazel run //:run_cl3_soc_all_tests
bazel run //:run_cl3_soc_rtthread

# Main regressions
bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"
bazel test //:test_cpu_soc_matrix_flow --test_output=errors --test_env=PATH="$PATH"
bazel test //:test_cpu_soc_rtthread_flow --test_output=streamed --test_env=PATH="$PATH"
bazel test //:all_tests --test_output=errors --test_env=PATH="$PATH"
```

For RT-Thread, make sure `scons`, a RISC-V GCC toolchain, and `AM_HOME`
or `/home/luyoung/ysyx-workbench/abstract-machine` are available.

## Tests

```bash
python3 -m pytest test/test_utility.py test/test_data_step.py test/test_allflow_builder.py -q
python3 -m pytest test/test_data_workspace.py test/test_engine_flow.py -q
python3 -m pytest test/test_examples.py -q
```

Detailed test notes live in [`test/README.md`](test/README.md).

## Third-party Tools

Repo-local binaries are expected under:

```text
fecompiler/tools/slang/bin/slang
fecompiler/tools/verilator/bin/verilator
```

Build instructions for third-party tools and RT-Thread BSP notes live in
[`fecompiler/thirdparty/README`](fecompiler/thirdparty/README).

## Documentation

- Chinese walkthrough: [`docs/README.zh-CN.md`](docs/README.zh-CN.md)
- Test-suite details: [`test/README.md`](test/README.md)
