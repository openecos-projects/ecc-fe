# Ibex Demo System Harness

The Ibex demo system is listed in the ECOS frontend catalog as an open-source
SoC candidate. This directory currently contains metadata only. It intentionally
does not claim simulation support until the SoC RTL, filelist, and
`ecos_sim_top` wrapper are added and validated.

Upstream repository: https://github.com/lowRISC/ibex-demo-system

Planned ECOS work:

- Add or fetch the demo system RTL and dependency filelist.
- Wrap the demo system with the stable `ecos_sim_top` simulator contract.
- Define which ECOS CPU adapter/socket path is supported.
- Add at least smoke tests before marking it runnable.
