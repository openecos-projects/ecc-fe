`timescale 1ns/1ps

module ysyx_00000000_regfile (
  input  logic        clock,
  input  logic        reset,
  input  logic [4:0]  read_addr1,
  input  logic [4:0]  read_addr2,
  output logic [31:0] read_data1,
  output logic [31:0] read_data2,
  input  logic        write_enable,
  input  logic [4:0]  write_addr,
  input  logic [31:0] write_data,
  output logic [31:0] debug_a0
);

  logic [31:0] registers [0:31];
  integer reset_index;

  always_comb begin
    read_data1 = (read_addr1 == 5'd0) ? 32'b0 : registers[read_addr1];
    read_data2 = (read_addr2 == 5'd0) ? 32'b0 : registers[read_addr2];

    if (write_enable && (write_addr != 5'd0)) begin
      if (read_addr1 == write_addr) read_data1 = write_data;
      if (read_addr2 == write_addr) read_data2 = write_data;
    end
  end

  always_ff @(posedge clock) begin
    if (reset) begin
      for (reset_index = 0; reset_index < 32; reset_index = reset_index + 1)
        registers[reset_index] <= 32'b0;
    end else begin
      if (write_enable && (write_addr != 5'd0)) registers[write_addr] <= write_data;
      registers[0] <= 32'b0;
    end
  end

  assign debug_a0 = registers[10];

endmodule
