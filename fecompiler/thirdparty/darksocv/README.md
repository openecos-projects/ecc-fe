# DarkSoCV Harness Profile

DarkSoCV is represented in ECOS as a simulator-ready harness profile.  The
profile keeps the public DarkSoCV catalog entry, but uses the shared
`ecos_sim_top` CPU-test harness until a full DarkSoCV-specific simulator top is
landed.

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Future work can replace the shared minimal harness with a real DarkSoCV wrapper
while keeping the same simulator-facing contract.
