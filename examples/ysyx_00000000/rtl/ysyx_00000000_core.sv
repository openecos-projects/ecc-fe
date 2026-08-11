`timescale 1ns/1ps

module ysyx_00000000_core #(
  parameter logic [31:0] RESET_VECTOR = 32'h2000_0000
) (
  input  logic        clock,
  input  logic        reset,
  input  logic        interrupt,

  output logic        if_req_valid,
  input  logic        if_req_ready,
  output logic [31:0] if_req_addr,
  input  logic        if_resp_valid,
  output logic        if_resp_ready,
  input  logic [31:0] if_resp_data,
  input  logic        if_resp_error,

  output logic        d_req_valid,
  input  logic        d_req_ready,
  output logic        d_req_write,
  output logic [31:0] d_req_addr,
  output logic [1:0]  d_req_size,
  output logic [31:0] d_req_wdata,
  output logic [3:0]  d_req_wstrb,
  input  logic        d_resp_valid,
  output logic        d_resp_ready,
  input  logic [31:0] d_resp_rdata,
  input  logic        d_resp_error,

  output logic        debug_commit_valid,
  output logic [31:0] debug_commit_pc,
  output logic [31:0] debug_commit_inst,
  output logic [31:0] debug_commit_npc,
  output logic        debug_commit_rd_wen,
  output logic [4:0]  debug_commit_rd_addr,
  output logic [31:0] debug_commit_rd_data,
  output logic        debug_commit_csr_wen,
  output logic [11:0] debug_commit_csr_addr,
  output logic [31:0] debug_commit_csr_data,
  output logic        debug_trap_valid,
  output logic [31:0] debug_trap_cause,
  output logic [31:0] debug_trap_tval,
  output logic [31:0] debug_gpr_a0
);

  localparam logic [31:0] CAUSE_LOAD_ACCESS_FAULT    = 32'd5;
  localparam logic [31:0] CAUSE_STORE_ACCESS_FAULT   = 32'd7;
  logic [31:0] csr_mtvec;
  logic [31:0] csr_mepc;

  logic [31:0] fetch_pc;
  logic        fetch_pending;
  logic        fetch_drop;
  logic [31:0] fetch_pending_pc;
  logic        fetch_buf_valid;
  logic [31:0] fetch_buf_pc;
  logic [31:0] fetch_buf_inst;
  logic        fetch_buf_error;

  logic        ifid_valid;
  logic [31:0] ifid_pc;
  logic [31:0] ifid_inst;
  logic        ifid_fetch_error;

  logic        idex_valid;
  logic [31:0] idex_pc;
  logic [31:0] idex_inst;
  logic [4:0]  idex_rs1;
  logic [4:0]  idex_rs2;
  logic [4:0]  idex_rd;
  logic [31:0] idex_rs1_data;
  logic [31:0] idex_rs2_data;
  logic [31:0] idex_imm;
  logic [3:0]  idex_alu_op;
  logic        idex_op1_zero;
  logic        idex_op1_pc;
  logic        idex_op2_imm;
  logic        idex_reg_write;
  logic [2:0]  idex_wb_sel;
  logic        idex_mem_read;
  logic        idex_mem_write;
  logic [1:0]  idex_mem_size;
  logic        idex_load_unsigned;
  logic        idex_branch;
  logic [2:0]  idex_branch_funct3;
  logic        idex_jal;
  logic        idex_jalr;
  logic [1:0]  idex_csr_op;
  logic        idex_csr_imm;
  logic [11:0] idex_csr_addr;
  logic        idex_ecall;
  logic        idex_ebreak;
  logic        idex_mret;
  logic        idex_fencei;
  logic        idex_illegal;
  logic        idex_fetch_error;

  logic        exmem_valid;
  logic [31:0] exmem_pc;
  logic [31:0] exmem_inst;
  logic [31:0] exmem_next_pc;
  logic [4:0]  exmem_rd;
  logic        exmem_reg_write;
  logic [31:0] exmem_wb_pre;
  logic        exmem_mem_read;
  logic        exmem_mem_write;
  logic [1:0]  exmem_mem_size;
  logic        exmem_load_unsigned;
  logic [31:0] exmem_mem_addr;
  logic [31:0] exmem_store_data;
  logic        exmem_csr_write;
  logic [11:0] exmem_csr_addr;
  logic [31:0] exmem_csr_wdata;
  logic        exmem_exception;
  logic [31:0] exmem_exception_cause;
  logic [31:0] exmem_exception_tval;

  logic        memwb_valid;
  logic [31:0] memwb_pc;
  logic [31:0] memwb_inst;
  logic [31:0] memwb_next_pc;
  logic [4:0]  memwb_rd;
  logic        memwb_reg_write;
  logic [31:0] memwb_wdata;
  logic        memwb_csr_write;
  logic [11:0] memwb_csr_addr;
  logic [31:0] memwb_csr_wdata;
  logic        memwb_exception;
  logic [31:0] memwb_exception_cause;
  logic [31:0] memwb_exception_tval;

  logic        lsu_request_sent;
  logic        trap_pending;

  logic [2:0]  dec_funct3;
  logic [4:0]  dec_rs1;
  logic [4:0]  dec_rs2;
  logic [4:0]  dec_rd;
  logic [31:0] dec_imm;
  logic [3:0]  dec_alu_op;
  logic        dec_op1_zero;
  logic        dec_op1_pc;
  logic        dec_op2_imm;
  logic        dec_reg_write;
  logic [2:0]  dec_wb_sel;
  logic        dec_mem_read;
  logic        dec_mem_write;
  logic [1:0]  dec_mem_size;
  logic        dec_load_unsigned;
  logic        dec_branch;
  logic        dec_jal;
  logic        dec_jalr;
  logic [1:0]  dec_csr_op;
  logic        dec_csr_imm;
  logic [11:0] dec_csr_addr;
  logic        dec_ecall;
  logic        dec_ebreak;
  logic        dec_mret;
  logic        dec_fencei;
  logic        dec_illegal;
  logic        dec_uses_rs1;
  logic        dec_uses_rs2;
  logic        dec_serializing;

  logic [31:0] dec_rs1_data;
  logic [31:0] dec_rs2_data;
  logic        regfile_write_enable;
  logic [31:0] regfile_debug_a0;
  logic        load_use_hazard;
  logic        serial_hazard;
  logic        id_stall;

  logic [31:0] ex_rs1;
  logic [31:0] ex_rs2;
  logic [31:0] ex_alu_result;
  logic        ex_redirect_raw;
  logic [31:0] ex_redirect_target;
  logic [31:0] ex_next_pc;
  logic [31:0] ex_wb_pre;
  logic [31:0] ex_csr_rdata;
  logic        ex_csr_valid;
  logic        ex_csr_read_only;
  logic        ex_csr_write;
  logic [31:0] ex_csr_wdata;
  logic        ex_exception;
  logic [31:0] ex_exception_cause;
  logic [31:0] ex_exception_tval;

  logic        exmem_mem_op;
  logic        d_req_fire;
  logic        d_resp_fire;
  logic        mem_wait;
  logic        mem_exception_now;
  logic [31:0] mem_load_shifted;
  logic [31:0] mem_load_data;
  logic [31:0] mem_wb_data;

  logic        trap_commit;
  logic        ex_stage_fire;
  logic        ex_exception_start;
  logic        ex_redirect_fire;
  logic        mem_exception_start;
  logic        frontend_kill;
  logic [31:0] frontend_redirect_pc;
  logic        frontend_redirect_valid;
  logic        fetch_buf_pop;
  logic        if_req_fire;
  logic        if_resp_fire;
  logic        ex_mret_fire;

  ysyx_00000000_decode u_decode (
    .inst          (ifid_inst),
    .fetch_error   (ifid_fetch_error),
    .funct3        (dec_funct3),
    .rs1           (dec_rs1),
    .rs2           (dec_rs2),
    .rd            (dec_rd),
    .imm           (dec_imm),
    .alu_op        (dec_alu_op),
    .op1_zero      (dec_op1_zero),
    .op1_pc        (dec_op1_pc),
    .op2_imm       (dec_op2_imm),
    .reg_write     (dec_reg_write),
    .wb_sel        (dec_wb_sel),
    .mem_read      (dec_mem_read),
    .mem_write     (dec_mem_write),
    .mem_size      (dec_mem_size),
    .load_unsigned (dec_load_unsigned),
    .branch        (dec_branch),
    .jal           (dec_jal),
    .jalr          (dec_jalr),
    .csr_op        (dec_csr_op),
    .csr_imm       (dec_csr_imm),
    .csr_addr      (dec_csr_addr),
    .ecall         (dec_ecall),
    .ebreak        (dec_ebreak),
    .mret          (dec_mret),
    .fencei        (dec_fencei),
    .illegal       (dec_illegal),
    .uses_rs1      (dec_uses_rs1),
    .uses_rs2      (dec_uses_rs2),
    .serializing   (dec_serializing)
  );

  assign regfile_write_enable = memwb_valid && !memwb_exception &&
                                memwb_reg_write && (memwb_rd != 5'd0);

  ysyx_00000000_regfile u_regfile (
    .clock        (clock),
    .reset        (reset),
    .read_addr1   (dec_rs1),
    .read_addr2   (dec_rs2),
    .read_data1   (dec_rs1_data),
    .read_data2   (dec_rs2_data),
    .write_enable (regfile_write_enable),
    .write_addr   (memwb_rd),
    .write_data   (memwb_wdata),
    .debug_a0     (regfile_debug_a0)
  );

  ysyx_00000000_csr u_csr (
    .clock        (clock),
    .reset        (reset),
    .interrupt    (interrupt),
    .read_addr    (idex_csr_addr),
    .read_data    (ex_csr_rdata),
    .read_valid   (ex_csr_valid),
    .read_only    (ex_csr_read_only),
    .mtvec        (csr_mtvec),
    .mepc         (csr_mepc),
    .retire_valid (memwb_valid && !memwb_exception),
    .write_enable (memwb_csr_write),
    .write_addr   (memwb_csr_addr),
    .write_data   (memwb_csr_wdata),
    .mret_fire    (ex_mret_fire),
    .trap_valid   (trap_commit),
    .trap_pc      (memwb_pc),
    .trap_cause   (memwb_exception_cause),
    .trap_tval    (memwb_exception_tval)
  );

  assign load_use_hazard = ifid_valid && idex_valid && idex_mem_read &&
                           (idex_rd != 5'd0) &&
                           ((dec_uses_rs1 && (dec_rs1 == idex_rd)) ||
                            (dec_uses_rs2 && (dec_rs2 == idex_rd)));
  assign serial_hazard = ifid_valid && dec_serializing &&
                         (idex_valid || exmem_valid || memwb_valid);
  assign id_stall = load_use_hazard || serial_hazard;

  always_comb begin
    ex_rs1 = (idex_rs1 == 5'd0) ? 32'b0 : idex_rs1_data;
    ex_rs2 = (idex_rs2 == 5'd0) ? 32'b0 : idex_rs2_data;

    if (exmem_valid && !exmem_exception && exmem_reg_write &&
        !exmem_mem_read && (exmem_rd != 5'd0)) begin
      if (idex_rs1 == exmem_rd) ex_rs1 = exmem_wb_pre;
      if (idex_rs2 == exmem_rd) ex_rs2 = exmem_wb_pre;
    end
    if (memwb_valid && !memwb_exception && memwb_reg_write && (memwb_rd != 5'd0)) begin
      if (!(exmem_valid && !exmem_exception && exmem_reg_write &&
            !exmem_mem_read && (exmem_rd != 5'd0) && (idex_rs1 == exmem_rd)) &&
          (idex_rs1 == memwb_rd)) ex_rs1 = memwb_wdata;
      if (!(exmem_valid && !exmem_exception && exmem_reg_write &&
            !exmem_mem_read && (exmem_rd != 5'd0) && (idex_rs2 == exmem_rd)) &&
          (idex_rs2 == memwb_rd)) ex_rs2 = memwb_wdata;
    end
  end

  ysyx_00000000_execute u_execute (
    .pc              (idex_pc),
    .inst            (idex_inst),
    .rs1_addr        (idex_rs1),
    .rs1_data        (ex_rs1),
    .rs2_data        (ex_rs2),
    .imm             (idex_imm),
    .alu_op          (idex_alu_op),
    .op1_zero        (idex_op1_zero),
    .op1_pc          (idex_op1_pc),
    .op2_imm         (idex_op2_imm),
    .wb_sel          (idex_wb_sel),
    .mem_read        (idex_mem_read),
    .mem_write       (idex_mem_write),
    .mem_size        (idex_mem_size),
    .branch          (idex_branch),
    .branch_funct3   (idex_branch_funct3),
    .jal             (idex_jal),
    .jalr            (idex_jalr),
    .csr_op          (idex_csr_op),
    .csr_imm         (idex_csr_imm),
    .ecall           (idex_ecall),
    .ebreak          (idex_ebreak),
    .mret            (idex_mret),
    .fencei          (idex_fencei),
    .illegal         (idex_illegal),
    .fetch_error     (idex_fetch_error),
    .csr_read_data   (ex_csr_rdata),
    .csr_read_valid  (ex_csr_valid),
    .csr_read_only   (ex_csr_read_only),
    .csr_mepc        (csr_mepc),
    .alu_result      (ex_alu_result),
    .redirect        (ex_redirect_raw),
    .redirect_target (ex_redirect_target),
    .next_pc         (ex_next_pc),
    .wb_data         (ex_wb_pre),
    .csr_write       (ex_csr_write),
    .csr_write_data  (ex_csr_wdata),
    .exception       (ex_exception),
    .exception_cause (ex_exception_cause),
    .exception_tval  (ex_exception_tval)
  );

  assign exmem_mem_op = exmem_mem_read || exmem_mem_write;
  assign d_req_valid = exmem_valid && exmem_mem_op && !exmem_exception && !lsu_request_sent;
  assign d_req_write = exmem_mem_write;
  assign d_req_addr = exmem_mem_addr;
  assign d_req_size = exmem_mem_size;
  assign d_req_wdata = exmem_store_data << {exmem_mem_addr[1:0], 3'b000};

  always_comb begin
    unique case (exmem_mem_size)
      2'd0: d_req_wstrb = 4'b0001 << exmem_mem_addr[1:0];
      2'd1: d_req_wstrb = 4'b0011 << exmem_mem_addr[1:0];
      default: d_req_wstrb = 4'b1111;
    endcase
  end

  assign d_req_fire = d_req_valid && d_req_ready;
  assign d_resp_ready = exmem_valid && exmem_mem_op && lsu_request_sent;
  assign d_resp_fire = d_resp_valid && d_resp_ready;
  assign mem_wait = exmem_valid && exmem_mem_op && !d_resp_fire;
  assign mem_exception_now = d_resp_fire && d_resp_error;

  assign mem_load_shifted = d_resp_rdata >> {exmem_mem_addr[1:0], 3'b000};
  always_comb begin
    unique case (exmem_mem_size)
      2'd0: mem_load_data = exmem_load_unsigned
                            ? {24'b0, mem_load_shifted[7:0]}
                            : {{24{mem_load_shifted[7]}}, mem_load_shifted[7:0]};
      2'd1: mem_load_data = exmem_load_unsigned
                            ? {16'b0, mem_load_shifted[15:0]}
                            : {{16{mem_load_shifted[15]}}, mem_load_shifted[15:0]};
      default: mem_load_data = mem_load_shifted;
    endcase
  end
  assign mem_wb_data = exmem_mem_read ? mem_load_data : exmem_wb_pre;

  assign trap_commit = memwb_valid && memwb_exception;
  assign ex_stage_fire = idex_valid && !mem_wait && !mem_exception_now && !trap_commit;
  assign ex_exception_start = ex_stage_fire && ex_exception;
  assign ex_redirect_fire = ex_stage_fire && !ex_exception && ex_redirect_raw;
  assign mem_exception_start = mem_exception_now && exmem_valid;
  assign ex_mret_fire = ex_redirect_fire && idex_mret;

  always_comb begin
    frontend_redirect_valid = 1'b0;
    frontend_redirect_pc = fetch_pc;
    if (trap_commit) begin
      frontend_redirect_valid = 1'b1;
      frontend_redirect_pc = {csr_mtvec[31:2], 2'b00};
    end else if (ex_redirect_fire) begin
      frontend_redirect_valid = 1'b1;
      frontend_redirect_pc = ex_redirect_target;
    end
  end
  assign frontend_kill = trap_commit || mem_exception_start ||
                         ex_exception_start || ex_redirect_fire;

  assign if_req_valid = !reset && !fetch_pending && !fetch_buf_valid &&
                        !trap_pending && !frontend_kill;
  assign if_req_addr = fetch_pc;
  assign if_req_fire = if_req_valid && if_req_ready;
  assign if_resp_ready = fetch_pending;
  assign if_resp_fire = if_resp_valid && if_resp_ready;

  assign fetch_buf_pop = fetch_buf_valid && !mem_wait && !mem_exception_now &&
                         !trap_commit && !trap_pending && !ex_exception_start &&
                         !ex_redirect_fire && !id_stall;

  always_ff @(posedge clock) begin
    if (reset) begin
      fetch_pc         <= RESET_VECTOR;
      fetch_pending    <= 1'b0;
      fetch_drop       <= 1'b0;
      fetch_pending_pc <= 32'b0;
      fetch_buf_valid  <= 1'b0;
      fetch_buf_pc     <= 32'b0;
      fetch_buf_inst   <= 32'b0;
      fetch_buf_error  <= 1'b0;
    end else begin
      if (fetch_buf_pop) fetch_buf_valid <= 1'b0;

      if (if_req_fire) begin
        fetch_pending    <= 1'b1;
        fetch_pending_pc <= fetch_pc;
        fetch_pc         <= fetch_pc + 32'd4;
      end

      if (if_resp_fire) begin
        fetch_pending <= 1'b0;
        if (!fetch_drop && !frontend_kill) begin
          fetch_buf_valid <= 1'b1;
          fetch_buf_pc    <= fetch_pending_pc;
          fetch_buf_inst  <= if_resp_data;
          fetch_buf_error <= if_resp_error;
        end
        fetch_drop <= 1'b0;
      end

      if (frontend_kill) begin
        fetch_buf_valid <= 1'b0;
        if (fetch_pending && !if_resp_fire) fetch_drop <= 1'b1;
        else fetch_drop <= 1'b0;
        if (frontend_redirect_valid) fetch_pc <= frontend_redirect_pc;
      end
    end
  end

  always_ff @(posedge clock) begin
    if (reset) begin
      ifid_valid       <= 1'b0;
      idex_valid       <= 1'b0;
      exmem_valid      <= 1'b0;
      memwb_valid      <= 1'b0;
      lsu_request_sent <= 1'b0;
      trap_pending     <= 1'b0;
    end else begin
      memwb_valid <= 1'b0;

      if (d_req_fire) lsu_request_sent <= 1'b1;

      if (trap_commit) begin
        ifid_valid       <= 1'b0;
        idex_valid       <= 1'b0;
        exmem_valid      <= 1'b0;
        memwb_valid      <= 1'b0;
        lsu_request_sent <= 1'b0;
        trap_pending     <= 1'b0;
      end else if (mem_exception_start) begin
        memwb_valid             <= 1'b1;
        memwb_pc                <= exmem_pc;
        memwb_inst              <= exmem_inst;
        memwb_next_pc           <= csr_mtvec & 32'hffff_fffc;
        memwb_rd                <= 5'b0;
        memwb_reg_write         <= 1'b0;
        memwb_wdata             <= 32'b0;
        memwb_csr_write         <= 1'b0;
        memwb_csr_addr          <= 12'b0;
        memwb_csr_wdata         <= 32'b0;
        memwb_exception         <= 1'b1;
        memwb_exception_cause   <= exmem_mem_read ? CAUSE_LOAD_ACCESS_FAULT
                                                  : CAUSE_STORE_ACCESS_FAULT;
        memwb_exception_tval    <= exmem_mem_addr;
        exmem_valid             <= 1'b0;
        idex_valid              <= 1'b0;
        ifid_valid              <= 1'b0;
        lsu_request_sent        <= 1'b0;
        trap_pending            <= 1'b1;
      end else if (mem_wait) begin
        // All younger state is held while the oldest memory operation waits.
      end else begin
        lsu_request_sent <= 1'b0;

        memwb_valid           <= exmem_valid;
        memwb_pc              <= exmem_pc;
        memwb_inst            <= exmem_inst;
        memwb_next_pc         <= exmem_next_pc;
        memwb_rd              <= exmem_rd;
        memwb_reg_write       <= exmem_reg_write && !exmem_exception;
        memwb_wdata           <= mem_wb_data;
        memwb_csr_write       <= exmem_csr_write && !exmem_exception;
        memwb_csr_addr        <= exmem_csr_addr;
        memwb_csr_wdata       <= exmem_csr_wdata;
        memwb_exception       <= exmem_exception;
        memwb_exception_cause <= exmem_exception_cause;
        memwb_exception_tval  <= exmem_exception_tval;

        if (idex_valid) begin
          exmem_valid           <= 1'b1;
          exmem_pc              <= idex_pc;
          exmem_inst            <= idex_inst;
          exmem_next_pc         <= ex_exception ? (csr_mtvec & 32'hffff_fffc) : ex_next_pc;
          exmem_rd              <= idex_rd;
          exmem_reg_write       <= idex_reg_write && !ex_exception;
          exmem_wb_pre          <= ex_wb_pre;
          exmem_mem_read        <= idex_mem_read && !ex_exception;
          exmem_mem_write       <= idex_mem_write && !ex_exception;
          exmem_mem_size        <= idex_mem_size;
          exmem_load_unsigned   <= idex_load_unsigned;
          exmem_mem_addr        <= ex_alu_result;
          exmem_store_data      <= ex_rs2;
          exmem_csr_write       <= ex_csr_write && !ex_exception;
          exmem_csr_addr        <= idex_csr_addr;
          exmem_csr_wdata       <= ex_csr_wdata;
          exmem_exception       <= ex_exception;
          exmem_exception_cause <= ex_exception_cause;
          exmem_exception_tval  <= ex_exception_tval;
        end else begin
          exmem_valid <= 1'b0;
        end

        if (ex_exception_start) begin
          idex_valid   <= 1'b0;
          ifid_valid   <= 1'b0;
          trap_pending <= 1'b1;
        end else if (ex_redirect_fire) begin
          idex_valid <= 1'b0;
          ifid_valid <= 1'b0;
        end else if (trap_pending) begin
          idex_valid <= 1'b0;
          ifid_valid <= 1'b0;
        end else if (id_stall) begin
          idex_valid <= 1'b0;
        end else begin
          idex_valid          <= ifid_valid;
          idex_pc             <= ifid_pc;
          idex_inst           <= ifid_inst;
          idex_rs1            <= dec_rs1;
          idex_rs2            <= dec_rs2;
          idex_rd             <= dec_rd;
          idex_rs1_data       <= dec_rs1_data;
          idex_rs2_data       <= dec_rs2_data;
          idex_imm            <= dec_imm;
          idex_alu_op         <= dec_alu_op;
          idex_op1_zero       <= dec_op1_zero;
          idex_op1_pc         <= dec_op1_pc;
          idex_op2_imm        <= dec_op2_imm;
          idex_reg_write      <= dec_reg_write;
          idex_wb_sel         <= dec_wb_sel;
          idex_mem_read       <= dec_mem_read;
          idex_mem_write      <= dec_mem_write;
          idex_mem_size       <= dec_mem_size;
          idex_load_unsigned  <= dec_load_unsigned;
          idex_branch         <= dec_branch;
          idex_branch_funct3  <= dec_funct3;
          idex_jal            <= dec_jal;
          idex_jalr           <= dec_jalr;
          idex_csr_op         <= dec_csr_op;
          idex_csr_imm        <= dec_csr_imm;
          idex_csr_addr       <= dec_csr_addr;
          idex_ecall          <= dec_ecall;
          idex_ebreak         <= dec_ebreak;
          idex_mret           <= dec_mret;
          idex_fencei         <= dec_fencei;
          idex_illegal        <= dec_illegal;
          idex_fetch_error    <= ifid_fetch_error;

          if (fetch_buf_pop) begin
            ifid_valid       <= 1'b1;
            ifid_pc          <= fetch_buf_pc;
            ifid_inst        <= fetch_buf_inst;
            ifid_fetch_error <= fetch_buf_error;
          end else begin
            ifid_valid <= 1'b0;
          end
        end
      end
    end
  end

  assign debug_commit_valid   = memwb_valid;
  assign debug_commit_pc      = memwb_pc;
  assign debug_commit_inst    = memwb_inst;
  assign debug_commit_npc     = memwb_next_pc;
  assign debug_commit_rd_wen  = memwb_valid && !memwb_exception &&
                                memwb_reg_write && (memwb_rd != 5'd0);
  assign debug_commit_rd_addr = memwb_rd;
  assign debug_commit_rd_data = memwb_wdata;
  assign debug_commit_csr_wen = memwb_valid && !memwb_exception &&
                                memwb_csr_write;
  assign debug_commit_csr_addr = memwb_csr_addr;
  assign debug_commit_csr_data = memwb_csr_wdata;
  assign debug_trap_valid     = trap_commit;
  assign debug_trap_cause     = memwb_exception_cause;
  assign debug_trap_tval      = memwb_exception_tval;
  assign debug_gpr_a0         = regfile_debug_a0;

endmodule
