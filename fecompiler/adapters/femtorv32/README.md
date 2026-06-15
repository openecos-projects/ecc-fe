# FemtoRV32 Electron CPU Adapter

This adapter integrates the FemtoRV32 Electron core from
`fecompiler/thirdparty/learn-fpga` with the ECOS frontend CPU socket.

The upstream core is kept unmodified.  `ecos_femtorv32_cpu_wrapper` adapts the
single memory port to `ysyx-axi-cpu-socket-v1`, sets the reset vector to
`0x20000000`, and handles the ECOS UART/HALT MMIO convention locally.

Current status:

- CPU filelist is provided by `filelist.cpu.f`.
- CPU tests and smoke tests are cataloged as experimental.
- Difftest is not supported.
