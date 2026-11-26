# ECC-FE CLI Design Specification

This document defines the ECC-FE command-line contract. It follows the ECC
backend CLI conventions while retaining frontend-specific workspace and
resource operations.

## Goals

- Make ECC-FE usable without Electron.
- Keep project and flow commands consistent with the ECC backend.
- Share workspace use cases with JSON-RPC instead of duplicating business logic.
- Reuse the desktop Resource Manager registry and manifest.
- Provide stable output and exit behavior for shell scripts and agents.

## Command Surface

```text
ecc-fe --version
ecc-fe version
ecc-fe init
ecc-fe check
ecc-fe doctor
ecc-fe run
ecc-fe status
ecc-fe log
ecc-fe config
ecc-fe resource list|status|install|update|uninstall|prune|env
ecc-fe param list|show|set|unset|diff
ecc-fe catalog list|show|check|validate
ecc-fe report qor|files|compare|lint-baseline|export|failure|coverage
ecc-fe runs list|delete
ecc-fe flow list|show
ecc-fe handoff
ecc-fe workspace ...
ecc-fe rpc ...
```

`workspace` and `rpc` remain compatibility surfaces for ECOS Studio. New
project-oriented commands call the same workspace application service.

`ecc-fe check` is the canonical project validation command shared with ECC.
`ecc-fe doctor` retains the same checks and is the compatibility name for
environment-focused diagnostics.

The former direct form, `ecc-fe --design <name> --top <top> ...`, has been
removed. It created its workspace inside the installation tree when
`--workspace` was omitted. Such an invocation now exits with status 2 and
prints the replacement, `ecc-fe init` followed by `ecc-fe run`, without
creating anything. `ecc-fe init --cpu-top-module` names the top module of a
custom CPU given by `--cpu-filelist` or `--rtl`.

## Output Contract

Inspection and mutation commands support four output modes:

| Mode | Contract |
| --- | --- |
| default | Human-oriented text. Not a parsing interface. |
| `--plain` | One stable `key=value` record per line. |
| `--json` | One object with a `records` array. |
| `--jsonl` | One JSON record per line. |

When multiple modes are provided, precedence is `jsonl`, `json`, `plain`, then
default text. Text and structured modes describe the same records.

Summary records expose follow-up commands through fields ending in `_cmd`.
Examples include `status_cmd`, `log_cmd`, `next_cmd`, and `remediation_cmd`.
Text mode prints field names exactly as the structured modes do, so
`status_cmd` never reads as a second `status`. Booleans are `true` and `false`
in every mode.

### Streams

| Mode | stdout | stderr |
| --- | --- | --- |
| text | records | error records, `run` progress lines |
| `--json` | one document, error records included | `run` progress lines |
| `--jsonl`, `--plain` | every record, errors and progress included | nothing |

A structured consumer reads stdout only. In text mode an error is written to
stderr as `ecc-fe: error: <code>` followed by its fields, and stdout carries
only the records that are not errors.

### Error Records

An error record is `kind=error` with `error` set to a stable lower-case code
(`[a-z][a-z0-9_]*`), never a sentence. Detail is carried by other fields:
`message` for the text of an underlying failure, `reason` for a rule that was
broken, and the offending value under its own name (`step`, `param`, `run`,
`workspace`).

| Code | Meaning |
| --- | --- |
| `workspace_not_found` | The selected workspace or run does not exist. |
| `workspace_unreadable` | A workspace state file is missing or malformed. |
| `workspace_busy` | Another command holds the workspace lock. |
| `unknown_step`, `unknown_parameter` | The name is not one ECC-FE knows; `expected` lists the steps. |
| `invalid_value`, `invalid_parameter`, `invalid_run_id` | The value is not acceptable; `reason` says why. |
| `log_not_found` | The step has not produced a log yet. |
| `invalid_resource_selection`, `unsupported_shell` | `resource` was called with a bad selection. |
| `resource_failed`, `resource_install_failed`, `invalid_resource_manifest` | Resource management failed. |
| `internal_error` | An unexpected exception; `message` and `exception` describe it. `ecc-fe --debug <command>` or `ECC_FE_DEBUG=1` prints the traceback to stderr. |
| `command_failed` | A failure that reached the output without a code of its own. |

The selector, run, parameter, catalog, and report sections list the codes
specific to them.

### `run` Records

`run` writes one `step` record per executed step (`step`, `tool`, `state`,
`log`, `report`) and then the `run` record, which is always last. The `run`
record carries `status`, the selectors that were used, `messages`, and
follow-up commands; when the application reports a coded failure it also
carries `error`. Step artifacts are not repeated here: `report files` lists
them.

### `status` Records

The first record is the `workspace` summary; one `step` record follows per
flow step. The summary carries `state`, the flow as a whole:

| `state` | Meaning |
| --- | --- |
| `running` | A command holds the workspace. |
| `interrupted` | A step was running when its command died. |
| `failed` | A step failed; `failed_step` names the first one. |
| `success` | Every step succeeded. |
| `pending` | Nothing failed and some steps have not run. |

Each `step` record carries `runtime` (`HH:MM:SS`, whole seconds),
`runtime_s` (seconds with millisecond precision), and `peak_memory_mb`: the
largest resident memory of the ECC-FE process and all the processes the step
started, sampled five times a second. Peak memory is measured on Linux only
and is `0` elsewhere; a process that lives for less than one sample can be
missed. Steps finished by an older version have no `runtime_s`.

`ecc-fe version --json` follows ECC's version exception and emits the schema
object directly; its `--jsonl` and `--plain` forms remain record-oriented.

### Program Name

ECC-FE may be started through a wrapper, such as an `ecc fe` subcommand that
passes its arguments on unchanged. The wrapper sets `ECC_FE_PROGRAM` to the
words a user types to reach ECC-FE (`ECC_FE_PROGRAM="ecc fe"`), and ECC-FE uses
them wherever it names itself in output:

- usage and help text (`Usage: ecc fe run [OPTIONS]`);
- the prefix of text-mode messages (`ecc fe: error: unknown_step`);
- every `*_cmd` field and every command quoted in a `reason`, so a suggested
  command can be typed back as printed.

The rules are:

