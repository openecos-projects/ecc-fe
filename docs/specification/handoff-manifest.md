# ECC-FE Handoff Manifest

The handoff manifest is the contract between ECC-FE and whatever takes a
design next, such as ECC. A consumer reads this file and nothing else of an
ECC-FE project: not `ecc-fe.toml`, not `runs/`, not `merged_rtl.f`.

`ecc-fe handoff` writes it for one run:

```bash
ecc-fe handoff                         # handoff/<run-id>/handoff.json, RTL by reference
ecc-fe handoff --bundle -o ./export    # self-contained directory
ecc-fe handoff --run-id fast --allow-incomplete
```

## Output Directory

| File | Content |
| --- | --- |
| `handoff.json` | The manifest described below. |
| `design.f` | The same design as a filelist: `+incdir+`, `+define+`, `-y`, `+libext+`, then the sources. |
| `standalone_check.log` | Output of the standalone elaboration, when it ran. |
| `rtl/` | Only with `--bundle`: a copy of the sources and headers. |

The default directory is `handoff/<run-id>` in the project (`handoff/` in a
direct workspace); `--output` chooses another.

## What Is Handed Off

Only the user's design.

- An RTL project hands off everything its filelist names.
- A CPU project hands off the CPU: the content of the CPU filelist, with the
  CPU top as `design.top`. The SoC harness the CPU is simulated in is a
  simulation environment; it is counted under `excluded` and not listed.

The design is read from the filelist it was created with, using the same
parser as the flow. Three things the frontend does implicitly are written out
so that a consumer needs no knowledge of them:

- the directory of every source and every listed header is an include
  directory, in addition to the `+incdir+` entries;
- headers are found by following `` `include `` from the sources through the
  include directories, whether or not the filelist names them;
- library directories and suffixes (`-y`, `+libext+`).

## Readiness

By default a manifest is written only when every step of the run succeeded
and none is stale, and when the design elaborates on its own:

| Error | Meaning |
| --- | --- |
| `handoff_not_ready` | A step failed, has not run, or is stale; `blockers` lists them. |
| `handoff_design_incomplete` | The design as described does not elaborate standalone; see `standalone_check.log`. |
| `handoff_inputs_unavailable` | The recorded filelist or a file it lists is missing. |

`--allow-incomplete` writes the manifest anyway, with `flow.status` set to
`incomplete` and the reasons in `flow.blockers`. A consumer must check
`flow.status`.

The standalone check (`--check`, on by default) runs slang on exactly what
the manifest lists: the files, include and library directories, defines and
parameter overrides, plus the frontend conventions below, with the design's
own top. Inside the flow a CPU is only ever elaborated together with its
harness; this is what shows that it also stands alone. It is `skipped` when
slang is not installed or with `--no-check`.

## Schema (version 1)

Top-level fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1`. A consumer must refuse a version it does not know. |
| `kind` | string | `ecc-fe-handoff`. |
| `generator` | object | `name` (`ecc-fe`) and `version`. |
| `created_at` | string | UTC, ISO 8601. |
| `source` | object | `project`, `run`, `workspace` the manifest was made from. Informational. |
| `design` | object | See below. |
| `constraints` | object | `sdc`: `null`, or `path` and `sha256` of the timing constraints handed off with the design. See below. Absent in manifests written before it was added. |
| `rtl` | object | See below. |
| `excluded` | object | What was left out and why; `soc_harness` for a CPU project. |
| `standalone_check` | object | `status` (`pass`, `fail`, `skipped`), and `tool`, `errors`, `log`, `defines` or `reason`. |
| `flow` | object | See below. |
| `tools` | object | Per tool used by the flow: `path` and `version`. |
| `conventions` | object | `frontend_defines` (`["SYNTHESIS"]`) and `default_timescale` (`1ns/1ps`): what the frontend added when it compiled the design. |

`design`:

| Field | Meaning |
| --- | --- |
| `name` | Design name. |
| `kind` | `rtl` or `cpu`. |
| `top` | Top module of what is handed off. |
| `clock_port` | Clock input of `top`, or `null` when it is not known. |
| `clock_port_source` | `parameter` (set for the project), `detected` (the only clock-named input of `top`), or `null`. |
| `frequency_mhz` | Target clock frequency. |
| `parameters` | Top-level parameter overrides, `NAME=value`. |
| `sdc` | Always `null` in version 1. Constraints are described by the top-level `constraints`. |

`constraints`:

The flow does not read timing constraints; it passes a file on. The file is
the one given to `handoff --sdc <file>`, otherwise the run's `design.sdc`
parameter (a path relative to the project is resolved from it). `sdc.path` is
absolute, or `constraints/<file name>` beside the manifest in a bundle.
`handoff` fails with `handoff_sdc_not_found` when the file named does not
exist. Nothing checks that the constraints agree with `design.clock_port` or
`design.frequency_mhz`; that is for the consumer to decide.

`rtl`:

| Field | Meaning |
| --- | --- |
| `bundled` | `true` when the files are in `rtl/` beside the manifest. |
| `root` | `rtl` when bundled, otherwise `null`. |
| `files` | Compilation units in order: `path`, `sha256`. |
| `headers` | Headers the design reads: `path`, `sha256`. |
| `incdirs`, `libdirs`, `libexts`, `defines` | As passed to the tools. |
| `filelist` | `design.f`. |
| `unresolved_includes` | `` `include `` names that could not be found: `include`, `from`. |
| `not_passed_on` | Filelist entries ECC-FE does not act on (tool options, other file types). |
| `source_filelist` | The filelist the design came from. Informational. |

Paths are absolute when `bundled` is `false`, and relative to the directory of
the manifest when it is `true`. A bundle keeps the directory layout below the
common directory of all inputs, so equally named files do not collide and
relative includes keep working.

`flow`:

| Field | Meaning |
| --- | --- |
| `status` | `complete` or `incomplete`. |
| `state` | The flow state as `ecc-fe status` reports it. |
| `blockers` | Why it is incomplete: `step`, `reason`. |
| `steps` | Per step: `step`, `tool`, `state`, `stale`, `stale_reason`, `quality`, `score`, `signature`, `input_fingerprint`, `finished_at`. |
| `lint` | `status`, `errors`, `warnings`, `actionable_diagnostics`, `baseline_gate`, `new_warnings`; `null` without a lint result. |

`quality` and `score` are reported, not enforced: a step can succeed with
`quality` `blocked`. For a CPU project the lint counts cover the CPU together
with its harness, because that is what was linted.

## Compatibility

- Fields may be added within a schema version; a consumer ignores fields it
  does not know.
- Removing a field, or changing the meaning or type of one, raises
  `schema_version`.
