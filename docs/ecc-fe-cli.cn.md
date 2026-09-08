# ECC-FE CLI 使用指南

ECC-FE CLI 可以在不启动 ECOS Studio 桌面端的情况下创建前端工程、检查环境、
运行流程并管理工具资源。命令风格与后端 `ecc` 对齐。

## 安装

发布版安装只要求系统已有 `python3`。无需 clone 仓库：

```bash
curl -fsSL \
  https://raw.githubusercontent.com/openecos-projects/ecc-fe/main/docs/ecc-fe-cli-setup.sh \
  -o /tmp/ecc-fe-cli-setup.sh
sh /tmp/ecc-fe-cli-setup.sh
```

脚本从公共 registry 获取与当前平台匹配的发布资产，校验大小与 SHA256，并安装
ECC-FE 运行时和必需资源。下载后单独执行脚本，便于安装前审阅内容。

在仓库内安装开发版本：

```bash
uv tool install .
ecc-fe resource install --required
ecc-fe --version
```

已经 clone 仓库时，也可以直接运行同一脚本：

```bash
bash docs/ecc-fe-cli-setup.sh
```

只安装 CLI 本体可使用：

```bash
bash docs/ecc-fe-cli-setup.sh --runtime-only
```

只检查现有安装：

```bash
bash docs/ecc-fe-cli-setup.sh --check-only
```

默认命令链接位于 `~/.local/bin`。如该目录不在 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 最短工作流

创建一个使用内置 PicoRV32 的工程：

```bash
ecc-fe init frontend-demo
cd frontend-demo
ecc-fe check
ecc-fe param list
ecc-fe run
ecc-fe status
ecc-fe report qor
```

每次工程运行都位于独立的 `runs/<run-id>` 目录。创建另一个实验运行：

```bash
ecc-fe run --run-id faster-sim --set sim.compile_preset=speed
ecc-fe status --run-id faster-sim
ecc-fe log sim --run-id faster-sim
ecc-fe config sim --resolved --run-id faster-sim
```

已存在的 run 默认不会被覆盖；确认替换时显式使用 `--overwrite`。覆盖前 CLI 会
验证目标确实是 ECC-FE run，并拒绝项目根目录、`runs/` 容器、内部模板及符号链接
重定向的路径。

ECOS Studio 创建的旧工作区和 M3 工作区仍可直接使用。恢复、从指定步骤重跑、
只运行一个步骤并查看日志：

```bash
ecc-fe run --workspace ./legacy-workspace --resume
ecc-fe run --workspace ./legacy-workspace --from review
ecc-fe run --workspace ./legacy-workspace --only lint --force
ecc-fe log lint --workspace ./legacy-workspace
```

原有 `--step lint --rerun` 和 `log --step lint` 写法继续兼容。

自定义 CPU 使用 filelist：

```bash
ecc-fe init ./my-cpu \
  --design my-cpu \
  --cpu-filelist ./rtl/filelist.cpu.f
```

## 环境诊断

`check` 是与 ECC 一致的工程检查入口；`doctor` 保留为环境诊断兼容名称，两者执行
相同检查。完整诊断：

```bash
ecc-fe doctor
```

只检查某个步骤所需工具：

```bash
ecc-fe doctor --step elab
ecc-fe doctor --step sim
```

`doctor` 会检查 Slang、Verilator、主机 C++ 编译器、Make、RISC-V 工具链、
前端 catalog 和资源清单。Yosys 是 review 增强能力，缺失时只提示 attention。
它不属于默认必需资源，可以按需执行 `ecc-fe resource install yosys`。

## 资源管理

```bash
ecc-fe resource list
ecc-fe resource status
ecc-fe resource install --required
ecc-fe resource install slang
ecc-fe resource update --required
ecc-fe resource env --shell zsh
```

`resource install --required` 会在开始下载前记录完整的必需资源集合。即使安装中途
因网络问题退出，`resource status` 也会列出尚未安装的必需项并返回退出码 `1`；
可以按提示单独重试该资源。

CLI 与桌面端共用以下目录，不需要重复安装：

```text
~/.local/state/ecos-studio/resources/manifest.json
~/.local/share/ecos-studio/tools/
```

可用 `XDG_STATE_HOME`、`XDG_DATA_HOME`、`XDG_CACHE_HOME` 覆盖。registry 地址可
用 `ECOS_REGISTRY_URL` 覆盖。

`resource env` 输出可用于一次性注入当前 shell：