- The value is one to four words of letters, digits and `_ . + / -`. Anything
  else, including an empty value, is ignored and the name stays `ecc-fe`.
- Only output changes. Record kinds, field names, error codes and exit codes
  are the same under any name.
- Nothing written to disk depends on the name: `ecc-fe.toml`, run directories,
  logs, lint baselines and handoff manifests are identical however ECC-FE was
  started. Text inside those files says `ecc-fe`.
- `ecc-fe version` and `--version` report the product as `ecc-fe`.
- Values taken from the user (paths, project and run names) are never
  rewritten, even when they contain `ecc-fe`.

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | The command did what it was asked. |
| `1` | The command failed: a missing or unreadable workspace, a failed flow step, a failed check, a resource or tool failure. |
| `2` | The invocation is wrong: an unknown option, step, or parameter, a value that does not parse, or options that cannot be combined. Nothing was read or changed. |
| `130` | User cancellation. |

The exit status describes the command, not the design. `status`, `log`,
`config`, and `report` exit `0` whenever they could read what they were asked
for, whatever the flow state is; a script reads the state from the records
(`status` summary `state`). `run` exits `1` when a step fails, and `check` and
`doctor` exit `1` when a required check fails, because producing that result
is what those commands are for.

## Workspace Locking

Commands that modify a workspace hold an exclusive advisory lock for their
whole duration: `run`, `param set`/`param unset` on a direct workspace,
workspace creation, and the `run-flow`, `run-step`, `reset-flow`, and
`refresh-config` operations shared with JSON-RPC. The lock is a `flock` on the
hidden sibling file `<parent>/.<workspace-name>.lock`, so it also covers
creating and replacing the workspace directory, and the kernel releases it
when the owning process exits.

A command never waits for the lock. When another live command owns the
workspace it fails with exit status `1` and one record:

```text
kind=error error=workspace_busy workspace=<path> owner_pid=<pid>
```

Inspection commands (`status`, `log`, `config`, `report`) do not take the lock
and remain usable while a flow is running. Steps left in the running state are
reset by recovery only when no live command holds the lock.

`status` probes the lock without taking it. Its workspace record carries
`running` and, while a command owns the workspace, `owner_pid`. A step stored
as running whose owner is gone is reported as `state=interrupted`; the
workspace record then carries `state=interrupted` and `resume_cmd`. The
command still exits with status `0`. `status` never rewrites the stored state.

`run --overwrite` builds the replacement run beside the existing one and swaps
it in only after it is complete. If the template, the parameter overrides, or
the copy fails, the previous run is left untouched.

Workspace and project state files (`home/*.json`, `ecc-fe.toml`, step
`subflow.json` and `checklist.json`) are replaced atomically: an interrupted
write leaves the previous complete file.

## Workspace Schema Version

A workspace records the version of its layout and of the state files under
`home/` once, as `schema_version` in `home/home.json`. The current version is 1.
A workspace without the field predates it and is version 1; it is not
rewritten to add the field.

An ecc-fe that finds a version it does not know (a larger number, or a value
that is not a positive integer) refuses the workspace instead of guessing:

- every command and JSON-RPC operation fails without reading further or
  modifying anything, including `create` over the existing directory and
  interrupted-step recovery;
- the application result carries `error: "unsupported_workspace_schema"`,
  `schema_version` (the value found), and `supported_schema_version`;
- the message tells the user to update ecc-fe.

The version is raised only when a change would make an older ecc-fe misread a
workspace, for example a renamed state file or a changed meaning of an
existing field. Adding an optional field does not raise it. `ecc-fe.toml` and
the report artifacts keep their own `schema_version` fields.

## Workspace Queries

A directory is a workspace only if it contains `home/parameters.json`. Every
operation, including the JSON-RPC ones, refuses any other directory and leaves
it untouched.

Loading or querying a workspace keeps each step's recorded stages
(`subflow.json`) and checklist. They are reset only when the step's result is
invalidated: by `reset-flow`, a full rerun, or a stale upstream. `get-info` and
`get-home` create no files or directories.

## Tool Execution

Every external tool a step runs (Slang, Verilator, the simulator binary, test
program builds, the Yosys precheck) goes through one runner:

- The tool and everything it spawns run in their own process group. On a
  timeout, on `Ctrl-C`, or on `SIGTERM` the whole group is terminated
  (`SIGTERM`, then `SIGKILL` after 5 seconds).
- stdout and stderr are interleaved directly into the step log file while the
  tool runs, so `tail -f` shows progress and the log survives an interruption.
  Tools do not inherit the command's stdin.
- Each call has a wall-clock limit. A call that exceeds it fails its step and
  the log names the limit.
- A command killed with `SIGKILL` cannot stop its tools. They are identified
  and stopped the next time the same step runs, before it starts new work.

Default limits, each overridable in seconds through the environment (`0`
disables that limit):

| Call | Default | Variable |
| --- | --- | --- |
| Slang elaboration | 30 min | `ECOS_FE_TIMEOUT_ELAB` |
| Verilator lint | 30 min | `ECOS_FE_TIMEOUT_LINT` |
| Simulator compile | 2 h | `ECOS_FE_TIMEOUT_SIM_COMPILE` |
| One test program build | 30 min | `ECOS_FE_TIMEOUT_PROGRAM_BUILD` |
| One simulation case | 4 h | `ECOS_FE_TIMEOUT_SIM_CASE` |

The Yosys precheck keeps its fixed 45 second review bound.

## Resource Contract

The CLI and Electron use the same state:

```text
$XDG_STATE_HOME/ecos-studio/resources/manifest.json
$XDG_DATA_HOME/ecos-studio/tools/<name>/<version>/
```

Defaults are `~/.local/state` and `~/.local/share`. The manifest schema remains
version 3 and tool entries use the desktop fields `type`, `name`, `version`,
`path`, `installed_at`, `sha256`, `size`, `detected_executables`, `executable`,
`active`, and `managed`.

The top-level `required_resources` array snapshots the dependency set resolved
for the installed ECC-FE runtime. It is written before dependency downloads
start, so `resource status` can report an interrupted installation from local
state without contacting the registry. Missing required resources make the
command exit with status `1` and include a resource-specific remediation
command.

