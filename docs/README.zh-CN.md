# ecc-fe（当前结构说明）

这份 README 目标是让你“即使不懂 Python/后端”也能看懂这个项目现在在做什么、怎么跑、数据怎么流、每个目录有什么用。

---

## 1. 这个项目到底是什么？

`ecc-fe` 目前是一个 **最小可运行的“流程骨架”项目**，它模拟了芯片流程软件里最核心的两件事：

1. 点击“新建 Workspace”时，自动创建一套标准目录和配置文件。
2. 点击“运行 rtl2gds”时，按 step1~step6 依次执行流程，更新状态并产出每步结果文件。

现在它是“流程模拟器”，不是完整 EDA 工具链。

---

## 2. 我们在做什么（项目阶段目标）

当前阶段目标：

1. 先把工程组织、接口协议、目录结构、状态机跑通。
2. 保证前端 -> 后端 -> 文件系统这条链路完整可验证。
3. 后续再逐步把“模拟执行”替换成真实工具执行。

你可以把它理解成：先把“管道和骨架”搭好，再接“真实加工设备”。

---

## 3. 先补最基础的 Python 概念（只讲本项目会用到的）

### 3.1 模块与包

- `server/ecos_server/main.py` 是服务入口模块。
- `server/ecos_server/ecc/services/ecc.py` 是业务服务模块。
- `server/ecos_server/ecc/engine/flow.py` 是流程引擎模块。
- `server/ecos_server/ecc/data/workspace.py` 是文件落盘模块。

`server/ecos_server/ecc/` 目录里按 `routers/schemas/services/data/engine/tools` 分层，每层都有 `__init__.py`。

### 3.2 函数

比如 `create_workspace(...)`：输入参数 -> 执行逻辑 -> 返回结果（字典）。

### 3.3 类

比如 `EccService`、`EngineFlow`。类用于把“状态 + 方法”放在一起，便于管理一次 workspace 会话。

### 3.4 dataclass / Enum

- `dataclass`：自动生成初始化代码，适合“结构化数据对象”。
- `Enum`：枚举常量，避免字符串写错（如命令、状态）。

本项目里：

- `CMDEnum`：命令名集合（`create_workspace`、`rtl2gds`...）
- `StateEnum`：状态集合（`Unstart`、`Ongoing`、`Success`...）

---

## 4. 代码结构总览

```text
ecc-fe/
├── BUILD.bazel                # Bazel 构建入口
├── docs/                      # 文档
├── gui/                       # 前端页面
│   ├── index.html            # 页面结构
│   └── src/
│       ├── views/ECCView.js
│       ├── composables/useWorkspace.js
│       └── api/workspace.js
├── scripts/                   # 常用脚本
│   ├── run_server.sh
│   └── run_tests.sh
├── server/                    # 后端（对齐 ecos-studio 的管理方式）
│   └── ecos_server/
│       ├── main.py            # HTTP 服务入口
│       └── ecc/
│           ├── config.py
│           ├── flow_spec.py
│           ├── routers/workspace.py
│           ├── schemas/ecc.py
│           ├── services/ecc.py
│           ├── data/workspace.py
│           ├── engine/flow.py
│           └── tools/ecc/builder.py
├── tests/
│   └── test_workspace_flow.py
└── workspace_projects/        # 运行后生成的 workspace 数据目录
```

---

## 4.1 源码定位（带行号）

下面这些是最关键的“入口行号”，建议你边看 README 边打开对应文件：

1. HTTP 服务入口与 API 路由分发：`server/ecos_server/main.py:19`
2. Workspace 路由函数（create/load/rtl2gds/run_step）：`server/ecos_server/ecc/routers/workspace.py:13`
3. 命令与状态枚举定义：`server/ecos_server/ecc/schemas/ecc.py:13`
4. 请求解析（`cmd/data` 校验）：`server/ecos_server/ecc/schemas/ecc.py:88`
5. `create_workspace` 业务编排入口：`server/ecos_server/ecc/services/ecc.py:36`
6. `rtl2gds` 全流程编排入口：`server/ecos_server/ecc/services/ecc.py:84`
7. Workspace 目录创建与 home/origin 写入：`server/ecos_server/ecc/data/workspace.py:32`
8. 旧 step 目录清理逻辑：`server/ecos_server/ecc/data/workspace.py:139`
9. Flow 默认步骤初始化：`server/ecos_server/ecc/engine/flow.py:25`
10. Step 串行执行与状态更新：`server/ecos_server/ecc/engine/flow.py:137`
11. Step 路径结构定义（config/output/data...）：`server/ecos_server/ecc/tools/ecc/builder.py:12`
12. 前端点击“新建/运行”入口：`gui/src/views/ECCView.js:20`
13. 前端参数映射到后端 `parameters`：`gui/src/composables/useWorkspace.js:3`
14. 前端 API 封装（`cmd/data` 发包）：`gui/src/api/workspace.js:19`

---

