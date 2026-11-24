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

每次工程运行都位于独立的 `runs/<run-id>` 目录。run id 只能是普通名字（1-64 个字母、
数字、`.`、`_`、`-`，以字母或数字开头），不能是路径；其他写法会被拒绝并返回
`invalid_run_id`。创建另一个实验运行：

```bash
ecc-fe run --run-id faster-sim --set sim.compile_preset=speed
ecc-fe status --run-id faster-sim
ecc-fe log sim --run-id faster-sim
ecc-fe config sim --resolved --run-id faster-sim
```

工程引用的是 RTL 的路径，不会把 RTL 拷进工程，所以改了 RTL 之后不需要"重新导入"：

```bash
ecc-fe status      # 结果已过期的步骤会带 stale=True 和原因，并给出继续的命令
ecc-fe run --resume
```

`status` 只是提示，不会改动 run；真正的重跑发生在下一次 `run`，它会从第一个过期的步骤
开始重跑。新建的 run 读到的也总是当前的文件。

运行过程中 `ecc-fe run` 会在每个步骤开始和结束时输出一行进度；`--jsonl` 和 `--plain`
下进度以 `kind=progress` 的记录逐行输出到 stdout，便于脚本边跑边读。另开一个终端可以
跟随日志：

```bash
ecc-fe log sim --follow      # 打印日志末尾并持续输出新增内容，run 结束后自动退出
ecc-fe log --follow          # 跟随整个流程的日志
```

在已有的 run 上迭代（改了 RTL 后只重跑需要的步骤）：

```bash
ecc-fe run --to lint                 # 新建 run，只跑到 lint，跳过耗时的 sim
ecc-fe run --resume                  # 从第一个未完成的步骤继续
ecc-fe run --from review             # 从 review 开始重跑后面的步骤
ecc-fe run --from review --to lint   # 只重跑 review 到 lint
ecc-fe run --only lint --force       # 只重跑 lint
ecc-fe run --run-id faster-sim --only sim
```

这些选择器在原地继续已有的 run，不会新建；run 不存在时报 `run_not_found`。run 的参数在
创建时就固定了，之后改 `[params]` 不会影响它，要换参数请用 `--run-id` 新建一个 run。

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
  --cpu-filelist ./rtl/filelist.cpu.f \
  --cpu-top-module my_cpu_top
