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
ecc-fe run --only sim --clean        # 重跑 sim，并且全部从头构建
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

### 不是 CPU 的 RTL 设计

任意 RTL 设计也可以建工程，流程是 prepare、review、elab、lint；给了 testbench 时再加上 sim（见下一小节）：

```bash
ecc-fe init ./counter --top counter --filelist ./rtl/design.f
ecc-fe init ./counter --kind rtl --top counter --rtl ./rtl/counter.sv --rtl ./rtl/fifo.sv
cd counter && ecc-fe run
```

- 给了 `--filelist` 就按 RTL 设计处理；只给 `--rtl` 时默认仍当作自定义 CPU，要加
  `--kind rtl`。
- 源文件原地引用、不复制，改完 RTL 直接 `ecc-fe run --resume` 就会重跑受影响的步骤。
- `init` 会先检查 filelist 和文件是否存在、top 模块是否恰好定义一次，不满足时不会
  创建任何东西。
- 这类工程的 `check` 不要求 RISC-V 工具链；没有 testbench 时也不要求 C++ 编译器和
  Make，并且 `run --only sim` 之类会直接报 `step_not_in_flow`。如果只是因为这个 run 建得比
  testbench 的设置早，报错里会说明并给出新建 run 的命令。`param list` 不显示
  CPU 的 `sim.*` 参数。

工程类型记录在 `ecc-fe.toml` 的 `[design].kind`（`cpu` 或 `rtl`），创建后不能改。

### 用自己的 testbench 仿真 RTL 设计

RTL 设计用它自带的 SystemVerilog testbench 仿真。建工程时给出 testbench 文件和它的
顶层模块，流程里就多出 `sim` 步骤：

```bash
ecc-fe init ./counter --kind rtl --top counter --rtl ./rtl/counter.sv \
  --testbench ./tb/tb_counter.sv --testbench-top tb_counter
ecc-fe run --project ./counter
ecc-fe log sim --project ./counter
```

- `--testbench` 可以重复给多个文件，必须和 `--testbench-top` 一起给。
- testbench 不属于设计：不参与 review 和 lint，也不会进入交接清单。
- 已经建好的工程也可以后加：设置 `testbench.top` 和 `testbench.files`，
  之后新建的 run 就带 `sim` 步骤。

判定通过的规则（三条都满足才算通过）：

1. 仿真程序在时限内以退出码 0 结束。`$fatal` 和断言失败会让退出码非 0；
   testbench 一直不调用 `$finish` 会超时（时限由 `ECOS_FE_TIMEOUT_SIM_CASE` 控制）。
2. 设置了 `testbench.fail_pattern` 时，输出不能匹配它。
3. 设置了 `testbench.pass_pattern` 时，输出必须匹配它。

两个正则默认都不设置。这意味着 testbench 打印了 `FAIL` 再 `$finish` 时仍会被判为
通过，所以建议至少设一个：

```bash
ecc-fe param set testbench.pass_pattern 'TEST PASSED'
ecc-fe param set testbench.fail_pattern 'FAIL|ERROR'
```

没有设置任何正则而通过时，日志最后一行会说明这次只按退出码判定。失败时最后一行是
失败原因。其余参数：`testbench.plusargs`（传给仿真的 `+arg`）和
`testbench.extra_args`（额外的 Verilator 编译参数），以及 `testbench.coverage`
（收集设计的覆盖率，见"覆盖率"一节）。

`design.parameters` 通过 testbench 作用于仿真。仿真的顶层是 testbench，而工具只能
覆盖顶层模块的参数，所以规则是：testbench 模块自己声明了同名 `parameter` 的那些
覆盖值会传给它，由 testbench 再传给设计；testbench 没有声明的不生效。两类都会写在
日志最后几行和 `cases.json` 的 `design_parameters` 里：

```systemverilog
module tb #(parameter WIDTH = 8);
  fifo #(.WIDTH(WIDTH)) dut (.*);   // design.parameters 里的 WIDTH=16 经这里到达设计
endmodule
```

