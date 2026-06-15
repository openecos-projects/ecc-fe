# ECOS CPU Wrapper Contract

Different CPU cores have different native ports.  A CPU integration should
provide a small RTL wrapper that adapts the core to the CPU socket expected by
the selected SoC wrapper.

## Contract v1

The first ECOS CPU socket is the YSYX-style AXI CPU socket used by the current
AM SoC harness.  A compatible CPU wrapper should expose:

- `clock`
- `reset`
- `io_interrupt`
- AXI-like instruction/data master channels:
  - `io_master_aw*`
  - `io_master_w*`
  - `io_master_b*`
  - `io_master_ar*`
  - `io_master_r*`
- Optional slave channels, tied off by wrappers that do not use them.

The current CL3 integration uses `ysyx_00000000` as the reference CPU wrapper.
It adapts `CL3Top` to the YSYX AM SoC CPU socket and also provides the existing
UART/trap side effects used by CPU tests.

## Integration Rule

Adding a new CPU should require:

1. Import or download the CPU RTL.
2. Add a CPU wrapper that implements the ECOS CPU socket contract.
3. Register CPU metadata including wrapper top, socket contract, ISA, filelist,
   and integration level.

Until a CPU wrapper exists, the catalog entry should remain `filelist_ready` or
`metadata_only`, not `sim_ready`.
