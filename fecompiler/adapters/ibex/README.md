# Ibex CPU Adapter

Ibex is integrated as an experimental ECOS frontend CPU adapter.  The wrapper
keeps upstream Ibex RTL unmodified and adapts the native instruction/data
memory request interfaces to `ysyx-axi-cpu-socket-v1`.

Upstream repository: https://github.com/lowRISC/ibex

Current ECOS scope:

- `filelist.cpu.f` lists the minimal Ibex RTL dependencies needed by the ECOS
  adapter.
- `ecos_ibex_cpu_wrapper.sv` exposes the stable ECOS CPU wrapper contract and a
  `ysyx_00000000` compatibility module for existing SoC harnesses.
- CPU tests and smoke tests are declared as supported without difftest.

Known limitations:

- This is an experimental adapter.  It has not been promoted to a verified
  production profile.
- Difftest is not exposed yet.