头文件：`testbench.files` 里列出的 `.vh` / `.svh` 不会被当作源文件编译，只把所在
目录加入 include 搜索路径。testbench 通过 `` `include `` 引用到的文件（包括间接
引用的）会和 testbench 源文件一起被跟踪，改了其中任何一个，sim 的结果就会过期。

目前的限制：一个流程只跑一个 testbench、一次仿真，没有用例列表。`sim.*` 参数是 CPU 工程用来配置测试程序的，对 RTL 设计
无效；反过来 `testbench.*` 对 CPU 工程无效。

### 移动工程目录

工程目录可以整体移动或复制。下一条命令会发现位置变了，把工程内部的路径（包括放在
工程目录里的 RTL 和 filelist）改写成新位置，并在 stderr 提示一行。放在工程目录之外的
输入仍按原来的绝对路径引用。移动后已有 run 的结果会被判为过期，下次运行会重跑。

用 `--workspace` 的直接工作区同样可以移动或复制，处理方式相同：下一条命令改写工作区
内部的路径并提示一行，已有结果会过期。

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

### CPU 仿真的运行方式

下面几个参数只改变仿真怎么跑，不改变结果；改动它们不会让已有的 sim 结果过期
（`sim.dump_waveform` 除外，它决定有没有波形文件）：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `sim.build_jobs` | `0` | 编译仿真程序和测试程序的并行数；`0` 表示用上所有核 |
| `sim.case_jobs` | `1` | 同时运行的用例数 |
| `sim.dump_waveform` | `true` | 每个用例是否写出 `wave.vcd`（CoreMark 始终不写） |
| `sim.coverage` | `false` | 是否收集行覆盖率和翻转覆盖率，见下文"覆盖率" |
| `sim.history_limit` | `20` | `report/runs/` 下保留最近多少次运行 |

几点说明：

- 仿真程序是增量构建的：编译参数和源文件列表没变时，只重编变了的部分。
- 整套 cpu-tests 的时间主要花在编译 35 个测试程序上。它们并行编译，并且没变的不再
  重编：程序源文件、公共运行库、编译选项、编译器都没变时直接用上次的镜像，
  `report/build_programs.log.txt` 里记为 `up to date`。
- 想不复用任何东西、全部从头构建时加 `--clean`：
  `ecc-fe run --only sim --clean`。它会重建仿真程序（清空 `obj_dir`）并重编所有
  测试程序。`--clean` 要和 `--only`、`--from` 或 `--rerun` 一起用。
- 不需要看波形时把 `sim.dump_waveform` 设为 `false`：整套 cpu-tests 的波形约 500 MB。
- 用例运行参数里显式写了 `--wave <文件>` 的，始终照写。
- 波形只有 VCD 格式：SoC 外壳的 C++ 驱动只支持 VCD。

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

每个步骤有三个互相独立的结论：

- **状态**（`state`）：步骤有没有跑完。
- **质量**（`quality`）：结果有没有通过门禁。`blocked` 表示有硬性条件没过（比如有
  elaboration 错误），`incomplete` 表示有门禁没法判定（比如没装 Yosys）。
- **分数**（`score`）：结果有多好，满分 100。分数不影响质量结论。

分数遵循三条规则（详见 `docs/specification/quality-scoring.md`）：

1. 只算评估过的项。不适用于该设计的检查、没运行的工具，对应的项标为
   `"evaluated": false`，不得分也不扣分；`evaluated_weight` 说明这次实际评估了多少权重。
2. 警告按密度算，即每千行 RTL 的警告数；不足 200 行的设计按 200 行计。错误不按规模
   折算，每个固定扣分。
3. 分数只描述这一次运行，不依赖以前的运行。两次运行的比较用 `report compare`。

权重和容忍度在 `fecompiler/analysis/scoring_policy.json` 里。

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

### 覆盖率

```bash
ecc-fe param set sim.coverage true         # CPU 工程
ecc-fe param set testbench.coverage true   # RTL 设计（用自己的 testbench 仿真）
ecc-fe run --run-id cov
ecc-fe report coverage --run-id cov
```

打开后，仿真程序带行覆盖率和翻转覆盖率构建，每个用例的数据合并成一份：某个
覆盖点只要有一个用例覆盖到就算覆盖。结果在两个文件里：

- `sim_verilator/report/coverage.json`：汇总，按总体、按源文件归属（`cpu`、
  `design`、`soc` 等）、按文件三个角度给出 `covered / total / percent`；
- `sim_verilator/output/coverage.dat`：合并后的原始数据，可以交给
  `verilator_coverage --annotate <目录>` 生成逐行标注的源码。

```text
$ ecc-fe report coverage --ownership cpu --limit 3
  line 65.4% (117/179)  toggle 74.9% (5345/7134)  branch 63.4% (293/462)  rtl/core.v  [cpu]
