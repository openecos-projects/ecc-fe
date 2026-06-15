# SweRVolf SoC Harness

SweRVolf is listed in the ECOS frontend catalog as an open-source SoC
candidate. This directory currently contains metadata only. It intentionally
does not claim simulation support until the SoC RTL, filelist, wrapper, and
test path are added and validated.

Upstream repository: https://github.com/chipsalliance/Cores-SweRVolf

Planned ECOS work:

- Add or fetch a stable SweRVolf source snapshot.
- Add an `ecos_sim_top` wrapper with ECOS simulator-facing ports.
- Decide which CPU socket adapter path ECOS will support.
- Add runnable smoke tests before marking the harness `sim_ready`.
