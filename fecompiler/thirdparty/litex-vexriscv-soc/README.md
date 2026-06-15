# LiteX VexRiscv SoC Harness

LiteX VexRiscv is listed in the ECOS frontend catalog as an open-source SoC
candidate. This directory currently contains metadata only. It intentionally
does not claim simulation support until a fixed generated SoC configuration,
filelist, and `ecos_sim_top` wrapper are added and validated.

Upstream repository: https://github.com/enjoy-digital/litex

Planned ECOS work:

- Select a reproducible LiteX VexRiscv generated RTL configuration.
- Add an `ecos_sim_top` wrapper that presents the ECOS simulator-facing ports.
- Decide whether the CPU socket is native, translated, or provided by a
  generated CPU wrapper.
- Add tests and move the catalog level beyond `metadata_only`.
