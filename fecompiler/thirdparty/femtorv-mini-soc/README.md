# FemtoRV Mini SoC Harness

This is an ECOS catalog harness for small open-source RV32 cores such as
FemtoRV32.  The first implementation reuses the shared minimal ECOS simulator
top and the existing CPU-test driver/runtime from `fecompiler/thirdparty/SoC`.

The goal is to keep the SoC side stable while CPU adapters evolve independently:

- simulator-facing top: `ecos_sim_top`
- CPU socket: `ysyx-axi-cpu-socket-v1`
- supported tests: smoke and CPU tests
- difftest: disabled
