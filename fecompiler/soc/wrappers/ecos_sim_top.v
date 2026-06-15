// ECOS simulator-facing SoC wrapper contract.
//
// Real SoC integrations should implement this interface and adapt their
// private SoC ports internally.  This template is not used by the current YSYX
// AM SoC flow yet; it documents the stable simulator IO that future wrappers
// should expose.

module ecos_sim_top (
  input  wire        clock,
  input  wire        reset,
  input  wire        uart_rx,
  output wire        uart_tx,
  output wire        trap_valid,
  output wire [31:0] trap_code
);
  assign uart_tx = 1'b1;
  assign trap_valid = 1'b0;
  assign trap_code = 32'h0;
endmodule
