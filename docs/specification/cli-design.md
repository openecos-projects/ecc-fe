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

The legacy direct form remains supported:

```bash
ecc-fe --design demo --top top --rtl demo.v
```

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
| `[params]` | Explicit CLI overrides applied before a flow run. |

Only quoted, flat, known parameter names are accepted in `[defaults]` and
`[params]`. Unknown top-level keys, schema versions, parameter names, invalid
types, non-finite numbers, out-of-range values, and unsupported choices fail
before flow execution. `param set` and `param unset` update only `[params]` and
preserve the rest of the TOML file.

`home/parameters.json` remains the runtime source consumed by the existing flow
engine, desktop integration, and JSON-RPC API. On `param set`, or before `run`
after a manual TOML edit, explicit overrides are synchronized atomically into
that file. A semantic change resets the frontend flow so stale results cannot
be reused. `param unset` restores the captured baseline.

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

In project mode, `param set/unset` changes defaults for future runs without
mutating completed runs. Repeated `ecc-fe run --set key=value` options take
precedence for one run and are recorded in
`home/cli-param-overrides.json`. Direct-workspace parameter mutations preserve
the M3 behavior: update `home/parameters.json` and reset stale flow state.

## Project And Run Selection

Project and inspection commands accept `--project`; omission means the current
directory. `--run-id` wins over `[flow].run`, which falls back to `default`.
A bare run id resolves below `runs/`; project-relative and absolute paths use
the same resolution rules as ECC.

Project mode creates a fresh run and refuses an existing target unless
`--overwrite` is explicit. Replacement is allowed only for an empty directory
or a real ECC-FE workspace. Project roots, the `runs/` container, the internal
template, symlink targets, and redirected paths are never replacement targets.

Existing workspaces use `--workspace` and support the ECC selectors:

```bash
ecc-fe run --workspace path --resume
ecc-fe run --workspace path --from review
ecc-fe run --workspace path --only lint
ecc-fe run --workspace path --only lint --force
```

`--resume`, `--from`, and `--only` are mutually exclusive. `--force` requires
`--only`. The M3 `--step/--rerun` spelling remains an alias for
`--only/--force`; full-flow `--rerun` remains supported for direct workspaces.
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
- Existing `--design/--top` invocations continue through the argparse runner.
- New commands are adapters over existing Python application and flow APIs.
- M3 `--workspace`, `--step`, and `--rerun` invocations remain valid.
