# OpenTitan Earl Grey Harness

OpenTitan Earl Grey is listed in the ECOS frontend catalog as a future
open-source SoC candidate. This directory currently contains metadata only. It
intentionally does not claim simulation support until a scoped top-level,
filelist, wrapper, and test strategy are added and validated.

Upstream repository: https://github.com/lowRISC/opentitan

Planned ECOS work:

- Decide whether to target the full Earl Grey top or a reduced simulation top.
- Add an `ecos_sim_top` wrapper with ECOS simulator-facing ports.
- Define supported software images and test suites.
- Keep this entry `metadata_only` until the build and simulation path is
  reproducible.
