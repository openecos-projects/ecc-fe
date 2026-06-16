# LiteX VexRiscv SoC Harness

LiteX VexRiscv is represented as an ECOS simulator-ready harness profile.  This
profile uses the shared minimal `ecos_sim_top` wrapper so the catalog entry can
create and prepare CPU-test workspaces while a fixed generated LiteX RTL
configuration is still future work.

Upstream repository: https://github.com/enjoy-digital/litex

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Planned ECOS work is to swap this profile to generated LiteX RTL without
changing the GUI/CLI contract.
