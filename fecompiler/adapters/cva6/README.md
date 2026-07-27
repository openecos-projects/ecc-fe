# CVA6 CPU Adapter

CVA6 is listed in the ECOS frontend catalog as an experimental open-source CPU
adapter target.

This adapter uses the CV32A6 IMAC Sv32 configuration from the pythondata CVA6
snapshot and keeps the upstream RTL under `fecompiler/thirdparty/cva6`.
`ecos_cva6_cpu_wrapper.sv` instantiates CVA6 with a 32-bit AXI profile and maps
the typed CVA6 AXI request/response structs onto the flat ECOS
`ysyx-axi-cpu-socket-v1` CPU socket.

Current status:

- Source snapshot: pythondata-cpu-cva6 commit
  `da8c19c8142eee4053b714fc2b748d746e17f175`.
- Upstream origin: https://github.com/openhwgroup/cva6
- Filelist: `filelist.cpu.f`
- Wrapper top: `ecos_cva6_cpu_wrapper`
- SoC-facing module: `cpu_top`
- Test suites: `smoke`, `cpu-tests`
- Difftest is not supported by this adapter.

The first ECOS profile is intentionally RV32/AXI32.  CVA6's upstream default
AXI package is 64-bit, so this wrapper provides local 32-bit AXI channel types
and passes them into the parameterized CVA6 top.