```

`report coverage` 先给总数，再按覆盖率从低到高列出文件；`--ownership` 只看某一类
源文件，CPU 工程里通常只关心自己的 CPU，而不是 SoC 外壳。

几点说明：

- 种类有 `line`、`toggle`，以及 Verilator 归在行覆盖率里的 `branch`。
- RTL 设计的 testbench 自己的文件不计入。
- 单个用例的数据合并后就删除（一个 SoC 规模的设计每个用例约 10 MB）。
- CPU 的覆盖率要靠 SoC 的仿真外壳把数据写出来。外壳太旧时，仿真照常进行，报告里
  `status` 为 `unavailable` 并说明原因，执行 `ecc-fe resource update` 更新 SoC 资源
  即可。
- 开销：实测一次整套 cpu-tests（35 个用例、8 个并行）从 32 秒变为 37 秒。
- 覆盖率目前不参与评分，也不在 `report compare` 里比较。

### 导出给 CI：SARIF 和 JUnit

```bash
ecc-fe report export --format sarif    # lint 诊断 -> lint.sarif
ecc-fe report export --format junit    # 仿真用例 -> sim.junit.xml
```

这条命令只转换流程已经写好的结果，不会重新运行任何步骤；无论结果好坏退出码都
是 0，流程通过与否看 `run` 的退出码。`-o` 指定输出文件。

SARIF（2.1.0）供 GitHub code scanning 这类界面使用：

- 默认只包含你自己源文件里的诊断和所有错误；SoC 外壳等不属于你的源文件里的
  警告不带，数量记在输出的 `left_out` 里，加 `--all` 可以全部带上。
- 位于 `--base-dir`（默认是当前目录）之下的文件写成相对路径，所以应当在仓库
  根目录执行，或者用 `--base-dir` 指向仓库根目录；其余文件保留绝对路径。
- 配置了 lint 基线时，已知警告标为 `unchanged`，新增警告标为 `new`。

JUnit 每个仿真用例对应一个 `testcase`，未通过的带失败类型、原因和日志结尾。
如果 sim 步骤失败但没有任何用例失败（例如测试程序编译失败），报告里会有一条
名为 `sim` 的错误，避免"没有用例"被当成通过。用例耗时没有单独记录，所以不写。

GitHub Actions 里的用法示例：

```yaml
- run: ecc-fe run --project fe
- if: always()
  run: |
    ecc-fe report export --format sarif --project fe
    ecc-fe report export --format junit --project fe
- if: always()
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: lint.sarif
```

## 排查问题

### 某一步为什么失败

```bash
ecc-fe report failure            # 第一个失败的步骤
ecc-fe report failure --step sim # 指定步骤
```

`doctor` 检查的是环境；这条命令回答的是"工具都跑了，这一步为什么没过"。它只读取
步骤留下的结果，不重新运行任何东西。`status` 和失败的 `run` 的输出里都带
`failure_cmd`，照着执行即可。

```text
$ ecc-fe report failure
  reason: the lint step failed: 2 warnings not in the lint baseline
  advice: ["fix the new warnings, waive them (lint.waiver_files), or accept them with `ecc-fe report lint-baseline`"]
  rerun_cmd: ecc-fe run --from lint
  [lint_new_warning]  rtl/adder.v:2:32  WIDTHEXPAND  Operator ADD expects 8 bits on the LHS ...
  [lint_new_warning]  rtl/adder.v:2:27  WIDTHTRUNC  Operator ASSIGNDLY expects 4 bits ...