```bash
eval "$(ecc-fe resource env --shell zsh)"
```

通常不需要执行这一步，因为 `ecc-fe` 启动时会自动读取资源清单。

## 工程配置和参数

`ecc-fe init <工程目录>` 会生成工程级 `ecc-fe.toml`、`runs/` 和内部的未执行
工作区模板。其中 `[design]`、`[frontend]` 和 `[defaults]` 是创建工程时的快照，
`[flow].run` 是未指定 `--run-id` 时使用的 run，`[params]` 是后续 run 的持久参数
覆盖层。每个 run 中的 `home/parameters.json` 仍是流程引擎和桌面端读取的运行时
文件，因此不会破坏已有 workspace/RPC 协议。

查看常用参数或按步骤筛选：

```bash
ecc-fe param list
ecc-fe param list --step sim --all
ecc-fe param show design.frequency_mhz
ecc-fe param diff
```

设置和撤销覆盖：

```bash
ecc-fe param set design.frequency_mhz 150
ecc-fe param set sim.compile_preset speed
ecc-fe param set sim.compile_opt_level --value=-O3
ecc-fe param set sim.compile_extra_cflags '["-g", "-Wall"]'
ecc-fe param unset design.frequency_mhz
```

参数是强类型的：布尔值接受 `true/false`，整数和浮点数会检查范围，列表使用
JSON 字符串数组。以 `-` 开头的值使用 `--value=<值>`，避免被命令行解析器当作
选项。在工程模式中，每次有效变更只更新工程配置，供后续新 run 使用；已存在的
run 保持可复现。单次实验使用可重复的 `ecc-fe run --set key=value`，覆盖会写入
该 run 的 `home/cli-param-overrides.json`，不会写回工程配置。

在直接工作区模式中，每次有效变更仍会同步到 `home/parameters.json` 并重置旧
流程状态，防止复用过期结果。也可以直接编辑 `[params]`；下一次
`ecc-fe run --workspace ...` 会先校验、同步并重置受影响的流程。

旧版桌面工作区没有 `ecc-fe.toml` 时仍可直接查询。第一次执行 `param set` 时，
CLI 会根据现有 workspace 生成配置文件；只读命令不会修改旧工作区。

## Catalog 查询

CLI 直接复用桌面端和 RPC 使用的前端 catalog：

```bash
ecc-fe catalog list
ecc-fe catalog list --kind core --status experimental
ecc-fe catalog show picorv32
ecc-fe catalog check
ecc-fe catalog validate --core-id picorv32 --test-suite-id smoke
```

`catalog check` 检查静态 catalog 契约；`catalog validate` 检查指定 CPU、SoC、
工具链和测试套件组合，并输出归一化后的配置和支持级别，不会启动编译或仿真。

## 前端报告

汇总流程状态和各步骤的 `frontend_detail.json`：

```bash
ecc-fe report qor
ecc-fe report qor --output ./frontend-qor.json
ecc-fe report files
ecc-fe report files --step lint
```

`report qor` 会原样聚合各步骤正式生成的 `qor_metrics.json`、
`qor_summary.json` 和 `qor_hotspots.json`，包括前端 readiness/quality score、
质量门禁、指标来源与热点。它不会把不同步骤的分数另算成一个未经定义的总分，也
不会虚构后端物理设计的面积、时序或功耗指标。`report files` 同时列出 `report/`
与 `analysis/` 下的文件路径、相对路径、大小和格式，便于脚本继续处理。

## 自动化输出

```bash
ecc-fe status --plain
ecc-fe status --json
ecc-fe status --jsonl
ecc-fe config --resolved --json
ecc-fe param diff --json
ecc-fe catalog validate --core-id picorv32 --json
ecc-fe report qor --json
```

- `--plain`：每行一个稳定的 `key=value` 记录，适合 shell。
- `--json`：单个 `{"records": [...]}` 对象。
- `--jsonl`：每行一个 JSON 对象，适合流式处理。

与 ECC 一致，`ecc-fe version --json` 是例外：它直接输出 schema 对象，不包裹
`records`。

退出码为 `0` 表示成功，`1` 表示业务或工具失败，`2` 表示参数错误，`130`
表示用户取消。

## 兼容命令

桌面端使用的 workspace/RPC 接口不变：

```bash
ecc-fe workspace catalog-check --json
ecc-fe workspace load --directory ./frontend-demo --json
ecc-fe rpc --help
```

原有参数模式也继续支持：

```bash
ecc-fe --design demo --top top --rtl demo.v
```