Install order is dependency-first. An installation is published only after:

1. platform asset resolution;
2. exact byte-size and SHA256 verification;
3. path-safe archive extraction;
4. resource health checks;
5. atomic directory replacement and atomic manifest write.

On failure, the prior installed directory and manifest entry remain available.

### Archives

A tar archive may hold regular files, directories, symbolic links and hard
links. A member whose path leaves the archive is refused. A symbolic link is
judged from the directory it is in and a hard link from the archive root, in
both cases after `..` is resolved: `bin/tool -> ../lib/tool` is accepted, a
target that ends up outside the archive, or an absolute one, is refused.
Where Python provides it, extraction also runs under the `data` filter, which
drops set-id bits. A zip archive may hold no symbolic links.

An archive whose members add up to more than `ECOS_EXTRACT_MAX_BYTES`
(default 64 GiB) is refused before anything is written.

What a `post_install` command prints goes to stderr, so the records on stdout
stay one document; when it fails, the last lines it printed are in the error.

### The Manifest

A manifest that exists and cannot be used (unreadable, empty, not JSON, not
an object, or with an `installed` field that is not an object) is
`invalid_resource_manifest` with `path`, `reason` and `remediation`, from
every `resource` command including `install`. Other commands read it as
empty, which hides the installed tools, and say so in one warning line on
stderr. `doctor` reports it under `resource_manifest` with the same
remediation.

### Downloads

Everything fetched is held to the size and SHA256 the registry gives, so
where the bytes come from does not have to be trusted. On that rests:

- **Continuing.** An archive is downloaded to
  `$XDG_CACHE_HOME/ecos-studio/downloads/<name>-<version>-<sha256 prefix>.archive.part`
  and renamed without `.part` once its size and checksum are right. A later
  attempt, in the same command or the next one, asks the server for the bytes
  after those on disk (`Range`); a server that does not support that sends
  the whole file again. A partial file whose continuation fails the checksum
  is discarded and fetched from the start.
- **Retrying.** After a network error or a 5xx answer the same address is
  tried again, `ECOS_DOWNLOAD_ATTEMPTS` times in all (default 4), waiting 1,
  3, 10 and 30 seconds. 400, 401, 403, 404 and 410 are not retried, and
  neither is a file that arrived whole with the wrong checksum: the next
  address, if there is one, is tried instead.
  `ECOS_DOWNLOAD_TIMEOUT` is the seconds one read may take (default 60).
- **Mirrors.** `ECOS_RESOURCE_MIRRORS` holds `prefix=replacement` rules
  separated by whitespace or commas. An address that starts with a prefix is
  tried with the prefix replaced first, then as it is.
- **Cache.** A complete archive already in the cache is used without a
  request. The archive is removed after the install has succeeded and kept
  when a later step of the install fails, so the retry does not download it
  again.
- **Local archive.** `resource install <id> --archive <file>` uses a copy of
  the archive that is already on the machine, after the same size and
  checksum test; it cannot be combined with `--required`.
- **Registry.** `ECOS_REGISTRY_URL` may name a local file. Each registry read
  over the network is saved as `downloads/registry.json`; when the registry
  cannot be read, that copy is used and a line on stderr says so.
- **Release metadata** (`metadata_url`) that cannot be read does not stop an
  install; metadata that disagrees with the registry's lock still does.

### Listing, Removing And Cleaning Up

`resource list --installed` reads the manifest only and makes no request;
`available_version` is `null` there. `resource list` asks the registry and,
for resources that have it, their release metadata; those requests are made
side by side and each waits at most 10 seconds.

`resource uninstall <id>` removes the resource's manifest entry, then its
directory. The directory is removed only when the resource manager put it
there (the entry is managed and its path is below
`tools/<name>/`); otherwise the entry alone is dropped and the record says
`status=unregistered`, `files=kept`. The record is `resource_uninstall` with
`version`, `path`, `bytes`, `required`, and `reinstall_cmd` for a resource
ECC-FE requires. A resource that is not installed is
`resource_uninstall_failed`.

`resource prune` lists what can be removed without changing what is
installed, and removes it only with `--apply`:

| `category` | What |
| --- | --- |
| `old_version` | A version directory of an installed tool that is not the installed version. |
| `staging` | `.extract-*` and `.backup-*` directories an install left behind. |
| `download` | Archives and partial downloads in the download cache. |

It writes a `resource_prune_summary` (`applied`, `items`, `removed_bytes`,
`removable_bytes`, `kept_recent`, `failed`, `apply_cmd`) and one
`resource_prune` record per item with `status` `would_remove`, `removed`,
`kept_recent` or `failed`. Staging directories and downloads younger than an
hour are kept, because an install may be using them. Version directories of
a tool that has no manifest entry, or whose installed path is outside its
tools directory, are not judged.

An install first repairs what a killed install of the same tool left: a
backup of the installed version is moved back when the installed directory
is gone, and staging directories older than an hour are removed.

Progress events (`resource_progress`) have the phases `downloading`,
`resuming`, `retrying` (with `attempt`, `of`, `after_s`, `reason`), `mirror`,
`cached`, `verifying` and `done`; in text mode they are lines on stderr.
Explicit environment variables take precedence over managed resources. Healthy
managed resources take precedence over tools found later on `PATH`.

The variables of one tool belong together and are taken from its managed
installation as a group or not at all:

| Tool | Set from the managed installation | Left alone when the user set |
| --- | --- | --- |
| Verilator | `ECOS_VERILATOR`, `VERILATOR_ROOT` | either of them to something else, or `VERILATOR` |
| slang | `ECOS_SLANG` | it to something else, or `SLANG` |
| Yosys | `CHIPCOMPILER_OSS_CAD_DIR`, `ECOS_ELECTRON_OSS_CAD_DIR` | either of them to something else, or `YOSYS`, `ECOS_YOSYS`, `ECOS_OSS_CAD_BIN` |
| RISC-V toolchain | `RISCV`, `RISCV_TOOLCHAIN`, `RISCV_PREFIX` | any of them to something else |

So a Verilator named with `ECOS_VERILATOR` runs with its own data directory,
not with the `VERILATOR_ROOT` of the managed one. A variable that names the
managed installation itself, also through a symbolic link, counts as the
managed tool. The managed tools' `bin` directories stay on `PATH`, which is
only consulted when nothing names a tool.

