`timescale 1ns/1ps

module ysyx_00000000_decode (
  input  logic [31:0] inst,
  input  logic        fetch_error,

  output logic [2:0]  funct3,
  output logic [4:0]  rs1,
  output logic [4:0]  rs2,
  output logic [4:0]  rd,
  output logic [31:0] imm,
  output logic [3:0]  alu_op,
  output logic        op1_zero,
  output logic        op1_pc,
  output logic        op2_imm,
  output logic        reg_write,
  output logic [2:0]  wb_sel,
  output logic        mem_read,
  output logic        mem_write,
  output logic [1:0]  mem_size,
  output logic        load_unsigned,
  output logic        branch,
  output logic        jal,
  output logic        jalr,
  output logic [1:0]  csr_op,
  output logic        csr_imm,
  output logic [11:0] csr_addr,
  output logic        ecall,
  output logic        ebreak,
  output logic        mret,
  output logic        fencei,
  output logic        illegal,
  output logic        uses_rs1,
  output logic        uses_rs2,
  output logic        serializing
);

  localparam logic [3:0] ALU_ADD  = 4'd0;
  localparam logic [3:0] ALU_SUB  = 4'd1;
  localparam logic [3:0] ALU_SLL  = 4'd2;
  localparam logic [3:0] ALU_SLT  = 4'd3;
  localparam logic [3:0] ALU_SLTU = 4'd4;
  localparam logic [3:0] ALU_XOR  = 4'd5;
  localparam logic [3:0] ALU_SRL  = 4'd6;
  localparam logic [3:0] ALU_SRA  = 4'd7;
  localparam logic [3:0] ALU_OR   = 4'd8;
  localparam logic [3:0] ALU_AND  = 4'd9;

  localparam logic [2:0] WB_ALU = 3'd0;
  localparam logic [2:0] WB_MEM = 3'd1;
  localparam logic [2:0] WB_PC4 = 3'd2;
  localparam logic [2:0] WB_CSR = 3'd3;

  localparam logic [1:0] CSR_NONE = 2'd0;
  localparam logic [1:0] CSR_RW   = 2'd1;
  localparam logic [1:0] CSR_RS   = 2'd2;
  localparam logic [1:0] CSR_RC   = 2'd3;

  logic [6:0] opcode;
  logic [6:0] funct7;
  logic       fence;

  always_comb begin
    opcode        = inst[6:0];
    funct3        = inst[14:12];
    funct7        = inst[31:25];
    rs1           = inst[19:15];
    rs2           = inst[24:20];
    rd            = inst[11:7];
    imm           = 32'b0;
    alu_op        = ALU_ADD;
    op1_zero      = 1'b0;
    op1_pc        = 1'b0;
    op2_imm       = 1'b0;
    reg_write     = 1'b0;
    wb_sel        = WB_ALU;
    mem_read      = 1'b0;
    mem_write     = 1'b0;
    mem_size      = 2'd2;
    load_unsigned = 1'b0;
    branch        = 1'b0;
    jal           = 1'b0;
    jalr          = 1'b0;
    csr_op        = CSR_NONE;
    csr_imm       = 1'b0;
    csr_addr      = inst[31:20];
    ecall         = 1'b0;
    ebreak        = 1'b0;
    mret          = 1'b0;
    fence         = 1'b0;
    fencei        = 1'b0;
    illegal       = 1'b0;
    uses_rs1      = 1'b0;
    uses_rs2      = 1'b0;

    unique case (opcode)
      7'b0110111: begin
        imm       = {inst[31:12], 12'b0};
        op1_zero  = 1'b1;
        op2_imm   = 1'b1;
        reg_write = 1'b1;
      end
      7'b0010111: begin
        imm       = {inst[31:12], 12'b0};
        op1_pc    = 1'b1;
        op2_imm   = 1'b1;
        reg_write = 1'b1;
      end
      7'b1101111: begin
        imm = {{11{inst[31]}}, inst[31], inst[19:12], inst[20],
               inst[30:21], 1'b0};
        jal       = 1'b1;
        reg_write = 1'b1;
        wb_sel    = WB_PC4;
      end
      7'b1100111: begin
        imm       = {{20{inst[31]}}, inst[31:20]};
        jalr      = 1'b1;
        reg_write = 1'b1;
        wb_sel    = WB_PC4;
        uses_rs1  = 1'b1;
        if (funct3 != 3'b000) illegal = 1'b1;
      end
      7'b1100011: begin
        imm = {{19{inst[31]}}, inst[31], inst[7], inst[30:25],
               inst[11:8], 1'b0};
        branch   = 1'b1;
        uses_rs1 = 1'b1;
        uses_rs2 = 1'b1;
        if (!(funct3 inside {3'b000, 3'b001, 3'b100, 3'b101, 3'b110, 3'b111}))
          illegal = 1'b1;
      end
      7'b0000011: begin
        imm       = {{20{inst[31]}}, inst[31:20]};
        op2_imm   = 1'b1;
        reg_write = 1'b1;
        wb_sel    = WB_MEM;
        mem_read  = 1'b1;
        uses_rs1  = 1'b1;
        unique case (funct3)
          3'b000: begin mem_size = 2'd0; load_unsigned = 1'b0; end
          3'b001: begin mem_size = 2'd1; load_unsigned = 1'b0; end
          3'b010: begin mem_size = 2'd2; load_unsigned = 1'b0; end
          3'b100: begin mem_size = 2'd0; load_unsigned = 1'b1; end
          3'b101: begin mem_size = 2'd1; load_unsigned = 1'b1; end
          default: illegal = 1'b1;
        endcase
      end
      7'b0100011: begin
        imm       = {{20{inst[31]}}, inst[31:25], inst[11:7]};
        op2_imm   = 1'b1;
        mem_write = 1'b1;
        uses_rs1  = 1'b1;
        uses_rs2  = 1'b1;
        unique case (funct3)
          3'b000: mem_size = 2'd0;
          3'b001: mem_size = 2'd1;
          3'b010: mem_size = 2'd2;
          default: illegal = 1'b1;
        endcase
      end
      7'b0010011: begin
        imm       = {{20{inst[31]}}, inst[31:20]};
        op2_imm   = 1'b1;
        reg_write = 1'b1;
        uses_rs1  = 1'b1;
        unique case (funct3)
          3'b000: alu_op = ALU_ADD;
          3'b010: alu_op = ALU_SLT;
          3'b011: alu_op = ALU_SLTU;
          3'b100: alu_op = ALU_XOR;
          3'b110: alu_op = ALU_OR;
          3'b111: alu_op = ALU_AND;
          3'b001: begin
            alu_op = ALU_SLL;
            if (funct7 != 7'b0000000) illegal = 1'b1;
          end
          3'b101: begin
            if (funct7 == 7'b0000000) alu_op = ALU_SRL;
            else if (funct7 == 7'b0100000) alu_op = ALU_SRA;
            else illegal = 1'b1;
          end
          default: illegal = 1'b1;
        endcase
      end
      7'b0110011: begin
        reg_write = 1'b1;
        uses_rs1  = 1'b1;
        uses_rs2  = 1'b1;
        unique case (funct3)
          3'b000: begin
            if (funct7 == 7'b0000000) alu_op = ALU_ADD;
            else if (funct7 == 7'b0100000) alu_op = ALU_SUB;
            else illegal = 1'b1;
          end
          3'b001: begin alu_op = ALU_SLL;  if (funct7 != 7'b0000000) illegal = 1'b1; end
          3'b010: begin alu_op = ALU_SLT;  if (funct7 != 7'b0000000) illegal = 1'b1; end
          3'b011: begin alu_op = ALU_SLTU; if (funct7 != 7'b0000000) illegal = 1'b1; end
          3'b100: begin alu_op = ALU_XOR;  if (funct7 != 7'b0000000) illegal = 1'b1; end
          3'b101: begin
            if (funct7 == 7'b0000000) alu_op = ALU_SRL;
            else if (funct7 == 7'b0100000) alu_op = ALU_SRA;
            else illegal = 1'b1;
          end
          3'b110: begin alu_op = ALU_OR;  if (funct7 != 7'b0000000) illegal = 1'b1; end
          3'b111: begin alu_op = ALU_AND; if (funct7 != 7'b0000000) illegal = 1'b1; end
          default: illegal = 1'b1;
        endcase
      end
      7'b0001111: begin
        if (funct3 == 3'b000) fence = 1'b1;
        else if (funct3 == 3'b001) fencei = 1'b1;
        else illegal = 1'b1;
      end
      7'b1110011: begin
        unique case (funct3)
          3'b000: begin
            if (inst == 32'h0000_0073) ecall = 1'b1;
            else if (inst == 32'h0010_0073) ebreak = 1'b1;
            else if (inst == 32'h3020_0073) mret = 1'b1;
            else illegal = 1'b1;
          end
          3'b001: begin csr_op = CSR_RW; uses_rs1 = 1'b1; end
          3'b010: begin csr_op = CSR_RS; uses_rs1 = 1'b1; end
          3'b011: begin csr_op = CSR_RC; uses_rs1 = 1'b1; end
          3'b101: begin csr_op = CSR_RW; csr_imm = 1'b1; end
          3'b110: begin csr_op = CSR_RS; csr_imm = 1'b1; end
          3'b111: begin csr_op = CSR_RC; csr_imm = 1'b1; end
          default: illegal = 1'b1;
        endcase
        if (csr_op != CSR_NONE) begin
          reg_write = 1'b1;
          wb_sel = WB_CSR;
        end
      end
      default: illegal = 1'b1;
    endcase

    if (fetch_error) illegal = 1'b0;
    serializing = illegal || ecall || ebreak || mret || fence || fencei ||
                  (csr_op != CSR_NONE);
  end

endmodule
