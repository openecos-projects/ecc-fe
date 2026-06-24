# test/README.md

本文档说明 `test/` 目录下每个测试文件在测什么、怎么测、使用了哪些 API。

## 测试运行方式

在仓库根目录执行：

```bash
cd /home/luyoung/ecc-fe

# 如果 scons 不在系统 PATH，但已有临时 venv，可先打开：
export PATH=/tmp/ecc-fe-scons-venv/bin:$PATH

# 全量 pytest（包含 test_examples.py）
python3 -m pytest test -q

# 单文件
python3 -m pytest test/test_engine_flow.py -q

# 单条用例
python3 -m pytest test/test_engine_flow.py::test_sim_runs_multiple_images_with_separate_logs -q
```

## Bazel 与 pytest 的范围差异

`BUILD.bazel` 里当前 `all_tests` 包含：

- `test_utility.py`
- `test_data_step.py`
- `test_allflow_builder.py`
- `test_data_workspace.py`
- `test_engine_flow.py`
- `test_cpu_soc_flow.py`
- `test_cpu_soc_matrix_flow.py`
- `test_cpu_soc_rtthread_flow.py`

`test_examples.py` 是集成测试，当前不在 `//:all_tests` 里，需通过 `pytest` 运行。

CPU+SoC 全流程回归推荐用 Bazel（避免遗漏依赖）：

```bash
# 单独跑 CPU+SoC 全流程
bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"

# 跑 3x3 CPU+SoC matrix
bazel test //:test_cpu_soc_matrix_flow --test_output=errors --test_env=PATH="$PATH"

# 启动 RT-Thread smoke test
bazel test //:test_cpu_soc_rtthread_flow --test_output=streamed --test_env=PATH="$PATH"

# 直接启动 RT-Thread flow
bazel run //:run_cl3_soc_rtthread

# 跑全部 Bazel 测试
bazel test //:all_tests --test_output=errors --test_env=PATH="$PATH"
```

`test_cpu_soc_flow` 主要日志目录：

- `workspace_projects/cpu_soc_test/log/log.txt`
- `workspace_projects/cpu_soc_test/sim_verilator/log/log.txt`
- `workspace_projects/cpu_soc_test/sim_verilator/report/log.txt`
- `workspace_projects/cpu_soc_test/sim_verilator/output/cases/<case>/log.txt`
- `workspace_projects/cpu_soc_test/sim_verilator/report/cases/<case>/log.txt`
- `workspace_projects/cpu_soc_test/sim_verilator/report/runs/<run_id>/cases/<case>/log.txt`（历史保留，不覆盖）

`test_cpu_soc_matrix_flow` 会创建 3 个 workspace：

- `workspace_projects/cpu_soc_matrix_cpu1_soc1`
- ...
- `workspace_projects/cpu_soc_matrix_cpu3_soc1`

每个组合的 case 日志在：

- `workspace_projects/cpu_soc_matrix_cpuX_socY/sim_verilator/output/cases/<case>/log.txt`

其中 `cpu_soc_matrix_cpu1_soc1` 还会把 `rtthread` 作为一个额外 case 一起跑，镜像和日志都在：

- `workspace_projects/cpu_soc_matrix_cpu1_soc1/sim_verilator/output/cases/rtthread.soc/`

`test_cpu_soc_rtthread_flow` 需要 `scons`、RISC-V GCC toolchain、`AM_HOME`（或默认
`/home/luyoung/ysyx-workbench/abstract-machine`），主要日志在：

- `workspace_projects/cpu_soc_rtthread_test/sim_verilator/output/cases/rtthread.soc/log.txt`

---

## 1) test/test_utility.py

### 测什么

`fecompiler.utility` 中 JSON 工具函数的健壮性：

- `json_read`：正常文件、缺失文件、坏 JSON、`str/Path` 路径、Unicode。
- `json_write`：写文件、自动创建父目录、覆盖写、缩进输出、`str/Path` 路径、Unicode。

### 怎么测

使用 `tmp_path` 创建临时文件，直接写入/读取 JSON 内容，校验返回值与文件内容是否符合预期。

### 用到的 API

- `fecompiler.utility.json_read`
- `fecompiler.utility.json_write`
- `pathlib.Path`
- `json.loads/json.dumps`

---

## 2) test/test_data_step.py

### 测什么

`fecompiler.data.step.StateEnum` 的定义契约：

- 成员集合完整性（`Invalid/Unstart/Success/Ongoing/Pending/Incomplete`）。
- `value == name` 约定。
- `StateEnum` 是 `str` 子类，可与字符串直接比较。
- 按值查找（如 `StateEnum("Ongoing")`）可用。

