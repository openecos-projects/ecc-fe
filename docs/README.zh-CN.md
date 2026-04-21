# ecc-fe 中文说明

`ecc-fe` 是一个纯 Python 芯片设计流程编排框架，结构对齐 [ecos-studio/ecc](https://github.com/ecos-studio/ecc) 的 `chipcompiler/`。

所有 EDA 步骤以 stub 方式运行——框架关注的是目录结构、状态跟踪和流程编排，而非真实 EDA 执行。

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
        └── _run_stub_step()                   →  写占位输出文件，标记步骤成功
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
└── step{1..7}_fe/
    ├── config/             # 各类 JSON 配置文件
    ├── data/               # fp/ pl/ cts/ no/ to/ rt/ sta/ drc/
    ├── output/             # .def.gz  .v  .gds  .json  .png
    ├── feature/            # step.json  db.json  map.json
    ├── report/             # step.rpt  db.rpt  sta/
    ├── log/                # log.txt
    ├── script/             # step_main.tcl
    ├── analysis/           # metrics.json  statis.csv
    ├── subflow.json        # 子步骤列表及各子步骤状态
    └── checklist.json
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
