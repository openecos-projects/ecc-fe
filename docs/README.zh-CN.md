# ecc-fe 中文说明

`ecc-fe` 是一个纯 Python 芯片设计流程编排框架，结构对齐 [ecos-studio/ecc](https://github.com/ecos-studio/ecc) 的 `chipcompiler/`。

当前默认流程为真实前端流程：`prepare -> elab -> lint -> sim`。

流程图与各步骤工具/输入/输出依赖见：[FE 流程图与依赖](./fe-flow.md)。

---

## 1. 项目结构

```text
ecc-fe/
├── fecompiler/                  # 核心库（对应 chipcompiler/）
│   ├── config.py                # 全局配置（DEFAULT_PROJECTS_ROOT）
│   ├── allflow/                 # 流程步骤定义（对应 rtl2gds/）
│   │   └── builder.py           # DEFAULT_FLOW_STEPS、build_allflow()
│   ├── analysis/                # 步骤指标分析
│   │   └── step.py              # StepMetricsBuilder
│   ├── cli/                     # CLI 入口（对应 chipcompiler/cli/）
│   │   └── main.py
│   ├── data/                    # 数据层
│   │   ├── step.py              # StepEnum、StateEnum、StepMetrics
│   │   └── workspace.py         # WorkspaceStep、CreateWorkspaceData、create/load_workspace
│   ├── engine/                  # 流程编排
│   │   └── flow.py              # EngineFlow
│   ├── thirdparty/              # 第三方工具子模块占位目录
│   ├── tools/
│   │   └── fe/                  # 步骤工作区构建器
│   │       ├── builder.py       # build_step()、build_step_space()、build_step_config()
│   │       ├── subflow.py       # EccSubFlowEnum、build_subflow()、init_subflow()
│   │       ├── base.py          # BaseStep 接口
│   │       └── copyfiles.py     # 示例步骤实现
│   └── utility/                 # 公共工具（json、log、file、filelist）
├── test/                        # pytest 测试
├── docs/                        # 文档
│   └── examples/                # 示例设计文件
├── workspace_projects/          # 项目默认输出目录（git 忽略）
└── BUILD.bazel
```

---

## 2. 运行原理

```
cli/main.py
  └── data/workspace.py     create_workspace()  →  创建 origin/ home/ log/
  └── engine/flow.py        EngineFlow
        ├── tools/fe/       build_step()        →  定义步骤所有路径（WorkspaceStep）
        │                   build_step_space()  →  在磁盘上创建所有目录
        │                   init_subflow()      →  写入 subflow.json
        └── run_all()                           →  顺序执行 prepare/elab/lint/sim
```

### 调用层次

| 层 | 模块 | 职责 |
|---|---|---|
| CLI | `cli/main.py` | 解析参数，调用 engine |
| 编排 | `engine/flow.py` | 按序驱动步骤、维护 flow.json 状态 |
| 构建 | `tools/fe/builder.py` | 定义步骤目录结构、创建目录 |
| 数据 | `data/workspace.py` | 创建/读取 workspace，读写 flow.json |

---

## 3. 快速开始

```bash
cd /home/luyoung/ecc-fe

# 创建项目并运行全部步骤（默认保存到 workspace_projects/<design>/）
python3 -m fecompiler.cli.main --design demo1 --top demo1_top

# 指定自定义路径
python3 -m fecompiler.cli.main --design demo1 --top demo1_top \
    --workspace /path/to/demo1

# 重跑所有步骤（即使已成功）
python3 -m fecompiler.cli.main --design demo1 --top demo1_top --rerun
```

> **`-m fecompiler.cli.main` 怎么找到的？**
> Python 的 `-m` 参数会从当前目录开始，按照包路径 `fecompiler/cli/main.py` 找到对应文件并执行其 `main()` 函数，无需安装，在仓库根目录下直接运行即可。

---

## 4. workspace 输出目录

创建 `demo1` 项目后，默认生成于 `workspace_projects/demo1/`：

```text
demo1/
├── home/
│   ├── flow.json           # 各步骤状态（Unstart / Ongoing / Success）
│   ├── parameters.json     # 设计参数
│   └── home.json
├── origin/
│   ├── demo1.def           # 占位 DEF 文件
│   ├── demo1.v             # 占位 Verilog 文件
│   └── demo1.sdc           # 自动生成的时序约束
├── log/
├── prepare_fe/
├── elab_slang/
├── lint_verilator/
└── sim_verilator/
```

---

## 5. 测试

```bash
cd /home/luyoung/ecc-fe

# 跑全部测试
python3 -m pytest test/

# 跑单个文件
python3 -m pytest test/test_engine_flow.py

# 跑单个测试函数
python3 -m pytest test/test_engine_flow.py::test_run_all_succeeds

# 显示详细输出
python3 -m pytest test/ -v

# 遇到第一个失败停止
python3 -m pytest test/ -x
```

### Bazel

```bash
bazel test //:all_tests
bazel test //:test_engine_flow
```

---

## 6. 关键代码位置

| 功能 | 文件 |
|---|---|
| CLI 入口 | `fecompiler/cli/main.py` |
| 全局配置 | `fecompiler/config.py` |
| 流程步骤定义 | `fecompiler/allflow/builder.py` |
| 流程编排 | `fecompiler/engine/flow.py` |
| workspace 创建/读取 | `fecompiler/data/workspace.py` |
| 步骤路径结构 | `fecompiler/tools/fe/builder.py` |
| 步骤资源查询 | `fecompiler/tools/fe/service.py` |
| 子步骤定义 | `fecompiler/tools/fe/subflow.py` |
| 步骤状态枚举 | `fecompiler/data/step.py` |
| 步骤注册表 | `fecompiler/tools/fe/__init__.py` |

---

## 7. 后端库 API 速查（完整）

本项目当前没有 FastAPI/Flask 这类 HTTP 路由；“后端 API”主要指可直接在 Python 中调用的库方法。

### 7.1 Workspace 创建与参数控制（`fecompiler.data.workspace`）

#### 核心类型

- `CreateWorkspaceData`
  - 创建项目时的输入结构体。
  - 支持同时配置：
    - CPU 选择：`cpu_filelist`
    - SoC 选择：`soc_filelist`
    - 仿真 testbench：`testbench`、`sim_cpp_sources`、`sim_cflags`、`sim_ldflags`
    - 被测文件选择：
      - 显式镜像：`sim_images`
      - 扫描目录：`sim_all_tests + sim_tests_dir`
      - 构建程序：`sim_build_all_programs + sim_programs_dir`，可选 `sim_tests_out_dir`
      - 指定程序：`sim_program_names` 或 `sim_program_sources`

#### 公开函数（5 个）

- `create_workspace(spec)`
  - 创建项目目录与 `home/parameters.json`, `home/flow.json` 等元数据。
- `load_workspace(directory)`
  - 读取已存在项目，返回 workspace dict（含 `cpu_filelist`/`soc_filelist`/仿真参数等）。
- `load_flow(flow_path)`
  - 读取 flow.json。
- `save_flow(flow_path, flow)`
  - 保存 flow.json。
- `build_parameter_overrides(...)`
  - 将运行时参数规范化为可持久化参数（路径绝对化、空值过滤）。

### 7.2 流程执行（`fecompiler.engine.flow.EngineFlow`）

#### 公开方法（12 个）

- `has_init()`
  - 判断 flow 是否已初始化。
- `init_default_steps()`
  - 用默认步骤初始化 flow（`prepare -> elab -> lint -> sim`）。
- `load()`
  - 从磁盘重载 flow 与 step 工作区结构。
- `save()`
  - 保存 flow 到磁盘。
- `clear_states()`
  - 清空步骤状态为 `Unstart`。
- `get_step(name, tool)`
  - 查找 flow.json 中某一步。
- `set_state(name, tool, state, runtime=None, peak_memory=None)`
  - 设置步骤状态。
- `is_flow_success()`
  - 判断全流程是否全部成功。
- `create_step_workspaces()`
  - 创建每个步骤目录与 config。
- `get_workspace_step(name)`
  - 获取某一步的 workspace step 信息。
- `run_step(step_name, rerun=False)`
  - 单步执行（你说的“单步执行方法”）。
- `run_all(rerun=False)`
  - 全流程执行（你说的“全流程执行方法”）。

### 7.3 被测文件/CPU/SoC 的选择规则

#### CPU 选择

- 使用 `CreateWorkspaceData.cpu_filelist`

#### SoC 选择

- 使用 `CreateWorkspaceData.soc_filelist`
- 常配套：
  - `testbench`（如 SoC driver/main.cpp）
  - `sim_cpp_sources`（如 dpi_mem.cpp）
  - `sim_cflags`（如 `-I<soc_root>`）

#### 被测文件选择（仿真镜像）

- 方式 A：显式指定镜像
  - `sim_images=[".../a.soc.bin", ".../b.soc.bin"]`
- 方式 B：扫描某目录全部镜像
  - `sim_all_tests=True`
  - `sim_tests_dir=".../tests/out"`
- 方式 C：从 `tests/programs/*.c` 先编译再仿真
  - `sim_build_all_programs=True`
  - `sim_programs_dir=".../tests/programs"`
  - 默认输出到 `sim_verilator/output/cases/<case>/`
  - 如需固定目录，可设置 `sim_tests_out_dir=".../tests/out"`
- 方式 D：只编译部分程序
  - `sim_program_names=["max", "fib"]`
  - 或 `sim_program_sources=[".../max.c", ".../fib.c"]`

### 7.4 步骤资源查询 API（`fecompiler.tools.fe.service`）

- `get_step_info(workspace, step, id)`
  - 预留接口，用于按资源 ID 查询 step 资源。
  - 当前实现为 stub（默认返回 `{}`）。

### 7.5 最小调用示例（库方式，不走 CLI）

```python
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow

spec = CreateWorkspaceData(
    directory="workspace_projects/AAA",
    parameters={"Design": "AAA", "Top module": "ysyxSoCTop"},
    cpu_filelist="/path/to/cl3_1/filelist.cpu.f",
    soc_filelist="/path/to/SoC2/filelist.soc.f",
    testbench="/path/to/SoC2/driver/main.cpp",
    sim_cpp_sources=["/path/to/SoC2/driver/dpi_mem.cpp"],
    sim_cflags=["-I/path/to/SoC2"],
    sim_all_tests=True,
    sim_tests_dir="/path/to/SoC2/tests/out",
    sim_run_args=["--max-cycles", "2000000"],
)
create_workspace(spec)

ws = load_workspace("workspace_projects/AAA")
engine = EngineFlow(workspace=ws)
engine.create_step_workspaces()

# 单步执行
engine.run_step("prepare", rerun=True)
engine.run_step("sim", rerun=True)

# 全流程执行
ok, reports = engine.run_all(rerun=True)
print(ok, reports)
```
