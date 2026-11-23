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
ecc-fe resource list|status|install|update|env
ecc-fe param list|show|set|unset|diff
ecc-fe catalog list|show|check|validate
ecc-fe report qor|files
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
`ecc-fe version --json` follows ECC's version exception and emits the schema
object directly; its `--jsonl` and `--plain` forms remain record-oriented.

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | Command completed; optional checks may still need attention. |
| `1` | Business, validation, resource, or tool failure. |
| `2` | Invalid command-line usage. |
| `130` | User cancellation. |

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
workspace record then carries `resume_cmd` and the command exits with status
`1`. `status` never rewrites the stored state.

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
Explicit environment variables take precedence over managed resources. Healthy
managed resources take precedence over tools found later on `PATH`.

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
| `[design]` | Creation-time project identity snapshot. |
| `[frontend]` | Creation-time catalog selection snapshot. |
| `[flow].run` | Default run id when `--run-id` is omitted. |
| `[defaults]` | Typed baseline captured from `home/parameters.json`. |
| `[params]` | Project mode only: overrides applied when a run is created. |

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
`--only`. `--to` names the last step to execute and combines with `--resume`,
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

`report files` recursively lists regular, non-symlink files in the selected
step `report/` and `analysis/` directories. Each record includes its section,
absolute path, workspace-relative path, byte size, format, and a shell-safe
inspection command.

## Compatibility Rules

- `workspace` keeps its existing ECOS Studio JSON envelope.
- JSON-RPC protocol and stdout framing do not change.
- Workspace files under `home/` do not change.
- `ecc-fe.toml` is additive and optional for legacy workspaces.
- The direct `--design/--top` form is removed; see Command Shape.
- New commands are adapters over existing Python application and flow APIs.
- M3 `--workspace`, `--step`, and `--rerun` invocations remain valid.
