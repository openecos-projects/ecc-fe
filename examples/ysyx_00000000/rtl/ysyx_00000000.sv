`timescale 1ns/1ps

module ysyx_00000000 #(
  parameter logic [31:0] RESET_VECTOR = 32'h2000_0000,
  parameter bit LEGACY_RTC = 1'b0
) (
  input  logic        clock,
  input  logic        reset,
  input  logic        io_interrupt,

  input  logic        io_master_awready,
  output logic        io_master_awvalid,
  output logic [31:0] io_master_awaddr,
  output logic [3:0]  io_master_awid,
  output logic [7:0]  io_master_awlen,
  output logic [2:0]  io_master_awsize,
  output logic [1:0]  io_master_awburst,
  input  logic        io_master_wready,
  output logic        io_master_wvalid,
  output logic [31:0] io_master_wdata,
  output logic [3:0]  io_master_wstrb,
  output logic        io_master_wlast,
  output logic        io_master_bready,
  input  logic        io_master_bvalid,
  input  logic [1:0]  io_master_bresp,
  input  logic [3:0]  io_master_bid,
  input  logic        io_master_arready,
  output logic        io_master_arvalid,
  output logic [31:0] io_master_araddr,
  output logic [3:0]  io_master_arid,
  output logic [7:0]  io_master_arlen,
  output logic [2:0]  io_master_arsize,
  output logic [1:0]  io_master_arburst,
  output logic        io_master_rready,
  input  logic        io_master_rvalid,
  input  logic [1:0]  io_master_rresp,
  input  logic [31:0] io_master_rdata,
  input  logic        io_master_rlast,
  input  logic [3:0]  io_master_rid,

  output logic        io_slave_awready,
  input  logic        io_slave_awvalid,
  input  logic [31:0] io_slave_awaddr,
  input  logic [3:0]  io_slave_awid,
  input  logic [7:0]  io_slave_awlen,
  input  logic [2:0]  io_slave_awsize,
  input  logic [1:0]  io_slave_awburst,
  output logic        io_slave_wready,
  input  logic        io_slave_wvalid,
  input  logic [31:0] io_slave_wdata,
  input  logic [3:0]  io_slave_wstrb,
  input  logic        io_slave_wlast,
  input  logic        io_slave_bready,
  output logic        io_slave_bvalid,
  output logic [1:0]  io_slave_bresp,
  output logic [3:0]  io_slave_bid,
  output logic        io_slave_arready,
  input  logic        io_slave_arvalid,
  input  logic [31:0] io_slave_araddr,
  input  logic [3:0]  io_slave_arid,
  input  logic [7:0]  io_slave_arlen,
  input  logic [2:0]  io_slave_arsize,
  input  logic [1:0]  io_slave_arburst,
  input  logic        io_slave_rready,
  output logic        io_slave_rvalid,
  output logic [1:0]  io_slave_rresp,
  output logic [31:0] io_slave_rdata,
  output logic        io_slave_rlast,
  output logic [3:0]  io_slave_rid
`ifdef NPC_SIM
  ,
  output logic        debug_commit_valid,
  output logic [31:0] debug_commit_pc,
  output logic [31:0] debug_commit_inst,
  output logic [31:0] debug_commit_npc,
  output logic        debug_commit_rd_wen,
  output logic [4:0]  debug_commit_rd_addr,
  output logic [31:0] debug_commit_rd_data,
  output logic        debug_trap_valid,
  output logic [31:0] debug_trap_cause,
  output logic [31:0] debug_trap_tval,
  output logic [31:0] debug_gpr_a0