```

每条原因带类别、位置和说明。各步骤会列出的内容：

| 步骤 | 列出什么 |
| --- | --- |
| prepare | 不可用的输入 |
| review | Yosys 报的原话，以及未豁免的 error 级问题和处理建议 |
| elab | slang 的错误、未定义的模块、找不到的顶层 |
| lint | Verilator 的错误；配了基线时，基线里没有的新警告 |
| sim | 未通过的用例（失败类型、第一条错误、日志和波形路径），或者没编过的 testbench / 测试程序 |

此外还会列出未通过的质量门禁（实际值和要求值）。结果里完全找不到原因时，才会去
日志里取第一条错误行。默认最多列 10 条，`--limit` 可以调。

### 其他

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

## 通过其他命令调用

如果 `ecc-fe` 是被另一个命令转发调用的（例如 `ecc fe ...`），转发方设置
`ECC_FE_PROGRAM`，输出里的程序名就会跟着变：

```bash
ECC_FE_PROGRAM="ecc fe" ecc-fe status
# ecc fe: error: workspace_not_found
#   remediation_cmd: ecc fe run
```

受影响的只有输出：用法和帮助、文本模式下的消息前缀、所有 `*_cmd` 字段和
`reason` 里引用的命令。记录的种类、字段名、错误码和退出码不变。

写到磁盘上的内容不随它变化：`ecc-fe.toml`、运行目录、日志、lint baseline
和交接清单里出现的仍是 `ecc-fe`。`version` 也仍然报告 `ecc-fe`。

取值只接受 1 到 4 个由字母、数字和 `_ . + / -` 组成的词，其他取值会被忽略。

## 让 lint 收敛

lint 的行为通过参数配置，写在 `ecc-fe.toml` 的 `[params]` 里（或用 `param set`）：

```bash
ecc-fe param set lint.profile basic                       # 只用 Verilator 默认的警告集，不加 -Wall
ecc-fe param set lint.rules_off '["UNUSEDSIGNAL"]'        # 关掉某些规则
ecc-fe param set lint.waiver_files '["waivers.vlt"]'      # Verilator 的 .vlt 豁免文件
ecc-fe param set lint.extra_args '["--timescale","1ns/1ps"]'
ecc-fe param set elab.extra_args '["--max-generate-steps","200000"]'
ecc-fe param set design.parameters '["WIDTH=16"]'         # 覆盖 top 的参数，elab 和 lint 都生效
```

路径相对工程目录解析。豁免文件的内容变了，lint 会被判为过期并重跑。

### "不新增警告"的门禁

存量警告一时清不完时，可以先把现状记成 baseline，之后只拦新增的：

```bash
ecc-fe run --to lint
ecc-fe report lint-baseline            # 生成 lint-baseline.json
ecc-fe param set lint.baseline lint-baseline.json
```

之后每次 lint：

- baseline 里有的警告标为 `known`，没有的标为 `new`。
- 自己代码里出现 `new` 警告时 lint 失败，日志末尾会说明有几条；SoC、第三方代码里的新
  警告只报告、不拦。
- 警告按"规则 + 文件名 + 消息"识别并计数，不看行号，所以在上面加几行代码不会让旧警告
  变成新的；同一条警告多出现一次算新增。
- 修掉的警告计入 `fixed`；想把 baseline 收紧，重新执行 `report lint-baseline` 即可。

没有设置 baseline 时行为和以前一样：警告不会让 lint 失败。

## 交接给后端

流程跑完后，用 `handoff` 生成交接清单，后续工具（如 ECC）只读这份清单：

```bash
ecc-fe handoff                         # 写到 handoff/<run-id>/，RTL 按路径引用
ecc-fe handoff --bundle -o ./export    # 把 RTL 一起复制进去，目录可以拷走
```

目录里有 `handoff.json`（清单）和 `design.f`（同一份设计的 filelist，带 `+incdir+`
和 `+define+`）。

- 只交接你自己的设计。CPU 工程交接的是 CPU 本身，top 是 CPU 的顶层；仿真用的 SoC
  外壳不在其中。
- 默认要求所有步骤都成功、且结果没有过期，否则报 `handoff_not_ready` 并列出原因。
- 默认还会用 slang 只凭清单内容把设计单独编译一遍，确认它离开前端环境也成立；
  不成立时报 `handoff_design_incomplete`，日志在 `standalone_check.log`。
- `--allow-incomplete` 可以强行生成，清单里会标明 `incomplete` 和原因。
- 时序约束：`ecc-fe handoff --sdc ./constraints/top.sdc` 把一份 SDC 随设计一起交接，
  也可以事先用 `ecc-fe param set design.sdc <文件>` 记在工程里（对之后新建的 run
  生效）。前端流程不读取它，只在清单的 `constraints.sdc` 里记下路径和哈希，
  `--bundle` 时复制到 `constraints/` 下。文件不存在时报 `handoff_sdc_not_found`。

清单的字段见 `docs/specification/handoff-manifest.md`。

## 查看流程由哪些步骤组成

```bash
ecc-fe flow list          # 这个设计的流程有哪些步骤，按顺序
ecc-fe flow show sim      # 一个步骤的工具、参数，以及什么会让它的结果过期
```

这两条命令只读，说的是流程"由什么组成"；每一步跑没跑、结果如何看 `status`。
和 `param list` 一样，在工程里默认描述下一次新建的 run，加 `--run-id` 描述
已有的 run。

```text
$ ecc-fe flow list
  1  prepare   fe          prepare_fe
  2  review    fe          review_fe
  3  elab      slang       elab_slang
  4  lint      verilator   lint_verilator
  -  sim       not in this flow: an RTL design is simulated when it names a testbench (testbench.top)
```

四列依次是顺序、步骤名、工具、步骤目录。不属于这个设计流程的步骤以 `-` 开头
并说明原因，上面是一个没有 testbench 的 RTL 设计。

`flow show <步骤>` 的主要内容：

| 字段 | 含义 |
| --- | --- |
| `after` / `before` | 流程里的前一步和后一步 |
| `inputs_from` | 它读的输入由哪一步准备 |
| `programs` | 用到的外部程序 |
| `environment_checks` | `doctor --step` 为它检查的项目 |
| `parameters` | 这个设计里作用于该步骤的参数 |
| `qor_report` | 质量分析读取的报告文件 |
| `stale_when` | 哪些东西变了，已成功的结果就算过期 |
| `run_cmd` / `params_cmd` / `doctor_cmd` | 只跑这一步、看它的参数、检查它的环境 |

`stale_when` 里 `parameters` 是会让结果过期的参数，`parameters_not_tracked` 是
只影响怎么跑、不影响结果的参数（例如 `sim.build_jobs`），`sources` 是参与步骤
签名的 ECC-FE 源文件。

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
