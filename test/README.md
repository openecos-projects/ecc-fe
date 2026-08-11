# test/README.md

本文档说明 `test/` 目录下每个测试文件在测什么、怎么测、使用了哪些 API。

## 测试运行方式

在仓库根目录执行：

```bash
cd /home/luyoung/ecc-fe

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

`test_examples.py` 是轻量 example collateral 检查，当前不在 `//:all_tests` 里，需通过 `pytest` 运行。

CPU+SoC 全流程回归推荐用 Bazel（避免遗漏依赖）：

```bash
# 单独跑 CPU+SoC 全流程
bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"

# 跑 example ysyx_00000000 CPU + SoC matrix
bazel test //:test_cpu_soc_matrix_flow --test_output=errors --test_env=PATH="$PATH"

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

`test_cpu_soc_matrix_flow` 会创建 1 个 workspace：

- `workspace_projects/cpu_soc_matrix_cpu1_soc1`

每个组合的 case 日志在：

- `workspace_projects/cpu_soc_matrix_cpuX_socY/sim_verilator/output/cases/<case>/log.txt`

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

基于 `examples/ysyx_00000000` 的示例文件完整性检查：

- `filelist.cpu.f` 和 `README.md` 存在。
- filelist 声明 `ECOS_DIFFTEST` 并精确列出 8 个 RTL 文件，且路径都能解析。
- 原生顶层模块 `ysyx_00000000` 只在顶层 RTL 中定义一次。
- `ysyx_00000000` 是仓库内唯一随包发布的 example 目录。
- RTL 包含 ECC-FE `difftest_step` DPI 适配器。
- 该测试不运行 `prepare/elab/lint/sim`，只保证仓库内示例 collateral 没有断链。

### 怎么测

- 直接读取 `examples/ysyx_00000000/filelist.cpu.f`。
- 检查 filelist 顺序、相对路径、元数据文件、顶层模块定义和 example 目录集合。
- 扫描 RTL，确认存在 `difftest_step` DPI 导入。

### 用到的 API

- `pathlib.Path`

### 依赖说明

该文件属于轻量文件完整性测试，不依赖 `slang/verilator` 或 RISC-V toolchain。

---

## 7) test/test_cpu_soc_flow.py

### 测什么

单 workspace (`cpu_soc_test`) 的 CPU+SoC 全流程回归：

- `prepare/elab/lint/sim` 逐步成功。
- 使用固定版本的 `ysyx_00000000` CPU + `fecompiler/thirdparty/SoC`。
- CPU 顶层是 `ysyx_00000000`，程序按 `rv32i_zicsr` / `ilp32` 编译。
- `sim` 只构建并运行 `SoC/tests/programs/add.c` 这一条 smoke case。
- 加载 Spike 参考模型，从 payload 入口开始逐条执行 difftest。
- 每个 case 的最新日志落到 `sim_verilator/output/cases/<case>/log.txt`。
- 每次运行都保留历史日志到 `sim_verilator/report/runs/<run_id>/cases/<case>/log.txt`（不覆盖旧日志）。

### 怎么测

- `setUpClass` 里创建 `workspace_projects/cpu_soc_test`。
- 分别验证 workspace 创建、`prepare`、`elab`、`lint`。
- sim 阶段用后端 API 参数：
  - `required_cpu_top_module=ysyx_00000000`
  - `sim_cpp_sources=[dpi_mem.cpp, difftest_stub.cpp]`（runner 根据能力声明替换为 `difftest.cpp`）
  - `sim_ldflags=[-ldl]`
  - `sim_cflags=["-I.../SoC"]`
  - `sim_compile_march=rv32i_zicsr`
  - `sim_compile_mabi=ilp32`
  - `sim_program_names=[add]`
  - `sim_run_args` 包含 `--diff`、参考模型、image offset 和 reset vector。
- `test_cpu_soc_sim_add_success` 构建并运行 `add.soc`。
- `test_cpu_soc_sim_run_history_retains_add_logs` 连续跑同一 case，校验历史 run log 保留。

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
  - `examples/ysyx_00000000`
- SoC 变体：
  - `fecompiler/thirdparty/SoC`
- 动态生成 1 条测试：`test_full_flow_cpu1_soc1`。
- 每个组合都跑完整 `prepare -> elab -> lint -> sim`。
- 每个组合只编译并执行 `tests/programs/add.c`，形成 `add.soc` smoke case。
- 每个组合都使用 `ysyx_00000000` 原生顶层、`rv32i_zicsr` / `ilp32`，并启用 difftest。

### 怎么测

- `setUpClass` 先检查 `slang/verilator`、RISC-V GCC toolchain、`ysyx_00000000` example 和真实 SoC 必要文件。
- 每个组合创建独立 workspace：
  - `workspace_projects/cpu_soc_matrix_cpu<cpu_idx>_soc<soc_idx>`
- 每个 workspace 使用对应 CPU filelist、SoC filelist、testbench、`dpi_mem.cpp`、真实 `difftest.cpp` 和 Spike reference。
- `engine.run_all(rerun=True)` 跑完整流程。
- 校验：
  - 非 `sim` 步骤必须成功。
  - `sim` 步骤必须成功。
  - `prepare_fe/output/merged_rtl.f` 存在。
  - `elab_slang/report/log.txt` 存在。
  - `lint_verilator/report/log.txt` 不含 `%Error`。
  - `sim_verilator/report/cases.json` 只记录 `add.soc`。
  - `sim_verilator/output/cases/<case>/log.txt` 存在且不含 `FAILED` / `%Error`。
  - 每个构建出来的 `.soc.bin` 位于对应的 `sim_verilator/output/cases/<case>/` 目录下。

### 用到的 API

- `fecompiler.config.DEFAULT_PROJECTS_ROOT`
- `fecompiler.data.workspace.CreateWorkspaceData`
- `fecompiler.data.workspace.create_workspace`
- `fecompiler.data.workspace.load_workspace`
- `fecompiler.engine.flow.EngineFlow`
- `unittest`

### 依赖说明

该测试运行时间较长，Bazel target 使用 `timeout = "long"`。如果只想调一个组合，
可以用 unittest 生成后的方法名：

```bash
python3 -m pytest test/test_cpu_soc_matrix_flow.py::TestCpuSocMatrixFlow::test_full_flow_cpu1_soc1 -q
```

---

## 建议的日常使用方式

- 开发阶段先跑：
  - `python3 -m pytest test/test_utility.py test/test_data_step.py test/test_allflow_builder.py -q`
- 改动 workspace/flow 逻辑后跑：
  - `python3 -m pytest test/test_data_workspace.py test/test_engine_flow.py -q`
- 改动 CPU+SoC 普通仿真后跑：
  - `bazel test //:test_cpu_soc_flow --test_output=errors --test_env=PATH="$PATH"`
- 改动 SoC/CPU 兼容性后跑：
  - `bazel test //:test_cpu_soc_matrix_flow --test_output=errors --test_env=PATH="$PATH"`
- 发布前跑一次：
  - `bazel test //:all_tests --test_output=errors --test_env=PATH="$PATH"`
  - `python3 -m pytest test/test_examples.py -q`