## Project Config Contract

`ecc-fe init <name>` creates a project-level `ecc-fe.toml`, a `runs/` container,
and an unexecuted `.ecc-fe/template` workspace with schema version 1. A project
run is cloned from that template and stores all mutable state under
`runs/<run-id>`. Bare commands use the current directory as the project.

`ecc-fe init --workspace <path>` retains the M3 direct-workspace layout and
places `ecc-fe.toml` beside `home/`. Existing workspaces without the file
continue to work. Read-only commands do not migrate them; the first successful
direct-workspace `param set` creates the file from current workspace state.

The tables have distinct ownership:

| Table | Meaning |
| --- | --- |
| `[design]` | Creation-time project identity snapshot. Not read back. |
| `[frontend]` | Creation-time catalog selection snapshot. Not read back. |
| `[flow].run` | Default run id when `--run-id` is omitted. |
| `[defaults]` | Typed baseline captured from `home/parameters.json`. |
| `[params]` | Project mode only: overrides applied when a run is created. |

`[frontend]` records the catalog ids. It records `cpu_filelist` or
`soc_filelist` only for a file the user supplied: a filelist that ships with
ECC-FE or an installed resource follows from the ids, and its location on one
machine (including a `latest` resource directory) is not written. The inputs a
run actually reads are in `.ecc-fe/template`.

`param set` and `param unset` edit `[params]` entry by entry: comments and
entries whose value does not change are kept as written. The result is parsed
before it is written, and it must be the same document with only `[params]`
changed. When that cannot be achieved (for example `params` written as an
inline table) the command fails and the file is left untouched.

### Design Kinds

A project is one of two kinds, fixed when it is created and recorded as
`[design].kind`:

| Kind | Created with | Flow |
| --- | --- | --- |
| `cpu` | `init <name>` (a catalog CPU), `--rtl <file>` or `--cpu-filelist <file>` (a custom CPU) | prepare, review, elab, lint, sim on the SoC harness |
| `rtl` | `init <name> --top <module> --filelist <file>`, or `--kind rtl --rtl <file>...` | prepare, review, elab, lint; plus sim when the project names a testbench |

`--kind` defaults to `rtl` when `--filelist` is given and to `cpu` otherwise.
`--rtl` is repeatable for an RTL design. The sources of an RTL design are
used where they are, not copied, so the next run sees every edit.

Invocation mistakes exit with status 2 before anything is created:
`invalid_kind`, `init_option_conflict` (CPU options with an RTL design,
`--filelist` with a CPU, several `--rtl` for a CPU), and
`init_input_required` (an RTL design with neither or both of `--rtl` and
`--filelist`). For an RTL design `init` also checks, before creating
anything, that the filelist and every listed file exist and that the top
module is defined exactly once; a failure is `init_failed` with `init_issue`
records (`filelist_not_found`, `rtl_file_not_found`, `no_rtl_sources`,
`top_module_not_found`, `top_module_count_mismatch`).

Commands follow the flow of the design:

- `check` and `doctor` ask only for the tools the flow's steps use. An RTL
  design needs no RISC-V toolchain, and no C++ compiler or Make unless it has
  a testbench.
- `param list` and `param diff` leave out parameters of steps the flow does
  not have, `ecc-fe.toml` does not record defaults for them, and `param show`
  and `param set` refuse them with `parameter_not_applicable` (exit status 2).
- `run --only/--from/--to <step>` with a step the flow does not have fails
  with `step_not_in_flow` (exit status 2) and lists the steps it has. When
  the run lacks the step only because it was created before the project's
  settings added it, `reason` says so and `new_run_cmd` creates a run that
  has it.

### Simulating An RTL Design

An RTL design is simulated with a SystemVerilog testbench of its own. The
`testbench.*` parameters describe it:

| Parameter | Meaning |
| --- | --- |
| `testbench.top` | Top module of the testbench. Setting it adds `sim` to the flow. |
| `testbench.files` | The testbench sources. They are compiled after the design and are not part of it: they are not reviewed, linted, or handed off. |
| `testbench.pass_pattern` | Regular expression the output must match for the run to pass. |
| `testbench.fail_pattern` | Regular expression that fails the run when the output matches. Not set: the default rule below. `none`: no fail rule. |
| `testbench.plusargs` | Arguments for the simulation, such as `+seed=1`. |
| `testbench.extra_args` | Additional Verilator arguments for the build. |
| `testbench.coverage` | Collect line and toggle coverage of the design (default `false`). See Coverage. |

`init --kind rtl ... --testbench <file>... --testbench-top <module>` sets the
first two; both options are required together (`init_input_required`), the
files must exist and define the module (`init_issue` records
`testbench_file_not_found`, `testbench_top_not_found`), and a CPU project
refuses them (`init_option_conflict`). A project created without a testbench
gains the step when `testbench.top` is set later; like every parameter of a
project, that applies to runs created afterwards.

The step builds the design and the testbench into one program with
`verilator --binary --timing --trace`, with the testbench top as the top
module, and runs it once in the step's `output/` directory. The run passes
when all of these hold:

1. the program exits with status 0 before the simulation time limit
   (`ECOS_FE_TIMEOUT_SIM_CASE`); `$fatal` and a failed assertion make
   Verilator exit non-zero, a testbench that never calls `$finish` runs into
   the limit;
2. the output does not match the fail rule: `testbench.fail_pattern`, or the
   default rule when the project sets none;
3. the output matches `testbench.pass_pattern`, when one is set.

Patterns are Python regular expressions searched in the whole output, with
`^` and `$` matching at line boundaries. There is no default pass pattern.
When the fail rule is switched off and no pass pattern is set, the log ends
with a line saying that the run was judged by its exit status alone.

The default fail rule is
`^\s*(?:\[\s*)?(?:TEST\s+)?(?:FAIL(?:ED)?|ERROR)\b(?!\s*[:=]?\s*0\b)|^%Error`,
applied per line: a line that opens with `FAIL`, `FAILED` or `ERROR` in
capitals, optionally after `TEST ` or an opening bracket, unless a count of
zero follows (`FAILED: 0`); or an error Verilator itself reports. It is a
rule of the step, not the parameter's default value: `testbench.fail_pattern`
stays empty in a project that sets none, which keeps projects that recorded
an empty value valid. `none` (any case) switches a pattern off.

