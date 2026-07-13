# VexRiscv CPU Adapter

VexRiscv is wired into the ECOS frontend catalog through a generated LiteX Hub
RTL snapshot and an ECOS CPU wrapper.

Upstream repository: https://github.com/SpinalHDL/VexRiscv
Generated RTL source: https://github.com/litex-hub/pythondata-cpu-vexriscv
Snapshot revision: `642ecfed1c84460555d6d803d660cc60cfc1ecb6`

Current support:

- selected RTL: `VexRiscv_Min.v`,
- upstream bus: separate instruction/data Wishbone ports,
- ECOS wrapper: `ecos_vexriscv_cpu_wrapper`,
- SoC-facing module: `cpu_top` via the bundled legacy bridge,
- supported suites: `smoke`, `cpu-tests`,
- difftest/RT-Thread: not supported.

Planned ECOS work:

- Run real Verilator `cpu-tests/add` in the GUI/CLI and tune Wishbone-to-socket
  timing if the generated core needs extra wait-state handling.
- Consider additional generated VexRiscv profiles after the Min profile is
  proven stable.
