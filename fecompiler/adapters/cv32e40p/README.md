# CV32E40P CPU Adapter

CV32E40P is listed in the ECOS frontend catalog as an open-source CPU
candidate. This directory currently contains metadata only. It intentionally
does not claim simulation support until the RTL source, filelist, and
`ecos_cv32e40p_cpu_wrapper` are added and validated.

Upstream repository: https://github.com/openhwgroup/cv32e40p

Planned ECOS work:

- Add or fetch CV32E40P RTL sources.
- Build a wrapper for `ysyx-axi-cpu-socket-v1`.
- Add a CPU filelist and update this adapter from `metadata_only` to the
  appropriate readiness level.
