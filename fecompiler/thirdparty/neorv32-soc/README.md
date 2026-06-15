# NEORV32 SoC Harness

NEORV32 is listed in the ECOS frontend catalog as an open-source SoC candidate.
This directory currently contains metadata only. It intentionally does not
claim simulation support until a supported source snapshot, wrapper, and test
plan are added and validated.

Upstream repository: https://github.com/stnolting/neorv32

Planned ECOS work:

- Decide whether ECOS will support NEORV32's VHDL sources directly or through a
  generated/synthesized Verilog flow.
- Add an `ecos_sim_top` wrapper with ECOS simulator-facing ports.
- Define the software image format and runnable test suites.
- Keep this entry `metadata_only` until the simulation path is reproducible.
