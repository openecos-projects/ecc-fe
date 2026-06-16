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
3. Add a CPU adapter manifest next to the wrapper/filelist.
4. Add catalog metadata next to the runtime manifest, including ISA,
   repository, integration level, and
   supported test suites.

The runtime and catalog manifests live at:

```text
fecompiler/adapters/<cpu-id>/manifest.json
fecompiler/adapters/<cpu-id>/catalog.json
```

Example:

```json
{
  "id": "picorv32",
  "name": "PicoRV32",
  "socket_contract": "ysyx-axi-cpu-socket-v1",
  "wrapper_contract": "ecos-cpu-wrapper-v1",
  "wrapper_top": "ecos_picorv32_cpu_wrapper",
  "sim_ready": true,
  "supports_difftest": false
}
```

The catalog manifest is merged after the builtin `cores.json` fallback and
overrides any entry with the same `id`.  This lets new CPU integrations live in
their own directory while older planned entries can remain in the central
catalog until they are implemented.

Until a CPU wrapper exists, the catalog entry should remain `filelist_ready` or
`metadata_only`, not `sim_ready`.

Users should not need to reason about CPU socket compatibility.  If a CPU and
SoC are both listed as `sim_ready`, their wrappers must already agree on the
socket contract.  A socket mismatch is an ECOS catalog/wrapper integration bug,
not a user configuration problem.

## Static Contract Check

A CPU entry may be marked `sim_ready` only when all of these are true:

- `catalog.json` declares `cpu_wrapper_contract: ecos-cpu-wrapper-v1`.
- `catalog.json` declares `cpu_socket_contract: ysyx-axi-cpu-socket-v1`.
- `catalog.json` declares `cpu_wrapper_top`.
- `catalog.json` declares at least one `supported_test_suites` item.
- Built-in CPU integrations declare `cpu_filelist`.
- The CPU filelist exists and all RTL paths inside it exist.
- The CPU filelist contains the declared wrapper top module.

Run the lightweight check before asking users to try a new adapter:

```bash
python3 -m fecompiler.cli.workspace catalog-check --json
```

This only verifies adapter collateral and catalog consistency; it does not run
Verilator or any CPU test.  Real runtime readiness is still proven by running at
least one CPU test case through the GUI or CLI.
