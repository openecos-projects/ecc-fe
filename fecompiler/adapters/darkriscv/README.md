# DarkRISCV CPU Catalog Entry

DarkRISCV is included as a real third-party RTL source with an experimental ECOS
CPU adapter.

The core has a native Harvard-style memory interface and resets at a low address.
`ecos_darkriscv_cpu_wrapper.v` keeps the upstream RTL unchanged and maps that low
address alias into the ECOS CPU-test memory window used by the simulator harness.

Current status:

- Upstream source is available in `fecompiler/thirdparty/darkriscv`.
- `filelist.cpu.f` points at the DarkRISCV core RTL and ECOS wrapper.
- The adapter exposes `ysyx-axi-cpu-socket-v1`.
- CPU Tests are enabled for basic one-case experiments such as `add`.
- Difftest is not supported by this adapter.
