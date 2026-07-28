# SERV CPU Adapter

SERV is integrated as an experimental ECOS frontend CPU adapter.  The adapter
wraps SERV's separate instruction/data Wishbone-like ports and exposes the
`ysyx-axi-cpu-socket-v1` socket used by the current ECOS SoC wrappers.

Upstream repository: https://github.com/olofk/serv

Current support:

- CPU socket: `ysyx-axi-cpu-socket-v1`
- Wrapper top: `ecos_serv_cpu_wrapper`
- SoC-facing module: `cpu_top`
- Supported suites: `smoke`, `cpu-tests`
- Default CPU test case: `add`
- Difftest: not supported

The SERV adapter is meant to be used with the minimal RISC-V SoC harness first.
Broader SoC compatibility should be validated one harness at a time.
