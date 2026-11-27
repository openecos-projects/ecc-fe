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

This document describes scoring version 4 (`score.scoring_version`).

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
| review | Structural precheck | 40 | Yosys precheck passed; not evaluated when it did not run |
| | Actionable errors | 40 | 10 off per error |
| | Warning hygiene | 20 | density, none at 5 warnings per 1000 lines |
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
| | Code coverage | 25 | line (60 %) and toggle (40 %) coverage of the design, in full from 90 % and 75 %; not evaluated when the run collected none |

The three components above code coverage add up to 100, and code coverage
comes on top. A run that collects no coverage (`sim.coverage` is off, or the
harness cannot write it) is scored over those three alone, as in version 2.
With coverage the total weight is 125 and the score is still on a scale of
100.

Code coverage is that of the design under test, merged over the cases: the
sources owned as `cpu`, `design` or `third_party`. The SoC harness, the
adapter and generated code are the fixture and do not count. Verilator's line
and branch points together are the line coverage. A kind of which the design
has no point passes its share to the other.

Turning coverage on can lower a score: a run whose single case passes scores
100 without it and less with it, because what the case leaves unexercised is
now measured.

The simulation of an RTL design with its own testbench is gated and not
scored.

Review and lint judge the RTL the user owns. A core bundled with ECC-FE is
third-party code: its user owns none of it, so its lint warnings do not count
against them.

## Structural Measurements

The review step measures fanout, fanin and combinational depth on the Yosys
netlist. They are recorded as the metrics `max_fanout`, `max_fanin` and
`max_combinational_depth`, so `ecc-fe report compare` shows how they move
between runs. They are not scored and raise no warning. The netlist is
word-level, since Yosys has only run `proc` and light clean-up:

- A `case` is one cell, and its fanin is the number of all its input bits.
- Depth counts word-level cells, not gates.
- A synchronous reset lowered into mux selects counts as fanout.

So the numbers do not describe the synthesized circuit, and no single limit
suits both a small core and a large one. The lists `high_fanout_nets`,
`high_fanin_cells` and `deep_comb_paths` hold the largest of each, whatever
their size (eight per module, twenty for the design), to find them in the
source.

How they are counted:

- Clock and reset pins of registers and memories are not counted as fanout.
  Counting them made the number of registers the fanout of every module.
- An instance of another module is a boundary. A path into it and out again
  is not a combinational path of the module that instantiates it.
- "Combinational cycle candidate" is a note (`info`), one per module, placed
  where the module is defined. The graph is built from whole-word cells, so
  bits of one bus that feed other bits of it look like a loop. A real loop is
  found by Yosys `check` and reported as the error "Combinational loop
  reported by Yosys precheck".

## What Changed From Version 3

Version 4 takes structural headroom out of the review score. It compared
fanout, fanin and depth with fixed targets (64, 32 and 16) that every real
CPU exceeded, by a margin that measured the word-level netlist rather than
the design. The three warnings on those targets are gone as well, and with
them their share of the warning density. The other three review components
keep their proportions on a total of 100: 40, 40 and 20 in place of 30, 30
and 15. An error still costs 10 points. The score of every other step is
computed as in version 3.

## What Changed From Version 2

Version 3 adds the code coverage component to the simulation score and the
`line_coverage` and `toggle_coverage` metrics. Nothing else changed: the
score of every other step, and of a simulation that collected no coverage,
is computed as in version 2.

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

`report compare` shows both scores of a step and gives no `score_delta` when
their versions differ.

## Not Covered

- Without coverage, the simulation score does not reflect how much was
  tested: one passing case earns the full case weight. Collecting coverage
  is what makes it reflect that.
- The simulation of an RTL design with its own testbench reports coverage
  as metrics and is not scored.
- Coverage has no gate: no minimum blocks a result.
- A baseline chosen by the user for regression comparison does not exist.
- The policy cannot be overridden per project.
