// ECOS simulator-facing wrapper for the YSYX AM SoC compatibility harness.

module ecos_sim_top (
  input  wire        clock,
  input  wire        reset,
  input  wire        uart_rx,
  output wire        uart_tx,
  output wire        trap_valid,
  output wire [31:0] trap_code
);
  ysyxSoCTop dut (
    .clock(clock),
    .reset(reset)
  );

  assign uart_tx = 1'b1;
  assign trap_valid = 1'b0;
  assign trap_code = 32'h0;
endmodule