### 怎么测

直接对 Enum 做成员遍历、集合比对、类型判断、值查找与字符串比较。

### 用到的 API

- `fecompiler.data.step.StateEnum`

---

## 3) test/test_allflow_builder.py

### 测什么

`fecompiler.allflow.builder` 的静态流程定义和构造行为：

- `DEFAULT_FLOW_STEPS` 非空、首步为 `("prepare", "fe")`、每项为 `(name, tool)` 二元组。
- 工具分配规则正确：默认流程固定为 `prepare -> fe`、`elab -> slang`、`lint/sim -> verilator`。
- `sanitize_step_token` 的规范化规则（空格、特殊字符、空串等）。
- `build_allflow()` 返回形状与默认步骤一致，初始状态全部 `Unstart`。

### 怎么测

纯函数级断言，不依赖文件系统或外部工具。

### 用到的 API

- `fecompiler.allflow.builder.DEFAULT_FLOW_STEPS`
- `fecompiler.allflow.builder.sanitize_step_token`
- `fecompiler.allflow.builder.build_allflow`

---

## 4) test/test_data_workspace.py

### 测什么

`fecompiler.data.workspace` 的 workspace 持久化与加载行为：

- `create_workspace` 是否创建 `origin/log/home` 目录。
- 是否写出 `home/parameters.json`, `home/flow.json`, `home/home.json`。
- `flow.json` 是否包含全部默认步骤。
- `load_workspace` 能否恢复设计名、顶层名与输入信息。
- `load_flow/save_flow` 读写一致。
- 默认 SDC 文件生成。
- `origin_verilog` 拷贝行为。
- 默认设计名回退（由目录名推导）。
- 仿真参数持久化：
  - `testbench`
  - `sim_cpp_sources`
  - `sim_cflags`
  - `sim_ldflags`
  - `sim_run_args`
  - `sim_images`
- 参数归一化函数：
  - `build_parameter_overrides` 会统一处理路径绝对化和空值过滤。

### 怎么测

用 `tmp_path` 构建临时 workspace，调用 API 后直接检查磁盘结构与 JSON 字段。

### 用到的 API

- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.data.workspace.load_flow`
- `fecompiler.data.workspace.save_flow`
- `fecompiler.allflow.builder.DEFAULT_FLOW_STEPS`

---

## 5) test/test_engine_flow.py

### 测什么

`EngineFlow` 的核心执行语义、状态迁移，以及 `prepare/sim` 的关键逻辑。

覆盖点如下：

- `_format_runtime` 的时间格式化。
- `has_init/init_default_steps/get_step/set_state/clear_states/is_flow_success`。
- `create_step_workspaces` 的返回结构与目录落盘。
- `run_step/run_all` 的成功路径、跳过已成功步骤、`rerun=True` 行为。
- `load()` 从磁盘恢复 flow 状态。
- `flow.json` 同步行为：
  - 仅保留 `DEFAULT_FLOW_STEPS`，历史/非默认步骤会在同步时移除。
- 失败路径：
  - 坏 RTL 导致 `sim` 编译失败，步骤状态应为 `Incomplete`。
- 新建步骤目录行为：
  - `lint_verilator/data` 目录存在且默认为空目录。
- `prepare` 真实输入合并能力：
  - CPU + SoC 双 filelist 合并。
  - 嵌套 `-f` filelist、`+incdir+`、`+define+` 去重和传递。
- `sim` 参数透传：
  - `sim_cpp_sources / sim_cflags / sim_ldflags / sim_run_args`。
- 相对 include 修复：
  - `-Ifecompiler/thirdparty/SoC` 在 `BUILD_WORKSPACE_DIRECTORY` 下应解析为绝对路径。
- 多镜像仿真：
  - `sim_images` 会触发多次运行，并写 `report/cases/<case>/log.txt` 和 `cases.json`。
- 单镜像仿真：
  - 即便只有一个运行 case，也会写入统一的 `report/cases/<case>/log.txt` 结构。
- 复用已编译仿真器：
  - `sim_reuse_binary=True` 时跳过 compile 子步骤。

### 怎么测

- 使用 `_build_engine()` 先创建最小可运行 workspace。
- 大量使用 `monkeypatch` 替换 `subprocess.run`，把测试聚焦在命令拼装与状态判断，而不是依赖真实 Verilator 执行。
- 通过检查 `subflow.json/report` 和命令调用记录验证行为。

### 用到的 API

- `fecompiler.engine.flow.EngineFlow`
- `fecompiler.engine.flow._format_runtime`
- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.data.step.StateEnum`
- `pytest.monkeypatch`（间接使用）

---

