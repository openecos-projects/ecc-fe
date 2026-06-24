# ECOS SoC Wrapper Contract

The frontend simulator must not depend on each SoC's private top-level ports.
Every SoC integration should provide a small RTL wrapper that exposes the same
simulator-facing interface and adapts the real SoC internally.

## Contract v1

The simulator-facing top is named by each SoC manifest.  New integrations should
move toward `ecos_sim_top`:

```verilog
module ecos_sim_top (
  input  wire clock,
  input  wire reset,
  input  wire uart_rx,
  output wire uart_tx,
  output wire trap_valid,
  output wire [31:0] trap_code
);
endmodule
```

The existing YSYX AM SoC flow now exposes `ecos_sim_top`, which wraps the
private `ysyxSoCTop` internally.  The simulator driver should include and
instantiate the generated `Vecos_sim_top` model.

## Integration Rule

Adding a new SoC should require:

1. Import or download the SoC RTL.
2. Add a SoC wrapper that conforms to the ECOS simulator contract.
3. Add a SoC manifest so the CLI can discover filelists, driver
   sources, test programs, and supported test suites.
4. Add catalog metadata next to the runtime manifest, including CPU socket
   contract, integration level, and supported test suites.

The runtime and catalog manifests live at:

```text
fecompiler/thirdparty/<soc-id>/manifest.json
fecompiler/thirdparty/<soc-id>/catalog.json
```

Example:

```json
{
  "id": "ysyx-am-soc",
  "name": "YSYX AM SoC Harness",
  "variant": "soc1",
  "top_module": "ecos_sim_top",
  "sim_ready": true,
  "contract": "ecos-sim-wrapper-v1",
  "soc_filelist": "filelist.soc.f",
  "testbench": "driver/main.cpp",
  "sim_cpp_sources": ["driver/dpi_mem.cpp", "driver/difftest.cpp"],
  "sim_cflags": ["-I{soc_root}"],
  "sim_ldflags": ["-ldl"],
  "sim_programs_dir": "tests/programs",
  "sim_tests_dir": "tests/out",
  "sim_build_test_script": "scripts/build_test.sh",
  "supports_difftest": true
}
```

The catalog manifest is merged after the builtin `soc_harnesses.json` fallback
and overrides any entry with the same `id`.  This keeps each SoC integration
self-contained while preserving compatibility with older central catalog
entries.

Adding a new CPU follows the same idea: add the CPU RTL, add a CPU wrapper for
the SoC's CPU socket contract, then register the catalog metadata.

## Static Contract Check

A SoC entry may be marked `sim_ready` only when all of these are true:

- `catalog.json` declares `wrapper_contract: ecos-sim-wrapper-v1`.
- `catalog.json` declares `wrapper_top: ecos_sim_top`.
- `catalog.json` declares `cpu_socket_contract: ysyx-axi-cpu-socket-v1`.
- `catalog.json` declares at least one `supported_test_suites` item.
- `manifest.json` exists next to the SoC wrapper.
- The manifest points to an existing SoC filelist, testbench, build-test
  script, and simulator C++ sources.
- The SoC filelist exists, all RTL paths inside it exist, and it contains the
  declared wrapper top module.

Run the lightweight check before marking a harness runnable:

```bash
python3 -m fecompiler.cli.workspace catalog-check --json
```

This keeps catalog expansion honest: metadata-only SoCs can be shown as future
targets, while `sim_ready` SoCs must already have the concrete ECOS wrapper
collateral needed by workspace creation and preparation.
