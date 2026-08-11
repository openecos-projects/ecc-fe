`timescale 1ns/1ps

module ysyx_00000000_difftest (
  input logic        clock,
  input logic        reset,
  input logic        commit_valid,
  input logic [31:0] commit_pc,
  input logic [31:0] commit_npc,
  input logic [31:0] commit_inst,
  input logic        commit_rd_wen,
  input logic [4:0]  commit_rd_addr,
  input logic [31:0] commit_rd_data,
  input logic        commit_csr_wen,
  input logic [11:0] commit_csr_addr,
  input logic [31:0] commit_csr_data
);

`ifndef SYNTHESIS
  import "DPI-C" function int difftest_step(
    int n,
    int unsigned      pc       [],
    int unsigned      npc      [],
    int unsigned      inst     [],
    shortint unsigned rd_idx   [],
    shortint unsigned wen      [],
    int unsigned      wdata    [],
    shortint unsigned commit   [],
    shortint unsigned skip     [],
    shortint unsigned csr_wen  [],
    int unsigned      csr_wdata[],
    shortint unsigned csr_waddr[],
    shortint unsigned irq_en   []
  );

  localparam int COMMIT_PORTS = 1;

  int unsigned      pc        [COMMIT_PORTS];
  int unsigned      npc       [COMMIT_PORTS];
  int unsigned      inst      [COMMIT_PORTS];
  shortint unsigned rd_idx    [COMMIT_PORTS];
  shortint unsigned wen       [COMMIT_PORTS];
  int unsigned      wdata     [COMMIT_PORTS];
  shortint unsigned commit    [COMMIT_PORTS];
  shortint unsigned skip      [COMMIT_PORTS];
  shortint unsigned csr_wen   [COMMIT_PORTS];
  int unsigned      csr_wdata [COMMIT_PORTS];
  shortint unsigned csr_waddr [COMMIT_PORTS];
  shortint unsigned irq_en    [COMMIT_PORTS];
  always_comb begin
    pc[0]        = commit_pc;
    npc[0]       = commit_npc;
    inst[0]      = commit_inst;
    rd_idx[0]    = {11'b0, commit_rd_addr};
    wen[0]       = {15'b0, commit_rd_wen};
    wdata[0]     = commit_rd_data;
    commit[0]    = {15'b0, commit_valid};
    skip[0]      = 16'b0;
    csr_wen[0]   = {15'b0, commit_csr_wen};
    csr_wdata[0] = commit_csr_wen ? commit_csr_data : 32'b0;
    csr_waddr[0] = commit_csr_wen ? {4'b0, commit_csr_addr} : 16'b0;
    irq_en[0]    = 16'b0;
  end

  always @(posedge clock) begin
    if (!reset) begin
      if (difftest_step(
            COMMIT_PORTS,
            pc,
            npc,
            inst,
            rd_idx,
            wen,
            wdata,
            commit,
            skip,
            csr_wen,
            csr_wdata,
            csr_waddr,
            irq_en
          ) != 0)
        $fatal(1, "HIT BAD TRAP: difftest mismatch");
    end
  end
`else
  logic unused_inputs;
  assign unused_inputs = ^{clock, reset, commit_valid, commit_pc, commit_npc,
                           commit_inst, commit_rd_wen, commit_rd_addr,
                           commit_rd_data, commit_csr_wen, commit_csr_addr,
                           commit_csr_data};
`endif

endmodule
