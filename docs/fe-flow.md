# FE 流程图与依赖

本文档描述 `ecc-fe` 当前默认前端流程，以及每一步依赖的工具、输入和输出。

默认步骤来自 `fecompiler/allflow/builder.py`：

```text
prepare -> elab -> lint -> sim
```

## 总览图

```mermaid
flowchart TD
    U[用户输入 / API 参数] --> WS[create_workspace]

    U --> CPU[CPU filelist.f]
    U --> SOC[SoC filelist.soc.f]
    U --> TB[C++ testbench]
    U --> SIMOPT[仿真参数 / cases 选择]

    CPU --> WS
    SOC --> WS
    TB --> WS
    SIMOPT --> WS

    WS --> HOME[home/parameters.json<br/>home/flow.json<br/>home/home.json]
    WS --> ORIGIN[origin/<br/>原始 RTL / filelist / SDC]
    WS --> ENGINE[EngineFlow]

    ENGINE --> STEPWS[create_step_workspaces<br/>生成 step 工作区]
    STEPWS --> PREP[prepare_fe]
    PREP --> ELAB[elab_slang]
    ELAB --> LINT[lint_verilator]
    LINT --> SIM[sim_verilator]

    PREP --> PREP_OUT[merged_rtl.f<br/>prepared_inputs.json]
    PREP_OUT --> ELAB
    PREP_OUT --> LINT
    PREP_OUT --> SIM

    ELAB --> ELAB_OUT[Slang elaboration report]
    LINT --> LINT_OUT[Verilator lint report]
    SIM --> SIM_OUT[sim binary<br/>cases logs<br/>wave.vcd<br/>cases.json]
```

## 依赖关系图

```mermaid
flowchart LR
    subgraph Inputs[输入]
        CPU[CPU filelist.f]
        SOC[SoC filelist.soc.f]
        RTL[单独 RTL / legacy filelist]
        TB[C++ testbench]
        CPP[额外 C++ sources<br/>dpi_mem.cpp / difftest.cpp 等]
        CASES[sim_images / sim_all_tests<br/>sim_program_names / rtthread]
        FLAGS[sim_cflags / sim_ldflags / sim_run_args]
    end

    subgraph Prepare[prepare_fe<br/>工具: fecompiler prepare parser]
        P1[collect inputs]
        P2[merge filelist]
        P3[persist state]
        P4[report]
    end

    subgraph Slang[elab_slang<br/>工具: slang]
        S1[slang --lint-only<br/>semantic/elaboration check]
    end

    subgraph Lint[lint_verilator<br/>工具: verilator]
        V1[verilator --lint-only]
    end

    subgraph Sim[sim_verilator<br/>工具: verilator + SoC build_test.sh]
        C1[verilator --binary]
        C2[build test programs]
        C3[run cases]
    end

    CPU --> P1
    SOC --> P1
    RTL --> P1
    P1 --> P2 --> P3 --> P4
    P4 --> MANIFEST[prepared_inputs.json<br/>rtl_files / incdirs / defines]

    MANIFEST --> S1
    MANIFEST --> V1
    MANIFEST --> C1

    TB --> C1
    CPP --> C1
    FLAGS --> C1
    CASES --> C2
    CASES --> C3
    FLAGS --> C3

    S1 --> SR[report/log.txt<br/>report/elab.rpt]
    V1 --> LR[report/log.txt<br/>report/lint.rpt]
    C1 --> BIN["output/&lt;design&gt;_sim<br/>log/log.txt"]
    C2 --> IMAGES["output/cases/&lt;case&gt;/&lt;case&gt;.soc.bin"]
    BIN --> C3
    IMAGES --> C3
    C3 --> RUNS["output/cases/&lt;case&gt;/log.txt<br/>output/cases/&lt;case&gt;/wave.vcd<br/>report/cases.json<br/>report/runs/&lt;run_id&gt;/"]
```

## Step 目录结构

每一步由 `fecompiler/tools/fe/builder.py` 生成统一目录：

```text
<workspace>/<step>_<tool>/
├── config/
│   └── flow_config.json
├── input/
├── output/
│   ├── <design>_<step>.def.gz
│   ├── <design>_<step>.v
│   ├── <design>_<step>.gds
│   └── <design>_<step>.json
├── report/
│   └── <step>.rpt
├── log/
│   └── log.txt
├── script/
├── analysis/
├── subflow.json
└── checklist.json
```

`sim_verilator` 会额外产生：

