# CORE-V Mini SoC Harness

This is a small ECOS frontend SoC wrapper for open-source RISC-V CPU adapter
experiments, especially CORE-V style cores such as CV32E40P.

It exposes the same simulator-facing top as the other frontend harnesses:

- Top module: `ecos_sim_top`
- SoC wrapper contract: `ecos-sim-wrapper-v1`
- CPU socket contract: `ysyx-axi-cpu-socket-v1`
- Testbench/runtime: reused from `../SoC/driver`
- Difftest: not supported

The harness intentionally stays close to `minimal-riscv-soc` while carrying its
own catalog identity.  That gives the GUI a separate, meaningful target for
Core-V experiments without changing the stable YSYX AM SoC path.