A case judged by a fail rule has `fail_pattern_source`, `default` or
`project`. A failure by the default rule says so in `failure.message` and
carries `failure.hint`, which the step log repeats.

`report/cases.json` records the run as one case (`kind` `testbench`) with
`ok`, `returncode`, `judged_by` and, on failure, `failure.kind`
(`settings`, `compile`, `timeout`, `exit_status`, `fail_pattern`,
`pass_pattern_missing`) and `failure.message`. The verdict is also the last
line of the step log. The result is gated but not scored.

`design.parameters` reach the simulation through the testbench. The
simulated top module is the testbench, and a tool can override only the
parameters of the top module, so an override `NAME=value` is passed (`-G`)
when the testbench module declares a `parameter NAME`; the testbench is
expected to pass it on to the design. An override the testbench does not
declare is not applied. Both lists are in the case as
`design_parameters.applied` / `design_parameters.not_applied` and are named
in the last lines of the step log.

A header listed in `testbench.files` (`.vh`, `.svh`) is not compiled; its
directory is searched for includes. The files the testbench sources reach
with `` `include ``, directly or through other includes, are tracked with
the sources: editing one makes the result stale. They are looked up beside
the including file, then in the include directories of the design; includes
in every `` `ifdef `` branch are followed.

Limits of this form: one testbench and one run per flow, no test lists. The
`sim.*`
parameters configure the programs a CPU runs and do not apply to an RTL
design, as `testbench.*` do not apply to a CPU.

### Moving A Direct Workspace

A direct workspace keeps no location record. Where it was is read from the
path it stores to its own `home/parameters.json`; when a command finds it
somewhere else, the paths below the old location are rewritten in
`home/*.json` and in the generated filelists in `origin/`, and one line on
stderr says so. A copy is repaired the same way and the original is left as
it is. As for a project, paths outside the workspace are kept and existing
results become stale.

### Moving A Project

A project stores absolute paths. Its location is recorded in
`.ecc-fe/location.json`; when a command finds the project somewhere else, it
rewrites every path below the old location to the new one in the template
and in each run (`home/*.json` and generated filelists in `origin/`), says so
on stderr, and updates the record. Inputs that live inside the project
directory therefore move with it. Inputs outside it keep their absolute
paths. Results of existing runs become stale, because the paths that went
into them changed. A run that is in use is skipped and repaired by a later
command.

When `init` fails it removes what it created, including directories that did
not exist before, and never anything that did. The failure is an
`init_failed` error record followed by one `init_issue` record (`code`,
`message`, `field`) per reason.

Only quoted, flat, known parameter names are accepted in `[defaults]` and
`[params]`. Unknown top-level keys, schema versions, parameter names, invalid
types, non-finite numbers, out-of-range values, and unsupported choices are
rejected.

### Parameter Ownership

Each mode has exactly one source of parameter values.

**Project mode: `ecc-fe.toml`.** `[defaults]` plus `[params]` are the values a
new run is created with; `ecc-fe run --set key=value` adds overrides for that
one run and records them in `home/cli-param-overrides.json`. `param set` and
`param unset` edit only `[params]`. A run's `home/parameters.json` is the
record of what that run was created with: it is never rewritten afterwards, and
`param set --workspace <run directory>` is refused with `run_parameters_fixed`.

A run reports the values it was created with. `config --resolved` on a run and
`param list`, `show`, and `diff` with `--run-id` read that run's
`home/parameters.json`: a value that differs from the baseline has source `cli`
when it was given with `run --set` and `run` otherwise. The project's current
`[params]` is not consulted. Without `--run-id` the `param` commands describe
the next new run, with source `ecc-fe.toml` for values from `[params]`. A run
that does not exist is `run_not_found`.

**Direct-workspace mode: `home/parameters.json`.** This is the file the flow
engine, the desktop application, and the JSON-RPC API read and write, and the
CLI treats it the same way:

- `ecc-fe run --workspace` runs the workspace as it is. It does not compare the
  workspace with `ecc-fe.toml`, does not reset the flow because they differ,
  and does not write values from the TOML back.
- `param set` writes the value into `home/parameters.json`; `param unset`
  writes back the `[defaults]` value. The value is stored first and the flow is
  then reset, so a failed reset cannot lose the change.
- `param list`, `show`, and `diff` report the stored value with source
  `workspace` when it differs from the baseline, whoever wrote it.
- `ecc-fe.toml` beside `home/` only holds the `[defaults]` baseline. `[params]`
  in it is ignored; an entry left by an older ecc-fe is removed the next time
  that parameter is set or unset. A broken file fails `doctor` but does not
  stop a run.

The version 1 parameter namespace is:

```text
design.frequency_mhz
sim.compile_preset
sim.compile_opt_level
sim.compile_march
sim.compile_mabi
sim.compile_extra_cflags
sim.coremark_iterations
sim.coremark_total_data_size
sim.coremark_max_cycles
sim.coremark_has_float
sim.coremark_use_difftest
```

`param list` shows the common parameters; `--all` includes advanced compiler
and CoreMark settings. `param show` exposes the type, bounds, choices, baseline,
effective value, source, and runtime mapping. `param diff` reports explicit
non-default overrides.

Repeated `ecc-fe run --set key=value` options take precedence over `[params]`
for the run being created.

## Project And Run Selection

Project and inspection commands accept `--project`; omission means the current
directory. `--run-id` wins over `[flow].run`, which falls back to `default`.
A run id is a plain name, not a path: 1-64 letters, digits, `.`, `_`, or `-`,
starting with a letter or digit. It always resolves to `runs/<run-id>`. Any
other value, in `--run-id` or in `[flow].run`, is rejected as `invalid_run_id`
(exit status 2) before anything is read or written, so a run can never be
created, replaced, or locked outside the project's `runs/` directory. This is
deliberately stricter than ECC, which also accepts project-relative and
absolute paths. An empty `--run-id` selects the `default` run.