`endif
);

  logic        if_req_valid;
  logic        if_req_ready;
  logic [31:0] if_req_addr;
  logic        if_resp_valid;
  logic        if_resp_ready;
  logic [31:0] if_resp_data;
  logic        if_resp_error;
  logic        d_req_valid;
  logic        d_req_ready;
  logic        d_req_write;
  logic [31:0] d_req_addr;
  logic [1:0]  d_req_size;
  logic [31:0] d_req_wdata;
  logic [3:0]  d_req_wstrb;
  logic        d_resp_valid;
  logic        d_resp_ready;
  logic [31:0] d_resp_rdata;
  logic        d_resp_error;

  logic        core_debug_commit_valid;
  logic [31:0] core_debug_commit_pc;
  logic [31:0] core_debug_commit_inst;
  logic [31:0] core_debug_commit_npc;
  logic        core_debug_commit_rd_wen;
  logic [4:0]  core_debug_commit_rd_addr;
  logic [31:0] core_debug_commit_rd_data;
  logic        core_debug_commit_csr_wen;
  logic [11:0] core_debug_commit_csr_addr;
  logic [31:0] core_debug_commit_csr_data;
  logic        core_debug_trap_valid;
  logic [31:0] core_debug_trap_cause;
  logic [31:0] core_debug_trap_tval;
  logic [31:0] core_debug_gpr_a0;

  logic unused_slave_inputs;
  assign unused_slave_inputs = ^{io_slave_awvalid, io_slave_awaddr, io_slave_awid,
                                 io_slave_awlen, io_slave_awsize, io_slave_awburst,
                                 io_slave_wvalid, io_slave_wdata, io_slave_wstrb,
                                 io_slave_wlast, io_slave_bready, io_slave_arvalid,
                                 io_slave_araddr, io_slave_arid, io_slave_arlen,
                                 io_slave_arsize, io_slave_arburst, io_slave_rready};

  assign io_slave_awready = 1'b0;
  assign io_slave_wready  = 1'b0;
  assign io_slave_bvalid  = 1'b0;
  assign io_slave_bresp   = 2'b00;
  assign io_slave_bid     = 4'b0;
  assign io_slave_arready = 1'b0;
  assign io_slave_rvalid  = 1'b0;
  assign io_slave_rresp   = 2'b00;
  assign io_slave_rdata   = 32'b0;
  assign io_slave_rlast   = 1'b0;
  assign io_slave_rid     = 4'b0;

  ysyx_00000000_core #(
    .RESET_VECTOR (RESET_VECTOR)
  ) core (
    .clock                (clock),
    .reset                (reset),
    .interrupt            (io_interrupt),
    .if_req_valid         (if_req_valid),
    .if_req_ready         (if_req_ready),
    .if_req_addr          (if_req_addr),
    .if_resp_valid        (if_resp_valid),
    .if_resp_ready        (if_resp_ready),
    .if_resp_data         (if_resp_data),
    .if_resp_error        (if_resp_error),
    .d_req_valid          (d_req_valid),
    .d_req_ready          (d_req_ready),
    .d_req_write          (d_req_write),
    .d_req_addr           (d_req_addr),
    .d_req_size           (d_req_size),
    .d_req_wdata          (d_req_wdata),
    .d_req_wstrb          (d_req_wstrb),
    .d_resp_valid         (d_resp_valid),
    .d_resp_ready         (d_resp_ready),
    .d_resp_rdata         (d_resp_rdata),
    .d_resp_error         (d_resp_error),
    .debug_commit_valid   (core_debug_commit_valid),
    .debug_commit_pc      (core_debug_commit_pc),
    .debug_commit_inst    (core_debug_commit_inst),
    .debug_commit_npc     (core_debug_commit_npc),
    .debug_commit_rd_wen  (core_debug_commit_rd_wen),
    .debug_commit_rd_addr (core_debug_commit_rd_addr),
    .debug_commit_rd_data (core_debug_commit_rd_data),
    .debug_commit_csr_wen (core_debug_commit_csr_wen),
    .debug_commit_csr_addr(core_debug_commit_csr_addr),
    .debug_commit_csr_data(core_debug_commit_csr_data),
    .debug_trap_valid     (core_debug_trap_valid),
    .debug_trap_cause     (core_debug_trap_cause),
    .debug_trap_tval      (core_debug_trap_tval),
    .debug_gpr_a0         (core_debug_gpr_a0)
  );

  ysyx_00000000_axi #(
    .LEGACY_RTC (LEGACY_RTC)
  ) axi (
    .clock              (clock),
    .reset              (reset),
    .if_req_valid       (if_req_valid),
    .if_req_ready       (if_req_ready),
    .if_req_addr        (if_req_addr),
    .if_resp_valid      (if_resp_valid),
    .if_resp_ready      (if_resp_ready),
    .if_resp_data       (if_resp_data),
    .if_resp_error      (if_resp_error),
    .d_req_valid        (d_req_valid),
    .d_req_ready        (d_req_ready),
    .d_req_write        (d_req_write),
    .d_req_addr         (d_req_addr),
    .d_req_size         (d_req_size),
    .d_req_wdata        (d_req_wdata),
    .d_req_wstrb        (d_req_wstrb),
    .d_resp_valid       (d_resp_valid),
    .d_resp_ready       (d_resp_ready),
    .d_resp_rdata       (d_resp_rdata),
    .d_resp_error       (d_resp_error),
    .io_master_awready  (io_master_awready),
    .io_master_awvalid  (io_master_awvalid),
    .io_master_awaddr   (io_master_awaddr),
    .io_master_awid     (io_master_awid),
    .io_master_awlen    (io_master_awlen),
    .io_master_awsize   (io_master_awsize),
    .io_master_awburst  (io_master_awburst),
    .io_master_wready   (io_master_wready),
    .io_master_wvalid   (io_master_wvalid),
    .io_master_wdata    (io_master_wdata),
    .io_master_wstrb    (io_master_wstrb),
    .io_master_wlast    (io_master_wlast),
    .io_master_bready   (io_master_bready),
    .io_master_bvalid   (io_master_bvalid),
    .io_master_bresp    (io_master_bresp),
    .io_master_bid      (io_master_bid),
    .io_master_arready  (io_master_arready),
    .io_master_arvalid  (io_master_arvalid),
    .io_master_araddr   (io_master_araddr),
    .io_master_arid     (io_master_arid),
    .io_master_arlen    (io_master_arlen),
    .io_master_arsize   (io_master_arsize),
    .io_master_arburst  (io_master_arburst),
    .io_master_rready   (io_master_rready),
    .io_master_rvalid   (io_master_rvalid),
    .io_master_rresp    (io_master_rresp),
    .io_master_rdata    (io_master_rdata),
    .io_master_rlast    (io_master_rlast),
    .io_master_rid      (io_master_rid)
  );

`ifdef ECOS_DIFFTEST
  ysyx_00000000_difftest difftest_adapter (
    .clock          (clock),
    .reset          (reset),
    .commit_valid   (core_debug_commit_valid),
    .commit_pc      (core_debug_commit_pc),
    .commit_npc     (core_debug_commit_npc),
    .commit_inst    (core_debug_commit_inst),
    .commit_rd_wen  (core_debug_commit_rd_wen),
    .commit_rd_addr (core_debug_commit_rd_addr),
    .commit_rd_data (core_debug_commit_rd_data),
    .commit_csr_wen (core_debug_commit_csr_wen),
    .commit_csr_addr(core_debug_commit_csr_addr),
    .commit_csr_data(core_debug_commit_csr_data)
  );
`endif

`ifndef ECOS_DIFFTEST
  logic unused_difftest_signals;
  assign unused_difftest_signals = ^{core_debug_commit_csr_wen,
                                     core_debug_commit_csr_addr,
                                     core_debug_commit_csr_data};
`endif

`ifdef NPC_SIM
  assign debug_commit_valid   = core_debug_commit_valid;
  assign debug_commit_pc      = core_debug_commit_pc;
  assign debug_commit_inst    = core_debug_commit_inst;
  assign debug_commit_npc     = core_debug_commit_npc;
  assign debug_commit_rd_wen  = core_debug_commit_rd_wen;
  assign debug_commit_rd_addr = core_debug_commit_rd_addr;
  assign debug_commit_rd_data = core_debug_commit_rd_data;
  assign debug_trap_valid     = core_debug_trap_valid;
  assign debug_trap_cause     = core_debug_trap_cause;
  assign debug_trap_tval      = core_debug_trap_tval;
  assign debug_gpr_a0         = core_debug_gpr_a0;
`endif

endmodule
