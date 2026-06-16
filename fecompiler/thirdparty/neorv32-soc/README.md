# NEORV32 SoC Harness

NEORV32 is represented as an ECOS simulator-ready harness profile.  Because
NEORV32's upstream RTL is VHDL-first, this profile uses the shared minimal
`ecos_sim_top` wrapper for current CPU-test workspace creation.  A real NEORV32
source snapshot can replace the internals later without changing the GUI/CLI
contract.

Upstream repository: https://github.com/stnolting/neorv32

Current support:

- stable simulator-facing top: `ecos_sim_top`,
- CPU socket contract: `ysyx-axi-cpu-socket-v1`,
- supported suites: `smoke`, `cpu-tests`,
- default smoke case: `add`.

Planned ECOS work is to decide whether the full NEORV32 path is VHDL-native or
generated Verilog, then wire that path behind the same simulator contract.
