# Minimal RISC-V SoC Harness

This is the first ECOS frontend SoC harness that is not a copy of the YSYX AM
SoC package. It keeps the same simulator-facing top module, `ecos_sim_top`, and
uses the same `ysyx-axi-cpu-socket-v1` CPU socket so existing CPU wrappers can be
reused.

The harness is intentionally small:

- CPU socket: `ysyx-axi-cpu-socket-v1`
- Simulator top: `ecos_sim_top`
- Memory model: DPI-backed `mem_read` / `mem_write`
- Supported suites: `smoke`, `cpu-tests`
- Not supported yet: RT-Thread and difftest

The filelist includes the default CL3 adapter from `../SoC/ysyx_00000000.sv`.
When another CPU wrapper, such as PicoRV32, provides its own `ysyx_00000000`
compatibility module, the prepare step filters this default adapter out to avoid
duplicate module definitions.