```text
sim_verilator/
├── output/
│   ├── <design>_sim
│   └── cases/
│       └── <case>/
│           ├── <case>.soc.bin
│           ├── log.txt
│           └── wave.vcd
├── report/
│   ├── log.txt
│   ├── build_programs.log.txt
│   ├── cases.json
│   ├── cases/<case>/log.txt
│   └── runs/<run_id>/
└── log/log.txt
```

## 各步骤明细

| Step | Tool | 主要输入 | 主要输出 | 子步骤 |
|---|---|---|---|---|
| `prepare_fe` | Python parser | `cpu_filelist`, `soc_filelist`; 或 legacy `input_filelist`; 或 `origin_verilog` | `output/merged_rtl.f`, `output/prepared_inputs.json`, `report/prepare.rpt` | `collect inputs`, `merge filelist`, `persist state`, `report` |
| `elab_slang` | `fecompiler/tools/slang/bin/slang` 或 PATH 中的 `slang` | `prepared_inputs.json` 中的 RTL / incdirs / defines, `top_module` | `report/log.txt`, `report/elab.rpt` | `elaborate`, `report` |
| `lint_verilator` | repo-local 或系统 `verilator` | `prepared_inputs.json` 中的 RTL / incdirs / defines, `top_module` | `report/log.txt`, `report/lint.rpt` | `lint`, `report` |
| `sim_verilator` | `verilator --binary`; 可选 SoC `scripts/build_test.sh`; 可选 difftest ref so | RTL, `testbench`, `sim_cpp_sources`, `sim_cflags`, `sim_ldflags`, `sim_run_args`, cases 配置 | `output/<design>_sim`, `output/cases/<case>/`, `report/cases.json`, `report/runs/<run_id>/` | `compile`, `simulate`, `report` |

## 数据如何流动

1. 用户创建 workspace 时传入 CPU、SoC、testbench、仿真参数等。
2. `home/parameters.json` 持久化这些参数，`home/flow.json` 保存全流程状态。
3. `prepare_fe` 解析 filelist：
   - 支持 `-f` / `-F` 嵌套 filelist。
   - 收集 `.v` / `.sv`。
   - 收集 `+incdir+...` 和 `+define+...`。
   - 写出标准化 `prepared_inputs.json`。
4. `elab_slang`、`lint_verilator`、`sim_verilator` 优先从 `prepared_inputs.json` 读取 RTL 输入。
5. `sim_verilator` 先编译仿真器，再按 cases 运行：
   - 显式镜像：`sim_images`。
   - 扫描镜像：`sim_all_tests + sim_tests_dir`。
   - 先编译程序再运行：`sim_build_all_programs`, `sim_program_names`, `sim_program_sources`, `sim_programs_dir`。
   - `rtthread` 被当作普通 case，输出在 `sim_verilator/output/cases/rtthread.soc/`。

## 常见 SoC + CPU 仿真链路

```mermaid
sequenceDiagram
    participant User as User/API
    participant WS as Workspace
    participant Prep as prepare_fe
    participant Slang as elab_slang
    participant Lint as lint_verilator
    participant Sim as sim_verilator
    participant SoC as SoC tests/build_test.sh

    User->>WS: CPU filelist + SoC filelist + testbench + sim options
    WS->>Prep: origin/home metadata
    Prep->>Prep: parse CPU/SoC filelists
    Prep-->>WS: merged_rtl.f + prepared_inputs.json
    WS->>Slang: prepared RTL + top_module
    Slang-->>WS: elaboration log/report
    WS->>Lint: prepared RTL + top_module
    Lint-->>WS: lint log/report
    WS->>Sim: RTL + C++ testbench + sim args
    Sim->>SoC: build selected programs/cases if requested
    SoC-->>Sim: *.soc.bin
    Sim->>Sim: run design_sim per case
    Sim-->>WS: output/cases/case/log.txt, wave.vcd, cases.json
```

## 关键代码位置

| 功能 | 文件 |
|---|---|
| 默认流程定义 | `fecompiler/allflow/builder.py` |
| Step 注册表 | `fecompiler/tools/fe/__init__.py` |
| Workspace 创建/读取 | `fecompiler/data/workspace.py` |
| Flow 编排与状态维护 | `fecompiler/engine/flow.py` |
| Step 目录结构 | `fecompiler/tools/fe/builder.py` |
| Prepare 实现 | `fecompiler/tools/prepare/runner.py` |
| Slang elaboration | `fecompiler/tools/slang/runner.py` |
| Verilator lint/sim | `fecompiler/tools/verilator/runner.py` |
| RTL 输入共享解析 | `fecompiler/tools/common/rtl_inputs.py` |
