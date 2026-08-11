`timescale 1ns/1ps

module ysyx_00000000_execute (
  input  logic [31:0] pc,
  input  logic [31:0] inst,
  input  logic [4:0]  rs1_addr,
  input  logic [31:0] rs1_data,
  input  logic [31:0] rs2_data,
  input  logic [31:0] imm,
  input  logic [3:0]  alu_op,
  input  logic        op1_zero,
  input  logic        op1_pc,
  input  logic        op2_imm,
  input  logic [2:0]  wb_sel,
  input  logic        mem_read,
  input  logic        mem_write,
  input  logic [1:0]  mem_size,
  input  logic        branch,
  input  logic [2:0]  branch_funct3,
  input  logic        jal,
  input  logic        jalr,
  input  logic [1:0]  csr_op,
  input  logic        csr_imm,
  input  logic        ecall,
  input  logic        ebreak,
  input  logic        mret,
  input  logic        fencei,
  input  logic        illegal,
  input  logic        fetch_error,
  input  logic [31:0] csr_read_data,
  input  logic        csr_read_valid,
  input  logic        csr_read_only,
  input  logic [31:0] csr_mepc,

  output logic [31:0] alu_result,
  output logic        redirect,
  output logic [31:0] redirect_target,
  output logic [31:0] next_pc,
  output logic [31:0] wb_data,
  output logic        csr_write,
  output logic [31:0] csr_write_data,
  output logic        exception,
  output logic [31:0] exception_cause,
  output logic [31:0] exception_tval
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

  localparam logic [2:0] WB_PC4 = 3'd2;
  localparam logic [2:0] WB_CSR = 3'd3;

  localparam logic [1:0] CSR_NONE = 2'd0;
  localparam logic [1:0] CSR_RW   = 2'd1;
  localparam logic [1:0] CSR_RS   = 2'd2;
  localparam logic [1:0] CSR_RC   = 2'd3;

  localparam logic [31:0] CAUSE_INST_ADDR_MISALIGNED = 32'd0;
  localparam logic [31:0] CAUSE_INST_ACCESS_FAULT    = 32'd1;
  localparam logic [31:0] CAUSE_ILLEGAL_INST         = 32'd2;
  localparam logic [31:0] CAUSE_BREAKPOINT           = 32'd3;
  localparam logic [31:0] CAUSE_LOAD_MISALIGNED      = 32'd4;
  localparam logic [31:0] CAUSE_STORE_MISALIGNED     = 32'd6;
  localparam logic [31:0] CAUSE_ECALL_M              = 32'd11;

  logic [31:0] operand1;
  logic [31:0] operand2;
  logic        branch_taken;
  logic [31:0] csr_source;
  logic        csr_write_intent;
  logic        target_misaligned;
  logic        data_misaligned;

  assign operand1 = op1_zero ? 32'b0 : (op1_pc ? pc : rs1_data);
  assign operand2 = op2_imm ? imm : rs2_data;

  always_comb begin
    unique case (alu_op)
      ALU_ADD:  alu_result = operand1 + operand2;
      ALU_SUB:  alu_result = operand1 - operand2;
      ALU_SLL:  alu_result = operand1 << operand2[4:0];
      ALU_SLT:  alu_result = {31'b0, $signed(operand1) < $signed(operand2)};
      ALU_SLTU: alu_result = {31'b0, operand1 < operand2};
      ALU_XOR:  alu_result = operand1 ^ operand2;
      ALU_SRL:  alu_result = operand1 >> operand2[4:0];
      ALU_SRA:  alu_result = $unsigned($signed(operand1) >>> operand2[4:0]);
      ALU_OR:   alu_result = operand1 | operand2;
      ALU_AND:  alu_result = operand1 & operand2;
      default:  alu_result = 32'b0;
    endcase
  end

  always_comb begin
    unique case (branch_funct3)
      3'b000: branch_taken = (rs1_data == rs2_data);
      3'b001: branch_taken = (rs1_data != rs2_data);
      3'b100: branch_taken = ($signed(rs1_data) < $signed(rs2_data));
      3'b101: branch_taken = ($signed(rs1_data) >= $signed(rs2_data));
      3'b110: branch_taken = (rs1_data < rs2_data);
      3'b111: branch_taken = (rs1_data >= rs2_data);
      default: branch_taken = 1'b0;
    endcase
  end

  always_comb begin
    redirect = 1'b0;
    redirect_target = pc + 32'd4;
    if (branch && branch_taken) begin
      redirect = 1'b1;
      redirect_target = pc + imm;
    end else if (jal) begin
      redirect = 1'b1;
      redirect_target = pc + imm;
    end else if (jalr) begin
      redirect = 1'b1;
      redirect_target = (rs1_data + imm) & 32'hffff_fffe;
    end else if (mret) begin
      redirect = 1'b1;
      redirect_target = csr_mepc;
    end else if (fencei) begin
      redirect = 1'b1;
      redirect_target = pc + 32'd4;
    end
    next_pc = redirect ? redirect_target : (pc + 32'd4);
  end

  assign csr_source = csr_imm ? {27'b0, rs1_addr} : rs1_data;
  assign csr_write_intent = (csr_op == CSR_RW) ||
                            (((csr_op == CSR_RS) || (csr_op == CSR_RC)) &&
                             (csr_source != 32'b0));
  assign csr_write = (csr_op != CSR_NONE) && csr_write_intent;

  always_comb begin
    unique case (csr_op)
      CSR_RW:  csr_write_data = csr_source;
      CSR_RS:  csr_write_data = csr_read_data | csr_source;
      CSR_RC:  csr_write_data = csr_read_data & ~csr_source;
      default: csr_write_data = 32'b0;
    endcase
  end

  always_comb begin
    unique case (wb_sel)
      WB_PC4: wb_data = pc + 32'd4;
      WB_CSR: wb_data = csr_read_data;
      default: wb_data = alu_result;
    endcase
  end

  assign target_misaligned = redirect && (redirect_target[1:0] != 2'b00);
  assign data_misaligned = (mem_size == 2'd2) ? (alu_result[1:0] != 2'b00) :
                           (mem_size == 2'd1) ? alu_result[0] : 1'b0;

  always_comb begin
    exception = 1'b0;
    exception_cause = 32'b0;
    exception_tval = 32'b0;
    if (fetch_error) begin
      exception = 1'b1;
      exception_cause = CAUSE_INST_ACCESS_FAULT;
      exception_tval = pc;
    end else if (pc[1:0] != 2'b00) begin
      exception = 1'b1;
      exception_cause = CAUSE_INST_ADDR_MISALIGNED;
      exception_tval = pc;
    end else if (illegal ||
                 ((csr_op != CSR_NONE) &&
                  (!csr_read_valid || (csr_write_intent && csr_read_only)))) begin
      exception = 1'b1;
      exception_cause = CAUSE_ILLEGAL_INST;
      exception_tval = inst;
    end else if (ecall) begin
      exception = 1'b1;
      exception_cause = CAUSE_ECALL_M;
    end else if (ebreak) begin
      exception = 1'b1;
      exception_cause = CAUSE_BREAKPOINT;
    end else if (target_misaligned) begin
      exception = 1'b1;
      exception_cause = CAUSE_INST_ADDR_MISALIGNED;
      exception_tval = redirect_target;
    end else if (mem_read && data_misaligned) begin
      exception = 1'b1;
      exception_cause = CAUSE_LOAD_MISALIGNED;
      exception_tval = alu_result;
    end else if (mem_write && data_misaligned) begin
      exception = 1'b1;
      exception_cause = CAUSE_STORE_MISALIGNED;
      exception_tval = alu_result;
    end
  end

endmodule