## 5. 前端到后端的完整链路（点击按钮后发生了什么）

## 5.1 点击“新建 Workspace”

执行链路：

1. `gui/src/views/ECCView.js`
2. `useWorkspace().newProject(...)`
3. `createWorkspaceApi(...)`
4. `POST /api/workspace/create_workspace`
5. `server/ecos_server/main.py` 路由到 `ecc/routers/workspace.py`
6. `EccService.create_workspace(...)`
7. `workspace.create_workspace(...)` 创建目录和 home/origin 文件
8. `EngineFlow.create_step_workspaces(...)` 创建 step1~step6 的工作目录
9. 返回响应给前端，前端展示结果 JSON

## 5.2 点击“运行 rtl2gds”

执行链路：

1. `ECCView.js` -> `runFlow(...)`
2. `rtl2gdsApi(...)`
3. `POST /api/workspace/rtl2gds`
4. `EccService.rtl2gds(...)`
5. `EngineFlow.run_all(...)` 顺序调用 `run_step(...)`
6. 每步更新状态，写日志和输出文件
7. 返回每步 report

---

## 6. API 协议（非常重要）

项目使用命令式协议：

请求：

```json
{
  "cmd": "create_workspace",
  "data": { }
}
```

响应：

```json
{
  "cmd": "create_workspace",
  "response": "success",
  "data": { },
  "message": ["..."]
}
```

`response` 可为：`success` / `failed` / `error` / `warning`

当前接口：

1. `POST /api/workspace/create_workspace`
2. `POST /api/workspace/load_workspace`
3. `POST /api/workspace/rtl2gds`
4. `POST /api/workspace/run_step`
5. `POST /api/workspace/get_home_page`
6. `GET /api/workspace/health`
7. 兼容接口：`POST /api/flow/run`（内部映射到 `rtl2gds`）

---

## 7. 工作目录如何生成（最关心部分）

默认项目根目录：

`/home/luyoung/ecc-fe/workspace_projects`

可由环境变量覆盖：

`ECC_FE_PROJECTS_ROOT=/your/path`

假设你创建项目名 `demo_project`，则目录类似：

```text
workspace_projects/demo_project/
├── log/
│   └── server.log
├── origin/
│   ├── demo_project.def
│   ├── demo_project.v
│   ├── demo_project.sdc
│   └── filelist                # 如果传入 rtl_list/filelist 才会有
├── home/
│   ├── flow.json
│   ├── parameters.json
│   ├── home.json
│   └── checklist.json
├── step1_ecc/
├── step2_ecc/
├── step3_ecc/
├── step4_ecc/
├── step5_ecc/
└── step6_ecc/
```

> 注意：`create_workspace` 会清理“旧版本遗留 step 目录”（只清理像 step 工作目录那种结构），避免你目录里同时出现旧流程和新流程。

---

## 8. 每个目录/文件的作用

## 8.1 `log/`

- `server.log`：workspace 初始化日志（后续可扩展更多服务日志）。

## 8.2 `origin/`

这个目录存“原始输入”：

1. `*.def`：版图/布局输入（如果没传就生成占位文件）。
2. `*.v`：Verilog 输入（如果没传就生成占位模块）。
3. `*.sdc`：时序约束（自动生成基础模板）。
4. `filelist`：当你传 `rtl_list` 或 `filelist` 时会生成/拷贝。

## 8.3 `home/`

### `flow.json`

流程状态主文件。每步都有：

1. `name`：step 名称（step1..step6）
2. `tool`：工具名（ecc）
3. `state`：`Unstart/Ongoing/Success/Incomplete`
4. `runtime`：运行时长 `HH:MM:SS`
5. `peak memory (mb)`：峰值内存（当前模拟固定 0）
6. `info`：扩展信息

### `parameters.json`

项目参数（Design、Top module、Clock、PDK、频率等）。

### `home.json`

主页信息指针和监控数据容器：

1. 指向 flow/parameters/checklist 文件
2. `monitor`：step、runtime、memory、instance、frequency 序列
3. `metrics`：可视化指标占位

### `checklist.json`

检查项数据文件（当前初始化为空骨架）。

## 8.4 `stepN_ecc/`

每个 step 子目录里有：

1. `config/`：配置文件（如 `flow_config.json`, `db_default_config.json`）
2. `output/`：输出结果（`.def.gz`, `.v`, `.gds`, `.json`）
3. `data/`：中间数据目录（含 `fp/pnp/pl/cts/no/to/rt/sta/drc`）
4. `feature/`：特征提取结果（`*.db.json`, `*.step.json`, `*.map.json`）
5. `report/`：报告文件（`*.rpt`）
6. `log/`：步骤日志（`stepN.log`）
7. `script/`：执行脚本（`stepN_main.tcl`）
8. `analysis/`：分析数据（`*_metrics.json`, `*_statis.csv`）
9. `subflow.json`：子流程占位文件
10. `checklist.json`：步骤级检查项占位文件

---

