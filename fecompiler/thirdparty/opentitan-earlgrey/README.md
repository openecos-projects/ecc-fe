# OpenTitan Earl Grey Harness

OpenTitan Earl Grey is represented as an ECOS simulator-ready harness profile.
The full Earl Grey SoC is intentionally not pulled into this lightweight path
yet; the profile uses the shared minimal `ecos_sim_top` wrapper so frontend
workspaces can still exercise CPU-test flows through a stable contract.

Upstream repository: https://github.com/lowRISC/opentitan

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Planned ECOS work is to introduce a scoped Earl Grey simulation top behind the
same wrapper contract.