Project mode creates a fresh run and refuses an existing target unless
`--overwrite` is explicit. Replacement is allowed only for an empty directory
or a real ECC-FE workspace. Project roots, the `runs/` container, the internal
template, symlink targets, and redirected paths are never replacement targets.

### Progress While Running

`run` reports each step when it starts and when it finishes, instead of
staying silent until the flow ends.

| Mode | Where | What |
| --- | --- | --- |
| text | stderr | `ecc-fe: lint started`, `ecc-fe: lint success (0.5s)` |
| `--json` | stderr | the same lines; stdout stays one JSON document |
| `--jsonl` | stdout | `progress` records, one per line, before the `step` and `run` records |
| `--plain` | stdout | the same records as `key=value` lines |

A `progress` record has `event` set to `step_started` (`step`, `tool`),
`step_finished` (`step`, `state`, `elapsed_s`, `log`), or `stage` (`step`,
`stage`, `state`). Stage records are written only in `--jsonl` and `--plain`.

`ecc-fe log [step] --follow` prints the end of the log and then whatever is
appended while a command holds the workspace, and returns when the run is over
and the log is read to its end. For a step it follows both the report log and
the step's own log, naming the file when the output switches. `--follow`
writes the log itself, so it cannot be combined with `--json` or `--jsonl`.

### Inputs And Stale Results

A project created by `ecc-fe init` refers to its RTL: `--rtl`, `--cpu-filelist`,
`--soc-filelist`, and catalog cores are recorded as paths, not copied. Editing
the RTL therefore needs no import step. The next `run` compares each finished
step with the current inputs, configuration, and tools, re-executes the first
step that no longer matches and every step after it, and a new run always
reads the current files.

`status` reports this before anything is re-executed. A step whose stored
result no longer matches carries `stale: true` and `stale_reason` while keeping
its stored `state`; the workspace record lists them in `stale_steps` and gives
the `resume_cmd`. `status` never modifies the run and does not fail because of
stale steps.

Two creation forms of `ecc-fe workspace create` copy their input into
`origin/` instead of referring to it: `--origin-verilog`, and for CPU designs
`--filelist` and `--rtl`. Such a workspace keeps using its copy; the path the
copy came from is not recorded.

### Step Selectors

`run` accepts the ECC selectors for a project run and for a direct workspace:

```bash
ecc-fe run --to lint                   # new run, stop after lint
ecc-fe run --resume                    # continue at the first unfinished step
ecc-fe run --resume --to elab
ecc-fe run --from review               # re-execute review and what follows
ecc-fe run --from review --to lint
ecc-fe run --only lint                 # exactly one step
ecc-fe run --only lint --force         # even if it already succeeded
ecc-fe run --workspace path --only lint
```

`--resume`, `--from`, and `--only` are mutually exclusive. `--force` requires
`--only`. `--clean` makes the steps that are run again build everything
from scratch instead of reusing what earlier executions built (for `sim`:
the simulation program, its object directory, and the test programs). It
requires `--only` (and then implies `--force`), `--from`, or `--rerun`;
otherwise it is `clean_requires_rerun`. `--to` names the last step to execute and combines with `--resume`,
`--from`, or no selector, but not with `--only`; a `--to` step before the
`--from` step is `invalid_step_range`. The M3 `--step/--rerun` spelling remains
an alias for `--only/--force`, and full-flow `--rerun` re-executes every step.
Selector mistakes exit with status 2.

In project mode `--resume`, `--from`, `--only`, and `--rerun` continue an
existing run in place and fail with `run_not_found` when the run does not
exist. A run keeps the parameters it was created with: project `[params]`
changed afterwards are not applied to it, and `--set` and `--overwrite` cannot
be combined with these selectors. Running without them still creates a new run
and reports `run_exists`, now with the `--resume` command, when the run is
already there.

`ecc-fe workspace run-flow` accepts `--from` and `--to` with the same meaning.
Project creation controls (`--run-id`, `--overwrite`, and `--set`) cannot be
combined with `--workspace`.

## Catalog Contract

`catalog list`, `show`, `check`, and `validate` are read-only adapters over the
same catalog implementation used by the desktop and JSON-RPC API. Supported
list kinds are `core`, `soc`, `toolchain`, and `test-suite`. Contract checking
and configuration validation preserve the catalog implementation's pass/fail
result and issue records; they never run a hardware flow.

## Report Contract

`report qor` combines the persisted flow state and `frontend_detail.json` with
each step's official `analysis/qor_metrics.json`, `qor_summary.json`, and
`qor_hotspots.json` triplet. The artifact schema versions and shared generation
token are validated before the payloads are exposed unchanged. The command
preserves frontend readiness/quality scores, gates, metric provenance, and
hotspots, but does not synthesize an undefined cross-step total or backend
physical-design area, timing, and power metrics. `--output` writes the aggregate
as JSON.

How state, quality and score relate, and how each score is put together, is
specified in [quality-scoring.md](quality-scoring.md).

`report files` recursively lists regular, non-symlink files in the selected
step `report/` and `analysis/` directories. Each record includes its section,
absolute path, workspace-relative path, byte size, format, and a shell-safe
inspection command.

`report compare <base> <run>` compares two runs of one project. It writes a
`qor_compare` summary, one `qor_step` record per step, and one `qor_metric`
record per metric whose value differs (`--all` includes the unchanged ones).

- A metric is `improved` or `regressed` according to its `direction`
  (`higher_is_better`, `lower_is_better`); a `trend_only` metric or a
  non-numeric value is only ever `changed`.
- A step whose state is worse than in the base run counts as a regression.
- `same_inputs` on a step is `false` when the two results were produced from
  different inputs or settings, so their numbers measure different things.
- `parameter_changes` in the summary lists every stored parameter that
  differs, as `[base, run]`.
- The summary `status` is `regressed` if anything regressed, otherwise
  `improved`, `changed`, or `same`.

The command exits `0` when the comparison was made. With
`--fail-on-regression` it exits `1` when `regressed` is not zero. A run that
does not exist is `run_not_found`.

### Coverage

`sim.coverage` (CPU) and `testbench.coverage` (RTL design), both `false` by
default, build the simulation with Verilator line and toggle coverage
(`--coverage-line --coverage-toggle`) and collect what the run covers. They
change what the step produces, so they are part of its signature.

