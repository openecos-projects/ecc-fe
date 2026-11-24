# ECC-FE Step Quality And Scores

Every step that finishes is described three ways, in
`<step>/analysis/qor_summary.json` and through `ecc-fe report qor`:

| | Answers | Values |
| --- | --- | --- |
| state | Did the step run to the end? | `success`, `incomplete`, ... |
| quality | Did the result pass its gates? | `pass`, `blocked`, `incomplete` |
| score | How good is the result, on a scale of 100? | a number, or absent |

A gate is a hard condition, such as "no elaboration errors". Quality is
`blocked` when a gate failed, `incomplete` when a gate could not be decided
(for example the Yosys precheck did not run) or the step did not succeed, and
`pass` otherwise. The score never changes the quality.

This document describes scoring version 2 (`score.scoring_version`).

## Rules

The same three rules hold for every step.

1. **Only what was evaluated counts.** A component that does not apply to
   the design, or whose tool did not run, is marked `"evaluated": false`. It
   earns nothing and costs nothing: the score is
   `100 * earned / possible` over the evaluated components, and
   `score.evaluated_weight` says how much of the 100 that was. With nothing
   evaluated there is no score.
2. **Warnings are judged by density.** Warning counts are divided by the size
   of the RTL in thousands of lines. Credit falls linearly from full at zero
   warnings to none at the step's tolerance. A design under 200 lines is
   scored as 200 lines, so one warning in a tiny file is not a high density.
   Errors are counted, not normalised: each one costs a fixed amount.
3. **A score describes one run.** It does not depend on earlier runs. How a
   simulation compares with the previous one is reported under `regression`
   in `cases.json`; comparing two runs is the job of `ecc-fe report compare`.

Each component in `score.components` has `id`, `label`, `earned`,
`possible`, `evaluated` and a `summary` that states the numbers it used.

## Policy

The weights and tolerances are data: `fecompiler/analysis/scoring_policy.json`.
Changing it changes the scores, and it is part of every step's signature, so
existing results become stale.

| Step | Component | Weight | How it is earned |
| --- | --- | --- | --- |
| prepare | Source resolution | 30 | share of RTL files (20) and include directories (10) that resolve |
| | Top resolution | 20 | defined exactly once (15), its file is an input (5); not evaluated when no top is required |
| | Interface contract | 40 | share of required ports matched (35), no unexpected port (5); not evaluated without a contract |
| | Reproducibility | 10 | input fingerprint (5), merged filelist (2.5), manifest (2.5) |
| review | Structural precheck | 30 | Yosys precheck passed; not evaluated when it did not run |
| | Actionable errors | 30 | 10 off per error |
| | Warning hygiene | 15 | density, none at 5 warnings per 1000 lines |
| | Structural headroom | 25 | fanout, fanin and depth against their targets; not evaluated without a passing precheck |
| elab | Compiler execution | 25 | slang completed a full elaboration |
| | Diagnostic errors | 30 | 10 off per error |
| | Hierarchy closure | 20 | 5 off per unresolved module |
| | Top resolution | 15 | the top module was found |
| | Warning hygiene | 10 | density over all RTL, none at 5 per 1000 lines |
| lint | Analysis execution | 25 | Verilator completed |
| | Errors | 40 | 20 off per actionable error |
| | Warnings | 25 | density over the user's RTL, none at 20 per 1000 lines |
| | Rule breadth | 10 | 2 off per distinct rule |
| sim (CPU) | Required cases | 60 | share of cases that passed |
| | Difftest confidence | 25 | share of Difftest cases that matched; not evaluated when Difftest is off |
| | Cycle telemetry | 15 | share of cases that passed and reported a cycle count |

The simulation of an RTL design with its own testbench is gated and not
scored.

Review and lint judge the RTL the user owns. A core bundled with ECC-FE is
third-party code: its user owns none of it, so its lint warnings do not count
against them.

## Structural Measurements

The review step measures fanout, fanin and combinational depth on the Yosys
netlist.

- Clock and reset pins of registers and memories are not counted as fanout.
  Counting them made the number of registers the fanout of every module.
- An instance of another module is a boundary. A path into it and out again
  is not a combinational path of the module that instantiates it.
- "Combinational cycle candidate" is a warning. The graph is built from
  whole-word cells, so bits of one bus that feed other bits of it look like a
  loop. A real loop is found by Yosys `check` and reported as the error
  "Combinational loop reported by Yosys precheck".

## What Changed From Version 1

| Version 1 | Version 2 |
| --- | --- |
| A design without an interface contract was given the 40 interface points; a CPU without a known contract got 0 of 40. | Not evaluated in both cases. |
| Without Yosys a review could reach at most 45. | Precheck and headroom are not evaluated; the quality is `incomplete`. |
| Lint took 2.5 points per warning, review 3, elaboration 2, whatever the size. | Density per 1000 lines. |
| The first passing simulation scored lower than an identical second one; a failure repeated twice counted as stable. | No history in the score. |
| Without Difftest, half of its points were given. | Not evaluated. |
| The clock was the highest-fanout net of most modules. | Clock and reset pins are not counted. |
| A wrapper around a submodule was reported as a combinational loop (error). | Instances are boundaries; a word-level candidate is a warning. |
| "Input fingerprint recorded" was always true. | True only when a fingerprint was recorded. |

Scores of the two versions are not comparable. `report compare` still shows
both scores of a step but gives no `score_delta` when their versions differ.

## Not Covered

- The pass rate does not consider how many cases were run: one passing case
  earns the full case weight.
- A baseline chosen by the user for regression comparison does not exist.
- The policy cannot be overridden per project.
