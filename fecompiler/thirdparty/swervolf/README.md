# SweRVolf SoC Harness

SweRVolf is represented as an ECOS simulator-ready harness profile.  The
profile uses the shared minimal `ecos_sim_top` wrapper for current CPU-test
workspace creation while the full SweRVolf source integration remains future
work.

Upstream repository: https://github.com/chipsalliance/Cores-SweRVolf

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Planned ECOS work is to replace this profile with a stable SweRVolf RTL
snapshot while keeping the same external simulator contract.
