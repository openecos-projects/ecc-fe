# VexRiscv CPU Adapter

VexRiscv is listed in the ECOS frontend catalog as an open-source CPU
candidate. This directory currently contains metadata only. It intentionally
does not claim simulation support until a generated VexRiscv configuration,
filelist, and `ecos_vexriscv_cpu_wrapper` are added and validated.

Upstream repository: https://github.com/SpinalHDL/VexRiscv

Planned ECOS work:

- Pick or generate a stable VexRiscv RTL configuration.
- Build a wrapper for `ysyx-axi-cpu-socket-v1`.
- Add a CPU filelist and update this adapter from `metadata_only` to the
  appropriate readiness level.
