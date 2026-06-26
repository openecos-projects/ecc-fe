# ecc-fe

`ecc-fe` is a Python framework for front-end chip design flow orchestration,
aligned with [ecos-studio/ecc](https://github.com/ecos-studio/ecc)
(`chipcompiler/`).

The default flow is:

```text
prepare -> review -> elab -> lint -> sim
```

## Repository Layout

```text
ecc-fe/
├── BUILD.bazel                 # Bazel targets: CLI, examples, tests
├── MODULE.bazel                # Bazel module config
├── docs/
│   └── *.md                    # user and developer documentation
├── examples/
│   └── cl3/                    # CL3 CPU example collateral
├── fecompiler/
│   ├── allflow/                # DEFAULT_FLOW_STEPS
│   ├── cli/                    # python -m fecompiler.cli.main
│   ├── data/                   # workspace/flow/state persistence
│   ├── engine/                 # EngineFlow orchestration
│   ├── tools/
│   │   ├── prepare/            # merge CPU/SoC/filelist inputs
│   │   ├── review/             # CPU-only RTL quality and Yosys precheck
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
prepare (fe)        normalize CPU/SoC/filelist inputs
review  (fe)        CPU-only RTL quality review and optional Yosys precheck
elab    (slang)     SystemVerilog elaboration and hierarchy/semantic check
lint    (verilator) Verilator lint diagnostics
sim     (verilator) compile simulator, build images, run simulation cases
```

### Step Responsibilities

Each step answers a different question.  The separation is intentional: a
design can pass one step and still fail a later step because later steps use
stronger tools or require more runtime context.

| Step | Main question | What it does | Key outputs | Failure meaning |
| --- | --- | --- | --- | --- |
| `prepare` | Are the selected CPU, SoC harness, wrappers, include paths, defines, and filelists normalized into one usable RTL input set? | Parses CPU filelists, SoC filelists, adapter filelists, nested `.f` files, `+incdir+` entries, and `+define+` entries.  It also records source metadata and writes the merged RTL view consumed by later steps. | `prepare_fe/output/merged_rtl.f`, `prepare_fe/output/prepared_inputs.json`, `prepare_fe/report/prepare.rpt` | The project inputs are structurally incomplete: missing RTL, missing wrapper/socket expectations, bad filelist paths, or incompatible catalog metadata. |
| `review` | Is the user CPU RTL healthy enough to inspect before running compiler-grade checks? | Reviews CPU RTL only and intentionally ignores the SoC harness.  It scans source text for RTL quality risks such as clock/reset usage, always-block style, case/default patterns, assignment style, hot signal references, and other static review hints.  If Yosys is available, it also runs a bounded CPU-only structural precheck to estimate module risk, fanin/fanout candidates, combinational depth candidates, inferred cells, and structural diagnostics. | `review_fe/report/rtl_review.json`, `review_fe/report/rtl_review_summary.md`, `review_fe/report/yosys_precheck.json`, `review_fe/report/yosys_precheck.log` | A blocking CPU RTL quality issue was found, or Yosys produced a real parse/hierarchy/structural error.  If Yosys is unavailable, review can still succeed with the source-scan portion and reports Yosys as unavailable. |
| `elab` | Can a SystemVerilog frontend understand the complete design hierarchy? | Runs Slang in lint-only/elaboration mode on the prepared RTL.  It checks syntax, package/include/define handling, module resolution, top selection, parameter/port structure, and basic semantic consistency.  It also builds a readable module inventory from the RTL inputs. | `elab_slang/report/log.txt`, `elab_slang/report/elab_summary.json`, `elab_slang/report/elab.rpt` | The RTL cannot be elaborated as a complete SystemVerilog design: syntax errors, unresolved modules, bad hierarchical references, bad packages/includes, or incompatible language constructs. |
| `lint` | Does Verilator accept the RTL for simulation-oriented lint rules? | Runs `verilator --lint-only` with the prepared files, include directories, defines, and top module.  It parses Verilator diagnostics into structured errors, warnings, rule groups, and per-file hotspots for GUI display. | `lint_verilator/report/log.txt`, `lint_verilator/report/lint_summary.json`, `lint_verilator/report/lint.rpt` | Verilator found errors or returned a non-zero status.  Typical causes include unsupported constructs, width/range problems, undriven or multidriven signals, missing pins, latch/case warnings promoted by policy, or tool invocation problems. |
| `sim` | Can the selected CPU and SoC harness build and run real software images? | Compiles the prepared RTL plus the configured C++ simulator testbench with Verilator.  It builds requested test programs when needed, runs each simulation case, captures logs, preserves per-run history, and emits VCD waveforms.  RT-Thread is treated as a special terminal-style case with required log markers. | `sim_verilator/output/<design>_sim`, `sim_verilator/output/cases/<case>/`, `sim_verilator/report/cases.json`, `sim_verilator/report/log.txt`, `sim_verilator/report/runs/<run_id>/` | The simulator failed to compile, a test image could not be built, a case returned failure, required RT-Thread markers were missing, timeout policy failed, or the runtime/testbench configuration is incomplete. |

### How To Read The Steps

- `prepare` is an input-contract step.  It does not prove RTL correctness.
- `review` is a CPU-quality step.  It is deliberately CPU-only so SoC harness
  glue does not hide user RTL issues.
- `elab` is a frontend semantic gate.  It answers whether the design can be
  understood as a SystemVerilog hierarchy.
- `lint` is a Verilator compatibility and coding-diagnostics gate.  It is not
  a replacement for `review` or `elab`.
- `sim` is the executable behavior gate.  Passing earlier steps does not
  guarantee software-visible behavior is correct.

## Frontend Catalog Adapter Contract

Open-source CPU and SoC integrations are catalog driven.  A CPU or SoC should
only be marked `sim_ready` after its wrapper/filelist/runtime manifest follows
the shared ECOS contracts:

- CPU wrapper contract: `ecos-cpu-wrapper-v1`
- CPU socket contract: `ysyx-axi-cpu-socket-v1`
- SoC simulator wrapper contract: `ecos-sim-wrapper-v1`
- SoC simulator top: `ecos_sim_top`

The high-level shape is:

```text
CPU RTL -> CPU wrapper -> ysyx-axi-cpu-socket-v1
       -> SoC wrapper/harness -> ecos_sim_top -> Verilator main.cpp / GUI
```

### User CPU Filelist Modes

`ecc-fe` supports two user-provided CPU paths:

- `custom-filelist`: the user's filelist already provides the SoC-facing
  compatibility module `ysyx_00000000`.
- `standard-cpu-filelist`: the user's filelist provides a standard CPU top
  named `ecos_user_cpu_top`; `prepare` generates the `ysyx_00000000`
  compatibility wrapper automatically.

The standard CPU top uses the same AXI-like CPU socket as the SoC harness.
It is intentionally stricter than "any CPU top": port names and handshake
semantics must match `ysyx-axi-cpu-socket-v1`.  The advantage is that users no
longer need to write a compatibility wrapper by hand.  The generated wrapper
also preserves the simulator MMIO convention used by CPU tests: UART writes to
`0x1000_0000` are printed, and writes to `0x1000_000c` terminate the run as
GOOD/BAD TRAP depending on the written value.

Before claiming a new adapter is runnable, run the static catalog check:

```bash
python3 -m fecompiler.cli.workspace catalog-check --json
```

This command does not build or simulate.  It checks that `sim_ready` catalog
entries have concrete filelists, wrapper tops, runtime manifests, C++ simulator
sources, and at least one supported test suite.

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
│   ├── prepared_inputs.json
│   └── generated_standard_cpu_wrapper.sv  # only for standard-cpu-filelist
├── review_fe/report/
│   ├── rtl_review.json
│   ├── rtl_review_summary.md
│   ├── yosys_precheck.json
│   └── yosys_precheck.log
├── elab_slang/report/log.txt
├── elab_slang/report/elab_summary.json
├── lint_verilator/report/log.txt
├── lint_verilator/report/lint_summary.json
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
    parameters={"Design": "cpu_soc_test", "Top module": "ecos_sim_top"},

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
engine.run_step("review", rerun=True)
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
  --top ecos_sim_top \
  --cpu-filelist examples/cl3/filelist.cpu.f \
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