## 9. 数据是如何传递的（核心原理）

可以拆成 4 层：

1. **前端层**：收集用户输入，拼成 `cmd/data` JSON。
2. **协议层（schemas）**：校验和解析输入，把原始 JSON 转成结构化数据对象。
3. **服务层（service + engine）**：决定“先做什么后做什么”（编排）。
4. **数据层（data + builder）**：真正执行文件系统操作（创建目录、写文件）。

一个典型请求的数据传递：

1. 浏览器发送 `create_workspace`。
2. `schemas.parse_ecc_request` 检查 `cmd` 是否匹配。
3. `parse_create_workspace_data` 解析 `directory/pdk/parameters/...`。
4. `workspace.create_workspace` 在磁盘创建 `log/origin/home`。
5. `EccService.__build_flow` 构建 `EngineFlow`。
6. `EngineFlow.create_step_workspaces` 调 `builder` 生成 step 目录树。
7. 返回标准响应体。

---

## 10. step 之间如何传递输入输出

`EngineFlow.create_step_workspaces` 的逻辑是串联：

1. `step1` 输入使用 `origin_def + origin_verilog`
2. `step2` 输入使用 `step1` 的 `output.def + output.verilog`
3. `step3` 输入使用 `step2` 输出
4. ...一直到 `step6`

所以它是一个线性 pipeline。

---

## 11. 为什么看起来像“后端流程项目”？

你的感觉是对的。当前 `ecc-fe` 的核心价值在后端流程：

1. 定义流程步骤和状态机
2. 管理 workspace 文件结构
3. 统一接口协议
4. 为后续接入真实工具留出稳定骨架

前端目前只是最小控制面板（创建 + 运行 + 展示 JSON 结果）。

---

## 12. 现在还没做的（你后续可能会关心）

当前是“模拟执行”，未完成项包括：

1. 接入真实 EDA 工具执行（替换 `_run_single_step` 的占位写文件）。
2. 真实日志聚合、SSE 推送、进度通知。
3. 更完整的 `get_info` 资源接口。
4. 更严格的文件格式校验和错误恢复。

---

## 13. 如何运行

## 13.1 Python 直接运行

```bash
cd /home/luyoung/ecc-fe
python3 -m server.ecos_server.main
```

打开：

`http://127.0.0.1:8080`

## 13.2 Bazel 运行

```bash
cd /home/luyoung/ecc-fe
bazel run //:server
```

## 13.3 脚本运行

```bash
cd /home/luyoung/ecc-fe
./scripts/run_server.sh
```

测试脚本：

```bash
cd /home/luyoung/ecc-fe
./scripts/run_tests.sh
```

---

## 14. API 调用示例

## 14.1 创建 workspace

```bash
curl -X POST http://127.0.0.1:8080/api/workspace/create_workspace \
  -H 'Content-Type: application/json' \
  -d '{
    "cmd": "create_workspace",
    "data": {
      "directory": "/home/luyoung/ecc-fe/workspace_projects/demo_project",
      "pdk": "ics55",
      "parameters": {
        "Design": "demo_project",
        "Top module": "top",
        "Clock": "clk"
      },
      "rtl_list": []
    }
  }'
```

## 14.2 运行全流程

```bash
curl -X POST http://127.0.0.1:8080/api/workspace/rtl2gds \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"rtl2gds","data":{"rerun":false}}'
```

## 14.3 单步运行

```bash
curl -X POST http://127.0.0.1:8080/api/workspace/run_step \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"run_step","data":{"step":"step1","rerun":false}}'
```

---

## 15. 自动化测试

```bash
cd /home/luyoung/ecc-fe
python3 -m unittest tests/test_workspace_flow.py -v
bazel test //:workspace_flow_test
```

测试覆盖：

1. 创建 workspace 时目录和 `flow.json` 初始化
2. `rtl2gds` 后状态与输出文件
3. `run_step` 单步执行
4. 空 directory 时默认根目录回退

---

## 16. 建议你先读哪几个文件（学习路径）

如果你“啥都不懂”，建议按下面顺序看代码：

1. `server/ecos_server/main.py`（理解 HTTP 请求如何进来）
2. `server/ecos_server/ecc/routers/workspace.py`（理解路由分发）
3. `server/ecos_server/ecc/schemas/ecc.py`（理解请求/响应协议）
4. `server/ecos_server/ecc/services/ecc.py`（理解业务编排）
5. `server/ecos_server/ecc/data/workspace.py`（理解目录和文件如何创建）
6. `server/ecos_server/ecc/engine/flow.py`（理解 step 状态机和执行）
7. `server/ecos_server/ecc/tools/ecc/builder.py`（理解 step 子目录和路径规划）
8. `gui/src/views/ECCView.js`（理解前端怎么触发）

---

如果你愿意，下一步我可以继续把 README 增加一节：
“按一次真实请求逐行跟踪（带关键代码行号）”，把 `create_workspace` 和 `rtl2gds` 各走一遍。