## 6) test/test_examples.py

### 测什么

基于 `docs/examples/filelist.f` 的端到端集成行为：

- `filelist` 源文件拷贝到 `origin/`。
- `origin/filelist.f` 内容已转绝对路径且文件存在。
- `prepare/elab/lint/sim` 步骤状态成功。
- 关键产物存在：
  - `prepare_fe/output/merged_rtl.f`
  - `elab_slang/report/log.txt`
  - `lint_verilator/report/log.txt`
- `elab` 子流程 `subflow.json` 全成功。
- 最终 `home/flow.json` 所有步骤为 `Success`。

### 怎么测

- 使用模块级 fixture `adder_workspace`：
  - 清理 `workspace_projects/test_adder`
  - `create_workspace(...)`
  - `EngineFlow.create_step_workspaces()`
  - `EngineFlow.run_all(rerun=True)`
- 后续断言复用同一 workspace，降低重复构建开销。

### 用到的 API

- `fecompiler.config.DEFAULT_PROJECTS_ROOT`
- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.engine.flow.EngineFlow`
- `fecompiler.data.step.StateEnum`

### 依赖说明

该文件属于集成测试，依赖本地可执行工具与环境（如 `slang/verilator`）可用；相比纯单元测试更接近真实运行链路。

---

## 7) test/test_cpu_soc_flow.py

### 测什么

单 workspace (`cpu_soc_test`) 的 CPU+SoC 全流程回归：

- `prepare/elab/lint/sim` 逐步成功。
- 使用 CL3 CPU + `fecompiler/thirdparty/SoC`。
- `sim` 支持在同一个项目里先编译 `SoC/tests/programs/*.c`，再批量执行生成的 `.soc.bin`。
- 默认打开 difftest，使用 `SoC/tools/riscv32-spike-so` 作为参考模型。
- 每个 case 的最新日志落到 `sim_verilator/output/cases/<case>/log.txt`。
- 每次运行都保留历史日志到 `sim_verilator/report/runs/<run_id>/cases/<case>/log.txt`（不覆盖旧日志）。

### 怎么测

- `setUpClass` 里创建 `workspace_projects/cpu_soc_test`。
- 分别验证 workspace 创建、`prepare`、`elab`、`lint`。
- sim 阶段用后端 API 参数：
  - `sim_cpp_sources=[dpi_mem.cpp, difftest.cpp]`
  - `sim_cflags=["-I.../SoC"]`
  - `sim_ldflags=["-ldl"]`
  - `sim_run_args=[--max-cycles, 50000000, --diff, --ref, ...]`
- `test_cpu_soc_sim_each_program_success` 构建并运行 `SoC/tests/programs/*.c` 对应的所有 case。
- `test_cpu_soc_sim_batch_has_separate_logs_for_each_program` 单独跑一个 case 两次，校验历史 run log 保留。

### 用到的 API

- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.engine.flow.EngineFlow`
- `fecompiler.data.step.StateEnum`

---

## 8) test/test_cpu_soc_matrix_flow.py

### 测什么

CPU 变体 + 单一真实 SoC 组合回归：

- CPU 变体：
  - `docs/examples/cl3`
  - `docs/examples/cl3_1`
  - `docs/examples/cl3_2`
- SoC 变体：
  - `fecompiler/thirdparty/SoC`
- 动态生成 3 条测试：`test_full_flow_cpu1_soc1` 到 `test_full_flow_cpu3_soc1`。
- 每个组合都跑完整 `prepare -> elab -> lint -> sim`。
- 每个 SoC 的 `tests/programs/*.c` 都应被编译成 `.soc.bin` 并执行。
- `cpu1_soc1` 组合额外把 `rtthread` 作为 `rtthread.soc` case 执行，用于覆盖 tests + RT-Thread 混合 case。
- 每个组合默认打开 difftest，参考模型来自对应 SoC 的 `tools/riscv32-spike-so`。

### 怎么测

- `setUpClass` 先检查 `slang/verilator`、RISC-V GCC toolchain、3 组 CPU 和 3 组 SoC 必要文件。
- 每个组合创建独立 workspace：
  - `workspace_projects/cpu_soc_matrix_cpu<cpu_idx>_soc<soc_idx>`
- 每个 workspace 使用对应 CPU filelist、SoC filelist、testbench、`dpi_mem.cpp`、`difftest.cpp`。
- `engine.run_all(rerun=True)` 跑完整流程。
- 校验：
  - 非 `sim` 步骤必须成功。
  - `sim` 步骤必须成功。
  - `prepare_fe/output/merged_rtl.f` 存在。
  - `elab_slang/report/log.txt` 存在。
  - `lint_verilator/report/log.txt` 不含 `%Error`。
  - `sim_verilator/report/cases.json` 记录了所有 expected cases。
  - `sim_verilator/output/cases/<case>/log.txt` 存在且不含 `FAILED` / `%Error`。
  - 每个构建出来的 `.soc.bin` 位于对应的 `sim_verilator/output/cases/<case>/` 目录下。
  - `cpu1_soc1` 的 `rtthread.soc` 日志包含 RT-Thread banner、`Hello RISC-V!` 和 `msh />help`。

### 用到的 API

- `fecompiler.config.DEFAULT_PROJECTS_ROOT`
- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.engine.flow.EngineFlow`
- `unittest`

### 依赖说明

该测试运行时间较长，Bazel target 使用 `timeout = "long"`。其中 `cpu1_soc1` 还需要 `scons` 和
`AM_HOME`（或默认 `/home/luyoung/ysyx-workbench/abstract-machine`），因为它会额外跑 RT-Thread。
如果只想调一个组合，可以用 unittest 生成后的方法名：

```bash
python3 -m pytest test/test_cpu_soc_matrix_flow.py::TestCpuSocMatrixFlow::test_full_flow_cpu1_soc1 -q
```

---

## 9) test/test_cpu_soc_rtthread_flow.py

### 测什么

CPU+SoC 启动 RT-Thread 的 smoke test：

- 使用 CL3 CPU + `fecompiler/thirdparty/SoC`。
- 用户选择仿真程序名为 `rtthread` 时，后端会调用 SoC 的 `build_test.sh` 编译 `fecompiler/thirdparty/rt-thread-am/bsp/abstract-machine`。
- 默认打开 difftest，并使用 `SoC/tools/riscv32-spike-so`。
- 检查 RT-Thread 镜像 `rtthread.soc.bin` 被生成。
- 检查仿真日志里能看到：
  - `[soc-sim][difftest] enabled`
  - `[soc-sim][difftest] compare starts at pc=0x80000000`
  - `Thread Operating System`
  - `Hello RISC-V!`
  - `msh />help`
  - `RT-Thread shell commands:`
  - `[soc-sim] timeout after`
- 日志中不能出现 `FAILED` 或 `%Error`。

### 怎么测

- `setUpClass` 检查：
  - `slang/verilator`
  - RISC-V GCC toolchain
  - `scons`
  - `AM_HOME` 或默认 `/home/luyoung/ysyx-workbench/abstract-machine`
  - RT-Thread BSP `Makefile`
- 创建 `workspace_projects/cpu_soc_rtthread_test`。
- workspace 参数中设置：
  - `sim_program_names=["rtthread"]`
  - `sim_run_args=["--max-cycles", "10000000", "--wave", "/dev/null"]`
- RT-Thread 镜像位于：
  - `workspace_projects/cpu_soc_rtthread_test/sim_verilator/output/cases/rtthread.soc/rtthread.soc.bin`
- 只跑 `prepare` 和 `sim`，因为 smoke test 关注 RT-Thread 镜像构建、仿真、shell 输出和 difftest。

### 用到的 API

- `fecompiler.config.DEFAULT_PROJECTS_ROOT`
- `fecompiler.data.step.StateEnum`
- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.engine.flow.EngineFlow`
- `unittest`

### 依赖说明

该测试也使用 `timeout = "long"`。仿真会让 RT-Thread 运行到 `--max-cycles`，并依赖 `--timeout-ok` 将正常超时视为成功。推荐用 streamed 输出观察 shell：

```bash
bazel test //:test_cpu_soc_rtthread_flow --test_output=streamed --test_env=PATH="$PATH"
```

---

## 建议的日常使用方式

- 开发阶段先跑：
  - `python3 -m pytest test/test_utility.py test/test_data_step.py test/test_allflow_builder.py -q`
- 改动 workspace/flow 逻辑后跑：
  - `python3 -m pytest test/test_data_workspace.py test/test_engine_flow.py -q`
- 改动 CPU+SoC 普通仿真后跑：
  - `bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"`
- 改动 SoC/CPU 兼容性或 difftest 后跑：
  - `bazel test //:test_cpu_soc_matrix_flow --test_output=errors --test_env=PATH="$PATH"`
- 改动 RT-Thread、SoC UART 或 difftest MMIO 逻辑后跑：
  - `bazel test //:test_cpu_soc_rtthread_flow --test_output=streamed --test_env=PATH="$PATH"`
- 发布前跑一次：
  - `bazel test //:all_tests --test_output=errors --test_env=PATH="$PATH"`
  - `python3 -m pytest test/test_examples.py -q`
