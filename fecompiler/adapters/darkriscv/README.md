# DarkRISCV CPU Catalog Entry

DarkRISCV is included as a real third-party RTL source candidate for future ECOS
CPU adapter work.

This entry is intentionally marked `filelist_ready`, not `sim_ready`.  The core
has a native Harvard-style memory interface and an upstream reset-PC convention
that needs a dedicated ECOS wrapper before it can run the standard CPU tests.

Current status:

- Upstream source is available in `fecompiler/thirdparty/darkriscv`.
- `filelist.cpu.f` points at the DarkRISCV core RTL.
- Simulation is blocked until `ecos_darkriscv_cpu_wrapper` is implemented and
  validated against `ysyx-axi-cpu-socket-v1`.