Every case writes a coverage data file; the files are merged by adding the
counts of equal points, and a point is covered when its count is above zero.
After a run the step holds:

| File | Content |
| --- | --- |
| `sim_verilator/report/coverage.json` | The report (below). |
| `sim_verilator/output/coverage.dat` | The merged data, in Verilator's format; `verilator_coverage --annotate <dir>` reads it. |

The data of the single cases is removed once merged. `coverage.json`
(`schema_version` 1) has `status`, `cases`, `cases_with_data`, `data`, and
three views of the same counts, each as `{covered, total, percent}` per
kind: `kinds` (everything), `by_ownership` (by the owner of the source file:
`cpu`, `design`, `soc`, `adapter`, `third_party`, ...) and `files` (per
source file, with its `ownership`). The kinds are `line` and `toggle`, and
`branch`, which Verilator counts as part of line coverage. `percent` is
`null` where there is nothing to cover. A testbench's own files are left
out. `report/cases.json` carries the totals as `coverage` (`status`,
`report`, `kinds`, `data`).

`status` is `ok`, `empty` (the simulation wrote no data) or `unavailable`
with a `reason`. A CPU is simulated by the C++ harness of its SoC, and the
harness has to write the data out; one that does not is `unavailable` and
the simulation result itself is unaffected.

`report coverage [--ownership <owner>] [--limit N]` writes a `coverage`
record (the report's totals, or those of one owner, `files`, `shown`,
`report`, `data`, `annotate_cmd`) and one `coverage_file` record per file,
least covered first (default 20). `coverage_not_found` when the run
collected none; `unknown_ownership` and `invalid_limit` exit with status 2.

Coverage of the design under test is part of the simulation score of a CPU
and appears in `report compare` as the `line_coverage` and `toggle_coverage`
metrics; see [quality-scoring.md](quality-scoring.md). It has no gate.

### Failure Report

`report failure [--step <step>] [--limit N]` says why a step is not a
success. `doctor` checks the environment; this explains a step whose tools
ran. Without `--step` it takes the first failed step of the run. It reads
the step's `report/frontend_detail.json` and runs nothing; a log is searched
for its first error line only when the results name no cause. The command
exits `0` when it produced its report, as `status` does.

It writes a `failure` record: `failed`, `step`, `tool`, `state`, `reason`
(one sentence), `causes` (how many were found), `shown`, `advice`, `detail`
(the file read), `log_cmd`, and for a failed step `rerun_cmd`; `more_cmd`
when the list was cut at `--limit` (default 10). Then one `failure_cause`
record per cause, in order of importance:

| `category` | From | Fields beside `message` |
| --- | --- | --- |
| `input` | a prepare input contract that is not usable | `detail` |
| `tool` | what Yosys reported when the review precheck failed | `tool` |
| `review_issue` | an unwaived review issue of severity `error` | `rule`, `source`, `line`, `column`, `advice`, `ownership` |
| `elab_error` | a slang error, or a top module that was not found | `source`, `line`, `column` |
| `unresolved_module` | a module instantiated but not defined | `module` |
| `lint_error` | a Verilator error | `rule`, `source`, `line`, `column`, `ownership` |
| `baseline` | a lint baseline that cannot be read | `source` |
| `lint_new_warning` | a warning of the user's that the lint baseline does not know | `rule`, `source`, `line`, `column` |
| `sim_case` | a case that did not pass | `case`, `failure`, `evidence`, `log`, `wave`, `newly_failing` |
| `sim_build` | a testbench or test program that did not build | `case`, `failure`, `log` |
| `gate` | a failed QoR gate, with the measured and required values | `gate` |
| `log` | the first error line of the step's log, as a last resort | `log` |

A step that did not fail yields a `failure` record with `failed=false` and a
reason naming its state; a run without a failed step yields one with
`step=null` and the state of every step. `status` and a failed `run` carry
`failure_cmd`, the command that prints this report.

| Error | When |
| --- | --- |
| `unknown_step` | `--step` is not a flow step; exit status `2`. |
| `invalid_limit` | `--limit` is below 1; exit status `2`. |
| `step_not_in_flow` | The step is not part of this run's flow. |

### CI Export

`report export --format sarif|junit [-o file]` writes a result the flow has
already produced in a format CI systems read. Nothing is run again, and the
command exits `0` whatever the result says; the verdict of the flow is the
exit status of `run`.

| Format | Made from | Default file |
| --- | --- | --- |
| `sarif` | the diagnostics of `lint` (`lint_summary.json`) | `lint.sarif` |
| `junit` | the cases of `sim` (`cases.json`) | `sim.junit.xml` |

It writes one `report_export` record: `format`, `step`, `state` (of that
step), `path`, `bytes`, and for SARIF `results`, `errors`, `warnings`,
`left_out`, `base_dir`; for JUnit `tests`, `failures`, `errors`.

SARIF is version 2.1.0 with one run whose tool is Verilator. Each diagnostic
is a result with `ruleId`, `level` (`error` or `warning`), the message, and a
location with line and column when they are known. A file below `--base-dir`
(default: the current directory) is written relative to it under the base id
`SRCROOT`; any other file keeps an absolute `file:` URI. By default the log
carries the diagnostics in the user's own sources and every error; `--all`
adds warnings in sources the user does not own, and `left_out` counts the
ones not carried. With a lint baseline, `baselineState` is `unchanged` for a
known warning and `new` otherwise.

JUnit has one `testsuite` named after the suite and one `testcase` per case,
with `classname` `<design>.<suite>`. A case that did not pass has a `failure`
whose `type` and `message` are the failure kind and message of the case and
whose text is the first error and the end of its log. When the step failed
and no case did, for example because a build failed before any case ran, one
`testcase` named `sim` carries an `error`, so that a report without cases is
not read as a pass. Durations are not recorded per case and are not written.

| Error | When |
| --- | --- |
| `unknown_export_format` | `--format` is not `sarif` or `junit`; exit status `2`. |
| `lint_result_not_found` / `sim_result_not_found` | The step has no report to export; `remediation_cmd` runs the flow up to it. |

## Lint And Elaboration Settings

