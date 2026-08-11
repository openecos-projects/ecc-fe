`timescale 1ns/1ps

module ysyx_00000000_csr (
  input  logic        clock,
  input  logic        reset,
  input  logic        interrupt,

  input  logic [11:0] read_addr,
  output logic [31:0] read_data,
  output logic        read_valid,
  output logic        read_only,
  output logic [31:0] mtvec,
  output logic [31:0] mepc,

  input  logic        retire_valid,
  input  logic        write_enable,
  input  logic [11:0] write_addr,
  input  logic [31:0] write_data,
  input  logic        mret_fire,
  input  logic        trap_valid,
  input  logic [31:0] trap_pc,
  input  logic [31:0] trap_cause,
  input  logic [31:0] trap_tval
);

  logic [31:0] mstatus;
  logic [31:0] mscratch;
  logic [31:0] mcause;
  logic [31:0] mtval;
  logic [31:0] mie;
  logic [31:0] mip;
  logic [63:0] mcycle;
  logic [63:0] minstret;

  always_comb begin
    read_valid = 1'b1;
    read_only = 1'b0;
    unique case (read_addr)
      12'h300: read_data = mstatus;
      12'h301: begin read_data = 32'h4000_0100; read_only = 1'b1; end
      12'h304: read_data = mie;
      12'h305: read_data = mtvec;
      12'h340: read_data = mscratch;
      12'h341: read_data = mepc;
      12'h342: read_data = mcause;
      12'h343: read_data = mtval;
      12'h344: read_data = mip;
      12'hb00: read_data = mcycle[31:0];
      12'hb80: read_data = mcycle[63:32];
      12'hb02: read_data = minstret[31:0];
      12'hb82: read_data = minstret[63:32];
      12'hc00: begin read_data = mcycle[31:0]; read_only = 1'b1; end
      12'hc80: begin read_data = mcycle[63:32]; read_only = 1'b1; end
      12'hc02: begin read_data = minstret[31:0]; read_only = 1'b1; end
      12'hc82: begin read_data = minstret[63:32]; read_only = 1'b1; end
      12'hf11, 12'hf12, 12'hf13, 12'hf14: begin
        read_data = 32'b0;
        read_only = 1'b1;
      end
      default: begin
        read_data = 32'b0;
        read_valid = 1'b0;
      end
    endcase
  end

  always_ff @(posedge clock) begin
    if (reset) begin
      mstatus  <= 32'h0000_1800;
      mtvec    <= 32'b0;
      mscratch <= 32'b0;
      mepc     <= 32'b0;
      mcause   <= 32'b0;
      mtval    <= 32'b0;
      mie      <= 32'b0;
      mip      <= 32'b0;
      mcycle   <= 64'b0;
      minstret <= 64'b0;
    end else begin
      mcycle <= mcycle + 64'd1;
      mip[11] <= interrupt;

      if (retire_valid) begin
        minstret <= minstret + 64'd1;
        if (write_enable) begin
          unique case (write_addr)
            12'h300: mstatus  <= write_data;
            12'h304: mie      <= write_data;
            12'h305: mtvec    <= write_data;
            12'h340: mscratch <= write_data;
            12'h341: mepc     <= write_data;
            12'h342: mcause   <= write_data;
            12'h343: mtval    <= write_data;
            12'h344: mip      <= write_data;
            12'hb00: mcycle[31:0]   <= write_data;
            12'hb80: mcycle[63:32]  <= write_data;
            12'hb02: minstret[31:0]  <= write_data;
            12'hb82: minstret[63:32] <= write_data;
            default: ;
          endcase
        end
      end

      if (mret_fire) begin
        mstatus[3]     <= mstatus[7];
        mstatus[7]     <= 1'b1;
        mstatus[12:11] <= 2'b00;
      end

      if (trap_valid) begin
        mepc           <= trap_pc;
        mcause         <= trap_cause;
        mtval          <= trap_tval;
        mstatus[7]     <= mstatus[3];
        mstatus[3]     <= 1'b0;
        mstatus[12:11] <= 2'b11;
      end
    end
  end

endmodule
