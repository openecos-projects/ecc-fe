# SCR1 CPU Adapter

SCR1 is integrated as an experimental ECOS frontend CPU adapter.

- Upstream repository: https://github.com/syntacore/scr1
- ECOS wrapper: `ecos_scr1_cpu_wrapper.sv`
- CPU socket: `ysyx-axi-cpu-socket-v1`
- Supported suites: `smoke`, `cpu-tests`
- Not supported yet: difftest

The adapter keeps SCR1 upstream RTL unmodified.  `scr1_arch_custom.svh` sets the
ECOS reset vector and simulation memory map, while the wrapper adapts SCR1's
internal instruction/data memory ports to the common ECOS CPU socket.

This adapter should be treated as experimental until it has been exercised with
Verilator in the GUI flow.  Start with one CPU test case such as `add`.
