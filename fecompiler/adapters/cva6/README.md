# CVA6 CPU Adapter

CVA6 is listed in the ECOS frontend catalog as an open-source CPU candidate.
This directory currently contains metadata only. It intentionally does not
claim simulation support until the RTL source, filelist, and
`ecos_cva6_cpu_wrapper` are added and validated.

Upstream repository: https://github.com/openhwgroup/cva6

Planned ECOS work:

- Decide whether ECOS should target a 32-bit or 64-bit CVA6 configuration.
- Add or fetch CVA6 RTL sources.
- Build a wrapper for the selected ECOS CPU socket.
- Add a CPU filelist and update this adapter from `metadata_only` to the
  appropriate readiness level.