Tool behaviour is set through parameters, so it is recorded with each run
and a change makes the affected step stale.

| Parameter | Effect |
| --- | --- |
| `lint.profile` | `all` (default): `-Wall`, every lint and style warning. `basic`: Verilator's default warnings. |
| `lint.rules_off` | Rules turned off, e.g. `["UNUSEDSIGNAL"]`; each becomes `-Wno-<RULE>`. |
| `lint.waiver_files` | Verilator configuration (`.vlt`) files passed to lint. |
| `lint.baseline` | Baseline file for the gate described below. |
| `lint.extra_args` | Further Verilator arguments, appended as given. |
| `elab.extra_args` | Further slang arguments, appended as given. |
| `design.parameters` | Top-level parameter overrides, e.g. `["WIDTH=16"]`; `-G` for both tools. `sim` of an RTL design passes the ones its testbench declares. |

Paths in `lint.waiver_files` and `lint.baseline` are resolved against the
project directory (the workspace directory for a direct workspace). The
content of those files is part of the lint result's provenance: editing a
waiver file makes lint stale. A setting that cannot be used (an unknown
profile, a rule name that is not one, a missing waiver file) fails the step
and is named in its log.

Library directories and suffixes from the filelist (`-y dir`, `+libext+`)
are passed to review, elab, lint, and the simulation build.

### Baseline Gate

`ecc-fe report lint-baseline [-o file]` writes the warnings of a run's lint
result to a file (default `lint-baseline.json`) and prints the `param set`
command that enables it. With `lint.baseline` set:

- every warning is marked `baseline=known` or `baseline=new` in
  `lint_summary.json`; the summary carries `baseline` (`known`, `new`,
  `new_actionable`, `fixed`, `gate`);
- lint fails when a warning in code the user owns is not covered by the
  baseline (`gate=failed`); new warnings in other code are reported only;
- a baseline that cannot be read fails the gate.

A warning is identified by rule, file name, and message, counted: line
numbers and directories are not part of it, so moving code or checking the
design out elsewhere does not turn known warnings into new ones, and one
more occurrence of a known warning is new. Without a baseline the gate is
`off` and warnings never fail lint, as before.

Verilator's closing lines (`Exiting due to N error(s)`, `Command Failed`)
are not counted as errors.

`check` and `doctor` require Verilator 5.0 or newer; an older one is
reported as `error=tool_too_old` with `minimum_version`.

## Handoff

`ecc-fe handoff [--run-id <id>] [--output <dir>] [--bundle]
[--allow-incomplete] [--no-check] [--sdc <file>]` writes the handoff manifest of a run: the
user's design with everything needed to compile it, and what the flow
concluded. It is the only interface a consumer such as ECC uses; its content
and readiness rules are specified in
[handoff-manifest.md](handoff-manifest.md).

## Flow Description

`flow list` and `flow show <step>` are read-only views of the step registry
and of the flow profile a design's parameters select. They report what the
flow is made of, not what it has done (`status`). They describe the same
parameters as `param list`: a direct workspace, the run named by `--run-id`,
or otherwise the next new run of the project: the template's values with the
project's `[params]` applied.

`flow list` writes a `flow` record (`design_kind`, `describes` =
`workspace` | `run` | `next_run`, `project`, `run`, `workspace`, `steps` in
order, `capabilities`) and one `flow_step` record per registered step:
`index` (position in this flow, from 0), `step`, `tool`, `directory`,
`in_flow`. A registered step this design does not run has `in_flow=false`,
`index=null` and a `reason`.

`flow show <step>` writes one `flow_step` record with, in addition: `after`
and `before` (neighbours in this flow), `inputs_from` (the step that prepared
the inputs it reads), `programs`, `environment_checks` (what `doctor --step`
checks), `parameters` (the parameters of this design that apply to the
step), `qor_report`, `handler`, and `stale_when`, the things whose change
makes a successful result out of date:

| Field | Meaning |
| --- | --- |
| `inputs` | The files the step reads. |
| `parameters` | Parameters tracked for the step. |
| `parameters_not_tracked` | Parameters that change how the step runs, not what it produces. |
| `design_settings` | Tracked values fixed by the design or its catalog entry. |
| `programs` | External programs whose identity is tracked. |
| `sources` | ECC-FE source files hashed into the step signature. |

A step in the flow also carries `run_cmd`, `params_cmd` and `doctor_cmd`. An
unknown step is `unknown_step` with exit status `2`; a missing project or run
fails as it does for `param list`.

## Run Management

`runs list` writes a `run_list` summary (`project`, `count`, `selected`) and
one `run` record per directory under `runs/` whose name is a valid run id:
`state` (as in `status`), `failed_step`, `steps_done`, `steps_total`,
`modified` (UTC time the flow state last changed), `overrides` (the `run
--set` values the run was created with), and `selected` for the run that
commands use when `--run-id` is omitted. A run whose state files cannot be
read is listed with `state=unreadable`; the command does not fail.

`runs delete <run-id>...` removes runs. Each id yields a `run_deleted` record
or an error record, and the command exits `1` if any id failed:

| Error | When |
| --- | --- |
| `invalid_run_id` | Not a plain run name; exit status `2`, nothing is deleted. |
| `run_not_found` | No such run. |
| `workspace_busy` | A command is using the run. |
| `run_path_refused` | The entry is a symlink or is not a run directory. |

A run is renamed out of `runs/` before its files are removed, so an
interrupted delete never leaves a partial run under its name. The project
template and `ecc-fe.toml` are never touched.

Both commands need a project; elsewhere they fail with `project_not_found`.

Lock files: a run's `.<run-id>.lock` is removed with the run. A command aimed
at a run or workspace that does not exist removes the lock file it created,
and `runs delete` sweeps lock files whose run is gone.

## Compatibility Rules

- `workspace` keeps its existing ECOS Studio JSON envelope.
- JSON-RPC protocol and stdout framing do not change.
- Workspace files under `home/` do not change.
- `ecc-fe.toml` is additive and optional for legacy workspaces.
- The direct `--design/--top` form is removed; see Command Shape.
- New commands are adapters over existing Python application and flow APIs.
- M3 `--workspace`, `--step`, and `--rerun` invocations remain valid.
