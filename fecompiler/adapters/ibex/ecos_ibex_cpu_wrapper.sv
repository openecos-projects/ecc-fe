// ECOS Ibex adapter for the ysyx AXI CPU socket.
//
// Ibex exposes independent instruction and data memory request interfaces.
// This wrapper keeps upstream Ibex RTL unmodified, adapts those native ports to
// the stable ECOS/YSYX AXI-like CPU socket, and handles ECOS UART/HALT MMIO.

module ecos_ibex_cpu_wrapper (
  input         clock,
  input         reset,
  input         io_interrupt,
  input         io_master_awready,
  output        io_master_awvalid,
  output [31:0] io_master_awaddr,
  output [3:0]  io_master_awid,
  output [7:0]  io_master_awlen,
  output [2:0]  io_master_awsize,
  output [1:0]  io_master_awburst,
  output        io_master_awlock,
  output [3:0]  io_master_awcache,
  output [2:0]  io_master_awprot,
  output [3:0]  io_master_awqos,
  output [3:0]  io_master_awregion,
  input         io_master_wready,
  output        io_master_wvalid,
  output [31:0] io_master_wdata,
  output [3:0]  io_master_wstrb,
  output        io_master_wlast,
  output        io_master_bready,
  input         io_master_bvalid,
  input  [1:0]  io_master_bresp,
  input  [3:0]  io_master_bid,
  input         io_master_arready,
  output        io_master_arvalid,
  output [31:0] io_master_araddr,
  output [3:0]  io_master_arid,
  output [7:0]  io_master_arlen,
  output [2:0]  io_master_arsize,
  output [1:0]  io_master_arburst,
  output        io_master_arlock,
  output [3:0]  io_master_arcache,
  output [2:0]  io_master_arprot,
  output [3:0]  io_master_arqos,
  output [3:0]  io_master_arregion,
  output        io_master_rready,
  input         io_master_rvalid,
  input  [1:0]  io_master_rresp,
  input  [31:0] io_master_rdata,
  input         io_master_rlast,
  input  [3:0]  io_master_rid,
  output        io_slave_awready,
  input         io_slave_awvalid,
  input  [31:0] io_slave_awaddr,
  input  [3:0]  io_slave_awid,
  input  [7:0]  io_slave_awlen,
  input  [2:0]  io_slave_awsize,
  input  [1:0]  io_slave_awburst,
  input         io_slave_awlock,
  input  [3:0]  io_slave_awcache,
  input  [2:0]  io_slave_awprot,
  input  [3:0]  io_slave_awqos,
  input  [3:0]  io_slave_awregion,
  output        io_slave_wready,
  input         io_slave_wvalid,
  input  [31:0] io_slave_wdata,
  input  [3:0]  io_slave_wstrb,
  input         io_slave_wlast,
  input         io_slave_bready,
  output        io_slave_bvalid,
  output [1:0]  io_slave_bresp,
  output [3:0]  io_slave_bid,
  output        io_slave_arready,
  input         io_slave_arvalid,
  input  [31:0] io_slave_araddr,
  input  [3:0]  io_slave_arid,
  input  [7:0]  io_slave_arlen,
  input  [2:0]  io_slave_arsize,
  input  [1:0]  io_slave_arburst,
  input         io_slave_arlock,
  input  [3:0]  io_slave_arcache,
  input  [2:0]  io_slave_arprot,
  input  [3:0]  io_slave_arqos,
  input  [3:0]  io_slave_arregion,
  input         io_slave_rready,
  output        io_slave_rvalid,
  output [1:0]  io_slave_rresp,
  output [31:0] io_slave_rdata,
  output        io_slave_rlast,
  output [3:0]  io_slave_rid
);

  import ibex_pkg::*;

  localparam [31:0] RESET_PC = 32'h2000_0000;
  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;
  localparam [31:0] BOOT_ALIAS_BASE = 32'h2000_0000;
  localparam [31:0] BOOT_ALIAS_SIZE = 32'h0010_0000;
  localparam [31:0] IBEX_BOOT_OFFSET = 32'h0000_0080;

  localparam [2:0] ST_IDLE = 3'd0;
  localparam [2:0] ST_READ_ADDR = 3'd1;
  localparam [2:0] ST_READ_DATA = 3'd2;
  localparam [2:0] ST_WRITE_ADDR_DATA = 3'd3;
  localparam [2:0] ST_WRITE_RESP = 3'd4;

  reg [2:0]  state_q;
  reg        serving_data_q;
  reg [31:0] axi_addr_q;
  reg [31:0] axi_wdata_q;
  reg [3:0]  axi_wstrb_q;
  reg        aw_done_q;
  reg        w_done_q;

  wire        instr_req;
  wire        instr_gnt;
  reg         instr_rvalid_q;
  wire [31:0] instr_addr;
  reg  [31:0] instr_rdata_q;
  wire        data_req;
  wire        data_gnt;
  reg         data_rvalid_q;
  wire        data_we;
  wire [3:0]  data_be;
  wire [31:0] data_addr;
  wire [31:0] data_wdata;
  reg  [31:0] data_rdata_q;
  wire        irq_pending;
  wire        alert_minor;
  wire        alert_major_internal;
  wire        alert_major_bus;
  wire        double_fault_seen;
  ibex_mubi_t core_busy;
  crash_dump_t crash_dump;

  wire        dummy_instr_id;
  wire        dummy_instr_wb;
  wire [4:0]  rf_raddr_a;
  wire [4:0]  rf_raddr_b;
  wire [4:0]  rf_waddr_wb;
  wire        rf_we_wb;
  wire [31:0] rf_wdata_wb;
  wire [31:0] rf_rdata_a;
  wire [31:0] rf_rdata_b;

  wire [IC_NUM_WAYS-1:0] ic_tag_req;
  wire                   ic_tag_write;
  wire [IC_INDEX_W-1:0]  ic_tag_addr;
  wire [IC_TAG_SIZE-1:0] ic_tag_wdata;
  wire [IC_TAG_SIZE-1:0] ic_tag_rdata [IC_NUM_WAYS];
  wire [IC_NUM_WAYS-1:0] ic_data_req;
  wire                   ic_data_write;
  wire [IC_INDEX_W-1:0]  ic_data_addr;
  wire [IC_LINE_SIZE-1:0] ic_data_wdata;
  wire [IC_LINE_SIZE-1:0] ic_data_rdata [IC_NUM_WAYS];
  wire                   ic_scr_key_req;

  wire data_write_req = data_req && data_we;
  wire local_uart_write =
      (state_q == ST_IDLE) && data_write_req && (data_addr == UART_ADDR);
  wire local_halt_write =
      (state_q == ST_IDLE) && data_write_req && (data_addr == HALT_ADDR);
  wire local_write = local_uart_write || local_halt_write;
  wire aw_fire = io_master_awvalid && io_master_awready;
  wire w_fire = io_master_wvalid && io_master_wready;
  wire aw_done_next = aw_done_q || aw_fire;
  wire w_done_next = w_done_q || w_fire;

  function [7:0] wstrb_byte;
    input [31:0] data;
    input [3:0] strb;
    begin
      casez (strb)
        4'b???1: wstrb_byte = data[7:0];
        4'b??10: wstrb_byte = data[15:8];
        4'b?100: wstrb_byte = data[23:16];
        4'b1000: wstrb_byte = data[31:24];
        default: wstrb_byte = data[7:0];
      endcase
    end
  endfunction

  function [31:0] ecos_mem_addr;
    input [31:0] addr;
    begin
      if ((addr >= (BOOT_ALIAS_BASE + IBEX_BOOT_OFFSET)) &&
          (addr < (BOOT_ALIAS_BASE + BOOT_ALIAS_SIZE + IBEX_BOOT_OFFSET))) begin
        ecos_mem_addr = addr - IBEX_BOOT_OFFSET;
      end else begin
        ecos_mem_addr = addr;
      end
    end
  endfunction

  ibex_core #(
    .PMPEnable        (1'b0),
    .RV32E            (1'b0),
    .RV32M            (RV32MFast),
    .RV32B            (RV32BNone),
    .RV32ZC           (RV32ZcaZcbZcmp),
    .RegFileECC       (1'b0),
    .MemECC           (1'b0),
    .BranchTargetALU  (1'b0),
    .WritebackStage   (1'b0),
    .ICache           (1'b0),
    .BranchPredictor  (1'b0),
    .DbgTriggerEn     (1'b0),
    .SecureIbex       (1'b0),
    .DummyInstructions(1'b0)
  ) u_ibex_core (
    .clk_i                  (clock),
    .rst_ni                 (~reset),
    .hart_id_i              (32'b0),
    .boot_addr_i            (RESET_PC),
    .instr_req_o            (instr_req),
    .instr_gnt_i            (instr_gnt),
    .instr_rvalid_i         (instr_rvalid_q),
    .instr_addr_o           (instr_addr),
    .instr_rdata_i          (instr_rdata_q),
    .instr_err_i            (1'b0),
    .data_req_o             (data_req),
    .data_gnt_i             (data_gnt),
    .data_rvalid_i          (data_rvalid_q || local_write),
    .data_we_o              (data_we),
    .data_be_o              (data_be),
    .data_addr_o            (data_addr),
    .data_wdata_o           (data_wdata),
    .data_rdata_i           (data_rdata_q),
    .data_err_i             (1'b0),
    .dummy_instr_id_o       (dummy_instr_id),
    .dummy_instr_wb_o       (dummy_instr_wb),
    .rf_raddr_a_o           (rf_raddr_a),
    .rf_raddr_b_o           (rf_raddr_b),
    .rf_waddr_wb_o          (rf_waddr_wb),
    .rf_we_wb_o             (rf_we_wb),
    .rf_wdata_wb_ecc_o      (rf_wdata_wb),
    .rf_rdata_a_ecc_i       (rf_rdata_a),
    .rf_rdata_b_ecc_i       (rf_rdata_b),
    .ic_tag_req_o           (ic_tag_req),
    .ic_tag_write_o         (ic_tag_write),
    .ic_tag_addr_o          (ic_tag_addr),
    .ic_tag_wdata_o         (ic_tag_wdata),
    .ic_tag_rdata_i         (ic_tag_rdata),
    .ic_data_req_o          (ic_data_req),
    .ic_data_write_o        (ic_data_write),
    .ic_data_addr_o         (ic_data_addr),
    .ic_data_wdata_o        (ic_data_wdata),
    .ic_data_rdata_i        (ic_data_rdata),
    .ic_scr_key_valid_i     (1'b0),
    .ic_scr_key_req_o       (ic_scr_key_req),
    .irq_software_i         (1'b0),
    .irq_timer_i            (1'b0),
    .irq_external_i         (io_interrupt),
    .irq_fast_i             (15'b0),
    .irq_nm_i               (1'b0),
    .irq_pending_o          (irq_pending),
    .debug_req_i            (1'b0),
    .crash_dump_o           (crash_dump),
    .double_fault_seen_o    (double_fault_seen),
    .fetch_enable_i         (IbexMuBiOn),
    .mcounteren_writable_i  (IbexMuBiOn),
    .alert_minor_o          (alert_minor),
    .alert_major_internal_o (alert_major_internal),
    .alert_major_bus_o      (alert_major_bus),
    .core_busy_o            (core_busy)
  );

  ibex_register_file_ff #(
    .RV32E            (1'b0),
    .DataWidth        (32),
    .DummyInstructions(1'b0)
  ) register_file (
    .clk_i           (clock),
    .rst_ni          (~reset),
    .test_en_i       (1'b0),
    .dummy_instr_id_i(dummy_instr_id),
    .dummy_instr_wb_i(dummy_instr_wb),
    .raddr_a_i       (rf_raddr_a),
    .rdata_a_o       (rf_rdata_a),
    .raddr_b_i       (rf_raddr_b),
    .rdata_b_o       (rf_rdata_b),
    .waddr_a_i       (rf_waddr_wb),
    .wdata_a_i       (rf_wdata_wb),
    .we_a_i          (rf_we_wb)
  );

  for (genvar way = 0; way < IC_NUM_WAYS; way++) begin : g_unused_icache_rams
    assign ic_tag_rdata[way] = '0;
    assign ic_data_rdata[way] = '0;
  end

  assign data_gnt = (state_q == ST_IDLE) && data_req;
  assign instr_gnt = (state_q == ST_IDLE) && !data_req && instr_req;

  assign io_master_awvalid = (state_q == ST_WRITE_ADDR_DATA) && !aw_done_q;
  assign io_master_awaddr = axi_addr_q;
  assign io_master_awid = 4'b0010;
  assign io_master_awlen = 8'b0000_0000;
  assign io_master_awsize = 3'b010;
  assign io_master_awburst = 2'b01;
  assign io_master_awlock = 1'b0;
  assign io_master_awcache = 4'b0000;
  assign io_master_awprot = 3'b000;
  assign io_master_awqos = 4'b0000;
  assign io_master_awregion = 4'b0000;

  assign io_master_wvalid = (state_q == ST_WRITE_ADDR_DATA) && !w_done_q;
  assign io_master_wdata = axi_wdata_q;
  assign io_master_wstrb = axi_wstrb_q;
  assign io_master_wlast = 1'b1;
  assign io_master_bready = (state_q == ST_WRITE_RESP);

  assign io_master_arvalid = (state_q == ST_READ_ADDR);
  assign io_master_araddr = axi_addr_q;
  assign io_master_arid = serving_data_q ? 4'b0010 : 4'b0000;
  assign io_master_arlen = 8'b0000_0000;
  assign io_master_arsize = 3'b010;
  assign io_master_arburst = 2'b01;
  assign io_master_arlock = 1'b0;
  assign io_master_arcache = 4'b0000;
  assign io_master_arprot = 3'b000;
  assign io_master_arqos = 4'b0000;
  assign io_master_arregion = 4'b0000;
  assign io_master_rready = (state_q == ST_READ_DATA);

  assign io_slave_awready = 1'b0;
  assign io_slave_wready = 1'b0;
  assign io_slave_bvalid = 1'b0;
  assign io_slave_bresp = 2'b00;
  assign io_slave_bid = 4'b0000;
  assign io_slave_arready = 1'b0;
  assign io_slave_rvalid = 1'b0;
  assign io_slave_rresp = 2'b00;
  assign io_slave_rdata = 32'b0;
  assign io_slave_rlast = 1'b0;
  assign io_slave_rid = 4'b0000;

  always @(posedge clock) begin
    if (reset) begin
      state_q <= ST_IDLE;
      serving_data_q <= 1'b0;
      axi_addr_q <= 32'b0;
      axi_wdata_q <= 32'b0;
      axi_wstrb_q <= 4'b0000;
      aw_done_q <= 1'b0;
      w_done_q <= 1'b0;
      instr_rvalid_q <= 1'b0;
      instr_rdata_q <= 32'b0;
      data_rvalid_q <= 1'b0;
      data_rdata_q <= 32'b0;
    end else begin
      instr_rvalid_q <= 1'b0;
      data_rvalid_q <= 1'b0;

      if (local_uart_write) begin
`ifndef SYNTHESIS
        $write("%c", wstrb_byte(data_wdata, data_be));
        $fflush();
`endif
      end

      if (local_halt_write) begin
`ifndef SYNTHESIS
        if (data_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", data_wdata);
        end
`endif
      end

      case (state_q)
        ST_IDLE: begin
          aw_done_q <= 1'b0;
          w_done_q <= 1'b0;
          if (!local_write) begin
            if (data_req) begin
              serving_data_q <= 1'b1;
              axi_addr_q <= {ecos_mem_addr(data_addr)[31:2], 2'b00};
              axi_wdata_q <= data_wdata;
              axi_wstrb_q <= data_be;
              state_q <= data_we ? ST_WRITE_ADDR_DATA : ST_READ_ADDR;
            end else if (instr_req) begin
              serving_data_q <= 1'b0;
              axi_addr_q <= {ecos_mem_addr(instr_addr)[31:2], 2'b00};
              axi_wdata_q <= 32'b0;
              axi_wstrb_q <= 4'b0000;
              state_q <= ST_READ_ADDR;
            end
          end
        end
        ST_READ_ADDR: begin
          if (io_master_arready) begin
            state_q <= ST_READ_DATA;
          end
        end
        ST_READ_DATA: begin
          if (io_master_rvalid) begin
            if (io_master_rresp != 2'b00) begin
`ifndef SYNTHESIS
              $fatal(1, "Ibex AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
`endif
            end
            if (serving_data_q) begin
              data_rdata_q <= io_master_rdata;
              data_rvalid_q <= 1'b1;
            end else begin
              instr_rdata_q <= io_master_rdata;
              instr_rvalid_q <= 1'b1;
            end
            state_q <= ST_IDLE;
          end
        end
        ST_WRITE_ADDR_DATA: begin
          aw_done_q <= aw_done_next;
          w_done_q <= w_done_next;
          if (aw_done_next && w_done_next) begin
            state_q <= ST_WRITE_RESP;
          end
        end
        ST_WRITE_RESP: begin
          if (io_master_bvalid) begin
            if (io_master_bresp != 2'b00) begin
`ifndef SYNTHESIS
              $fatal(1, "Ibex AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
`endif
            end
            data_rvalid_q <= 1'b1;
            state_q <= ST_IDLE;
          end
        end
        default: begin
          state_q <= ST_IDLE;
        end
      endcase
    end
  end

  wire unused_status = ^{
    io_master_bid,
    io_master_rid,
    io_master_rlast,
    io_slave_awvalid,
    io_slave_awaddr,
    io_slave_awid,
    io_slave_awlen,
    io_slave_awsize,
    io_slave_awburst,
    io_slave_awlock,
    io_slave_awcache,
    io_slave_awprot,
    io_slave_awqos,
    io_slave_awregion,
    io_slave_wvalid,
    io_slave_wdata,
    io_slave_wstrb,
    io_slave_wlast,
    io_slave_bready,
    io_slave_arvalid,
    io_slave_araddr,
    io_slave_arid,
    io_slave_arlen,
    io_slave_arsize,
    io_slave_arburst,
    io_slave_arlock,
    io_slave_arcache,
    io_slave_arprot,
    io_slave_arqos,
    io_slave_arregion,
    io_slave_rready,
    irq_pending,
    alert_minor,
    alert_major_internal,
    alert_major_bus,
    double_fault_seen,
    core_busy,
    crash_dump,
    ic_tag_req,
    ic_tag_write,
    ic_tag_addr,
    ic_tag_wdata,
    ic_data_req,
    ic_data_write,
    ic_data_addr,
    ic_data_wdata,
    ic_scr_key_req
  };

endmodule

module ysyx_00000000 (
  input         clock,
  input         reset,
  input         io_interrupt,
  input         io_master_awready,
  output        io_master_awvalid,
  output [31:0] io_master_awaddr,
  output [3:0]  io_master_awid,
  output [7:0]  io_master_awlen,
  output [2:0]  io_master_awsize,
  output [1:0]  io_master_awburst,
  output        io_master_awlock,
  output [3:0]  io_master_awcache,
  output [2:0]  io_master_awprot,
  output [3:0]  io_master_awqos,
  output [3:0]  io_master_awregion,
  input         io_master_wready,
  output        io_master_wvalid,
  output [31:0] io_master_wdata,
  output [3:0]  io_master_wstrb,
  output        io_master_wlast,
  output        io_master_bready,
  input         io_master_bvalid,
  input  [1:0]  io_master_bresp,
  input  [3:0]  io_master_bid,
  input         io_master_arready,
  output        io_master_arvalid,
  output [31:0] io_master_araddr,
  output [3:0]  io_master_arid,
  output [7:0]  io_master_arlen,
  output [2:0]  io_master_arsize,
  output [1:0]  io_master_arburst,
  output        io_master_arlock,
  output [3:0]  io_master_arcache,
  output [2:0]  io_master_arprot,
  output [3:0]  io_master_arqos,
  output [3:0]  io_master_arregion,
  output        io_master_rready,
  input         io_master_rvalid,
  input  [1:0]  io_master_rresp,
  input  [31:0] io_master_rdata,
  input         io_master_rlast,
  input  [3:0]  io_master_rid,
  output        io_slave_awready,
  input         io_slave_awvalid,
  input  [31:0] io_slave_awaddr,
  input  [3:0]  io_slave_awid,
  input  [7:0]  io_slave_awlen,
  input  [2:0]  io_slave_awsize,
  input  [1:0]  io_slave_awburst,
  input         io_slave_awlock,
  input  [3:0]  io_slave_awcache,
  input  [2:0]  io_slave_awprot,
  input  [3:0]  io_slave_awqos,
  input  [3:0]  io_slave_awregion,
  output        io_slave_wready,
  input         io_slave_wvalid,
  input  [31:0] io_slave_wdata,
  input  [3:0]  io_slave_wstrb,
  input         io_slave_wlast,
  input         io_slave_bready,
  output        io_slave_bvalid,
  output [1:0]  io_slave_bresp,
  output [3:0]  io_slave_bid,
  output        io_slave_arready,
  input         io_slave_arvalid,
  input  [31:0] io_slave_araddr,
  input  [3:0]  io_slave_arid,
  input  [7:0]  io_slave_arlen,
  input  [2:0]  io_slave_arsize,
  input  [1:0]  io_slave_arburst,
  input         io_slave_arlock,
  input  [3:0]  io_slave_arcache,
  input  [2:0]  io_slave_arprot,
  input  [3:0]  io_slave_arqos,
  input  [3:0]  io_slave_arregion,
  input         io_slave_rready,
  output        io_slave_rvalid,
  output [1:0]  io_slave_rresp,
  output [31:0] io_slave_rdata,
  output        io_slave_rlast,
  output [3:0]  io_slave_rid
);
  ecos_ibex_cpu_wrapper wrapper (.*);
endmodule