```

`--cpu-top-module` 省略时，filelist 形式默认顶层模块名为 `cpu_top`。

## 工作区版本

每个工作区在 `home/home.json` 里记录一个 `schema_version`（当前为 1），表示工作区目录
结构和 `home/` 下状态文件的版本。没有这个字段的旧工作区按版本 1 处理，不会被改写。

如果工作区是用更新的 ecc-fe 创建的（版本号比当前运行时认识的大），所有命令都会拒绝
读写它并提示升级 ecc-fe，不会按旧格式去猜。

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

查看某个 run 实际使用的参数（而不是工程当前的设置）：

```bash
ecc-fe param list --run-id faster-sim
ecc-fe param diff --run-id faster-sim
ecc-fe config --resolved --run-id faster-sim
```

带 `--run-id` 时显示该 run 创建时的值：来自 `run --set` 的标为 `cli`，其余与基线不同的
标为 `run`。不带 `--run-id` 时显示的是下一个新 run 会用的值。

参数只有一个来源，按模式区分：

- **工程模式**：`ecc-fe.toml` 是唯一来源。新建 run 时用 `[defaults]` 加 `[params]`
  的值；run 建好后它的 `home/parameters.json` 只是记录，不会再被改写。
- **直接工作区模式**：`home/parameters.json` 是唯一来源，桌面端和 RPC 改的也是它。
  `param set` 直接写这个文件并重置旧流程状态；`ecc-fe run --workspace ...` 按工作区
  现状运行，不会再用 `ecc-fe.toml` 里的值去覆盖它。此时 `ecc-fe.toml` 只保存
  `[defaults]` 基线，供 `param unset` 恢复用，其中的 `[params]` 不生效。

旧版桌面工作区没有 `ecc-fe.toml` 时仍可直接查询。第一次执行 `param set` 时，
CLI 会根据现有 workspace 生成这份基线文件；只读命令不会修改旧工作区。

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

比较同一工程的两个 run：

```bash
ecc-fe report compare default faster-sim
ecc-fe report compare default faster-sim --all                 # 连没变的指标也列出
ecc-fe report compare default faster-sim --fail-on-regression  # 变差时退出码为 1
```

第一个是基准，第二个是被评判的 run。输出包括每个步骤的状态、质量结论和分数，
以及数值发生变化的指标；指标按自身的方向（越高越好 / 越低越好）标为 `improved`
或 `regressed`，没有方向的只标 `changed`。汇总里的 `parameter_changes` 列出两个
run 之间不同的参数。某个步骤标出"inputs or settings differ"时，表示两边的输入
或设置不同，数字不能直接当作同一条件下的对比。

## 排查问题

`ecc-fe init` 失败时会列出每一条原因（`init_issue` 记录，带 `code` 和 `message`），
并清掉它自己创建的目录；目录里原有的文件不会被动，修正后可以直接重新执行。

`status` 的每个步骤带有 `runtime_s`（精确到毫秒的耗时）和 `peak_memory_mb`
（该步骤期间 ECC-FE 及其子进程的内存峰值，仅 Linux，每秒采样 5 次）。

命令因内部异常失败时报 `internal_error`。加 `--debug`（放在子命令前）或设置
`ECC_FE_DEBUG=1` 可以把调用栈打印到 stderr：

```bash
ecc-fe --debug status
ECC_FE_DEBUG=1 ecc-fe run
```

## 管理 run

```bash
ecc-fe runs list                 # 列出工程里所有 run 及其状态
ecc-fe runs delete old-a old-b   # 删除 run
```

`runs list` 显示每个 run 的整体状态（与 `status` 一致）、完成的步骤数、最后
更新时间和创建时用 `--set` 指定的参数，并标出不带 `--run-id` 时默认使用的 run。

`runs delete` 不会询问确认，删除后无法恢复。正在运行的 run 会被拒绝
（`workspace_busy`）；run id 必须是普通名字，符号链接和非 run 目录不会被删除；
工程模板和 `ecc-fe.toml` 不受影响。

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

布尔值在所有模式下都写作 `true` / `false`。文本模式下字段名与结构化模式一致
（例如 `status_cmd`），不会和 `status` 混在一起。

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令完成了它该做的事 |
| `1` | 命令失败：工作区不存在或读不了、流程步骤失败、检查未通过、资源或工具出错 |
| `2` | 调用方式有误：未知的选项、步骤、参数名，值无法解析，或选项互相冲突；此时不会读写任何东西 |
| `130` | 用户取消 |

退出码描述的是命令本身，不是设计的状态。`status`、`log`、`config`、`report`
只要能读到内容就返回 `0`，流程有步骤失败也一样；脚本应该读输出里的状态：

```bash
ecc-fe status --json | jq -r '.records[0].state'   # running / interrupted / failed / success / pending
```

`run` 在有步骤失败时返回 `1`，`check` 和 `doctor` 在必需项未通过时返回 `1`。

### 错误输出

错误记录是 `kind=error`，其中 `error` 一定是稳定的小写代码（如
`workspace_not_found`、`unknown_step`、`log_not_found`），不会是一句话；
细节在 `message`、`reason` 等字段里。

- 文本模式：错误写到 stderr，形如 `ecc-fe: error: <代码>` 加若干字段行；
  stdout 只有正常记录。
- `--json` / `--jsonl` / `--plain`：错误记录和其他记录一样写到 stdout，
  消费方只需要读 stdout。

### `run` 的输出

`run` 先为每个执行过的步骤输出一条 `step` 记录（`step`、`tool`、`state`、
`log`、`report`），最后输出 `run` 记录。步骤产物不再塞进 `run` 记录，
用 `ecc-fe report files` 查看。

## 兼容命令

桌面端使用的 workspace/RPC 接口不变：

```bash
ecc-fe workspace catalog-check --json
ecc-fe workspace load --directory ./frontend-demo --json
ecc-fe rpc --help
```

旧的直连参数形式（`ecc-fe --design demo --top top --rtl demo.v`）已移除：它在未指定
`--workspace` 时会把工作区建在安装目录里。现在这样调用会以状态码 2 退出并提示改用
`ecc-fe init` 加 `ecc-fe run`，不会创建任何文件。
