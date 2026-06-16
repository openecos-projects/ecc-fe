# Ibex Demo System Harness

The Ibex demo system entry is now an ECOS simulator-ready harness profile.  It
uses the shared minimal `ecos_sim_top` wrapper so it can participate in frontend
CPU-test workspace creation while the full lowRISC demo-system wrapper remains
future work.

Upstream repository: https://github.com/lowRISC/ibex-demo-system

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Planned ECOS work is to replace the shared profile with the real demo-system
RTL while preserving the same external simulator contract.
