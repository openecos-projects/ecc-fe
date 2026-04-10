# ecc-fe（简要说明）

`ecc-fe` 是一个流程骨架项目，用来验证前后端联通和 workspace 目录管理。
当前不是完整 EDA 工具链，step 执行是模拟产物。

## 1. 主要功能

- 新建 workspace：创建标准目录和基础配置文件
- 运行 flow：按 `step1 ~ step6` 顺序执行
- 单步运行：执行指定 step

## 2. 项目结构（概览）

```text
ecc-fe/
├── gui/                  # 前端页面
├── server/ecos_server/   # 后端服务
├── scripts/              # 运行和测试脚本
├── tests/                # 自动化测试
└── workspace_projects/   # 运行后生成的数据目录
```

## 3. 快速运行

```bash
cd /home/luyoung/ecc-fe
python3 -m server.ecos_server.main
```

或：

```bash
bazel run //:server
```

自定义端口：

```bash
bazel run //:server -- --port 18080
```

## 4. workspace 输出目录

创建 `demo_project` 后，默认会在：

`/home/luyoung/ecc-fe/workspace_projects/demo_project`

生成大致结构：

```text
demo_project/
├── log/
├── origin/
├── home/
├── step1_ecc/
├── step2_ecc/
├── step3_ecc/
├── step4_ecc/
├── step5_ecc/
└── step6_ecc/
```

## 5. 常用 API

- `POST /api/workspace/create_workspace`
- `POST /api/workspace/load_workspace`
- `POST /api/workspace/rtl2gds`
- `POST /api/workspace/run_step`
- `GET /api/workspace/health`

## 6. 关键代码位置

- 入口：`server/ecos_server/main.py`
- 路由：`server/ecos_server/ecc/routers/workspace.py`
- 服务编排：`server/ecos_server/ecc/services/ecc.py`
- 目录创建：`server/ecos_server/ecc/data/workspace.py`
- flow 执行：`server/ecos_server/ecc/engine/flow.py`
- step 目录结构：`server/ecos_server/ecc/tools/ecc/builder.py`
