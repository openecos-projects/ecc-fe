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
3. Register the wrapper metadata so the CLI can discover filelists, driver
   sources, test programs, and supported test suites.

Adding a new CPU follows the same idea: add the CPU RTL, add a CPU wrapper for
the SoC's CPU socket contract, then register the catalog metadata.
