// ECOS CV32E40P adapter for the ysyx AXI CPU socket.
//
// CV32E40P exposes separate OBI instruction/data ports.  This wrapper keeps
// upstream RTL untouched and adapts the integer-core configuration to the
// stable ECOS CPU socket used by the frontend SoC wrappers.

module ecos_cv32e40p_cpu_wrapper (
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

  import cv32e40p_apu_core_pkg::*;

  localparam [31:0] RESET_PC = 32'h2000_0000;
  localparam [31:0] TRAP_VECTOR = 32'h2000_0100;
  localparam [31:0] DEBUG_ADDR = 32'h2000_0000;
  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

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
  reg         local_write_resp_q;
  wire        data_we;
  wire [3:0]  data_be;
  wire [31:0] data_addr;
  wire [31:0] data_wdata;
  reg  [31:0] data_rdata_q;

  wire                              apu_busy;
  wire                              apu_req;
  wire [APU_NARGS_CPU-1:0][31:0]   apu_operands;
  wire [APU_WOP_CPU-1:0]           apu_op;
  wire [APU_NDSFLAGS_CPU-1:0]      apu_flags;
  wire                              irq_ack;
  wire [4:0]                        irq_id;
  wire                              debug_havereset;
  wire                              debug_running;
  wire                              debug_halted;
  wire                              core_sleep;

  wire data_write_req = data_req && data_we;
  wire local_uart_write_req =
      (state_q == ST_IDLE) && data_write_req && (data_addr == UART_ADDR);
  wire local_halt_write_req =
      (state_q == ST_IDLE) && data_write_req && (data_addr == HALT_ADDR);
  wire local_write_req = local_uart_write_req || local_halt_write_req;
  wire local_uart_write = local_uart_write_req && !local_write_resp_q;
  wire local_halt_write = local_halt_write_req && !local_write_resp_q;
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

  cv32e40p_core #(
    .COREV_PULP      (0),
    .COREV_CLUSTER   (0),
    .FPU             (0),
    .FPU_ADDMUL_LAT  (0),
    .FPU_OTHERS_LAT  (0),
    .ZFINX           (0),
    .NUM_MHPMCOUNTERS(1)
  ) core (
    .clk_i             (clock),
    .rst_ni            (~reset),
    .pulp_clock_en_i   (1'b1),
    .scan_cg_en_i      (1'b0),
    .boot_addr_i       (RESET_PC),
    .mtvec_addr_i      (TRAP_VECTOR),
    .dm_halt_addr_i    (DEBUG_ADDR),
    .hart_id_i         (32'b0),
    .dm_exception_addr_i(DEBUG_ADDR),
    .instr_req_o       (instr_req),
    .instr_gnt_i       (instr_gnt),
    .instr_rvalid_i    (instr_rvalid_q),
    .instr_addr_o      (instr_addr),
    .instr_rdata_i     (instr_rdata_q),
    .data_req_o        (data_req),
    .data_gnt_i        (data_gnt),
    .data_rvalid_i     (data_rvalid_q || local_write_resp_q),
    .data_we_o         (data_we),
    .data_be_o         (data_be),
    .data_addr_o       (data_addr),
    .data_wdata_o      (data_wdata),
    .data_rdata_i      (data_rdata_q),
    .apu_busy_o        (apu_busy),
    .apu_req_o         (apu_req),
    .apu_gnt_i         (1'b0),
    .apu_operands_o    (apu_operands),
    .apu_op_o          (apu_op),
    .apu_flags_o       (apu_flags),
    .apu_rvalid_i      (1'b0),
    .apu_result_i      (32'b0),
    .apu_flags_i       ('0),
    .irq_i             ({31'b0, io_interrupt}),
    .irq_ack_o         (irq_ack),
    .irq_id_o          (irq_id),
    .debug_req_i       (1'b0),
    .debug_havereset_o (debug_havereset),
    .debug_running_o   (debug_running),
    .debug_halted_o    (debug_halted),
    .fetch_enable_i    (1'b1),
    .core_sleep_o      (core_sleep)
  );

  assign data_gnt = (state_q == ST_IDLE) && data_req && !local_write_resp_q;
  assign instr_gnt = (state_q == ST_IDLE) && !data_req && instr_req && !local_write_resp_q;

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
      local_write_resp_q <= 1'b0;
      data_rdata_q <= 32'b0;
    end else begin
      instr_rvalid_q <= 1'b0;
      data_rvalid_q <= 1'b0;
      local_write_resp_q <= 1'b0;

      if (local_uart_write) begin
`ifndef SYNTHESIS
        $write("%c", wstrb_byte(data_wdata, data_be));
        $fflush();
`endif
        local_write_resp_q <= 1'b1;
      end

      if (local_halt_write) begin
        local_write_resp_q <= 1'b1;
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
          if (!local_write_req && !local_write_resp_q) begin
            if (data_req) begin
              serving_data_q <= 1'b1;
              axi_addr_q <= {data_addr[31:2], 2'b00};
              axi_wdata_q <= data_wdata;
              axi_wstrb_q <= data_be;
              state_q <= data_we ? ST_WRITE_ADDR_DATA : ST_READ_ADDR;
            end else if (instr_req) begin
              serving_data_q <= 1'b0;
              axi_addr_q <= {instr_addr[31:2], 2'b00};
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
              $fatal(1, "CV32E40P AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
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
              $fatal(1, "CV32E40P AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
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
    apu_busy,
    apu_req,
    apu_operands[0],
    apu_operands[1],
    apu_operands[2],
    apu_op,
    apu_flags,
    irq_ack,
    irq_id,
    debug_havereset,
    debug_running,
    debug_halted,
    core_sleep
  };

endmodule

module cpu_top (
  input         clock,
  input         reset,
  input         io_extIrq,
  input         io_timerIrq,
  input         io_master_aw_ready,
  output        io_master_aw_valid,
  output [31:0] io_master_aw_bits_awaddr,
  output [3:0]  io_master_aw_bits_awid,
  output [7:0]  io_master_aw_bits_awlen,
  output [2:0]  io_master_aw_bits_awsize,
  output [1:0]  io_master_aw_bits_awburst,
  output        io_master_aw_bits_awlock,
  output [3:0]  io_master_aw_bits_awcache,
  output [2:0]  io_master_aw_bits_awprot,
  input         io_master_w_ready,
  output        io_master_w_valid,
  output [31:0] io_master_w_bits_wdata,
  output [3:0]  io_master_w_bits_wstrb,
  output        io_master_w_bits_wlast,
  output        io_master_b_ready,
  input         io_master_b_valid,
  input  [1:0]  io_master_b_bits_bresp,
  input  [3:0]  io_master_b_bits_bid,
  input         io_master_ar_ready,
  output        io_master_ar_valid,
  output [31:0] io_master_ar_bits_araddr,
  output [3:0]  io_master_ar_bits_arid,
  output [7:0]  io_master_ar_bits_arlen,
  output [2:0]  io_master_ar_bits_arsize,
  output [1:0]  io_master_ar_bits_arburst,
  output        io_master_ar_bits_arlock,
  output [3:0]  io_master_ar_bits_arcache,
  output [2:0]  io_master_ar_bits_arprot,
  output        io_master_r_ready,
  input         io_master_r_valid,
  input  [1:0]  io_master_r_bits_rresp,
  input  [31:0] io_master_r_bits_rdata,
  input         io_master_r_bits_rlast,
  input  [3:0]  io_master_r_bits_rid
);

  wire combined_interrupt = io_extIrq | io_timerIrq;

  ecos_cv32e40p_cpu_wrapper wrapper (
    .clock(clock),
    .reset(reset),
    .io_interrupt(combined_interrupt),
    .io_master_awready(io_master_aw_ready),
    .io_master_awvalid(io_master_aw_valid),
    .io_master_awaddr(io_master_aw_bits_awaddr),
    .io_master_awid(io_master_aw_bits_awid),
    .io_master_awlen(io_master_aw_bits_awlen),
    .io_master_awsize(io_master_aw_bits_awsize),
    .io_master_awburst(io_master_aw_bits_awburst),
    .io_master_awlock(io_master_aw_bits_awlock),
    .io_master_awcache(io_master_aw_bits_awcache),
    .io_master_awprot(io_master_aw_bits_awprot),
    .io_master_awqos(),
    .io_master_awregion(),
    .io_master_wready(io_master_w_ready),
    .io_master_wvalid(io_master_w_valid),
    .io_master_wdata(io_master_w_bits_wdata),
    .io_master_wstrb(io_master_w_bits_wstrb),
    .io_master_wlast(io_master_w_bits_wlast),
    .io_master_bready(io_master_b_ready),
    .io_master_bvalid(io_master_b_valid),
    .io_master_bresp(io_master_b_bits_bresp),
    .io_master_bid(io_master_b_bits_bid),
    .io_master_arready(io_master_ar_ready),
    .io_master_arvalid(io_master_ar_valid),
    .io_master_araddr(io_master_ar_bits_araddr),
    .io_master_arid(io_master_ar_bits_arid),
    .io_master_arlen(io_master_ar_bits_arlen),
    .io_master_arsize(io_master_ar_bits_arsize),
    .io_master_arburst(io_master_ar_bits_arburst),
    .io_master_arlock(io_master_ar_bits_arlock),
    .io_master_arcache(io_master_ar_bits_arcache),
    .io_master_arprot(io_master_ar_bits_arprot),
    .io_master_arqos(),
    .io_master_arregion(),
    .io_master_rready(io_master_r_ready),
    .io_master_rvalid(io_master_r_valid),
    .io_master_rresp(io_master_r_bits_rresp),
    .io_master_rdata(io_master_r_bits_rdata),
    .io_master_rlast(io_master_r_bits_rlast),
    .io_master_rid(io_master_r_bits_rid),
    .io_slave_awready(),
    .io_slave_awvalid(1'b0),
    .io_slave_awaddr(32'b0),
    .io_slave_awid(4'b0),
    .io_slave_awlen(8'b0),
    .io_slave_awsize(3'b0),
    .io_slave_awburst(2'b0),
    .io_slave_awlock(1'b0),
    .io_slave_awcache(4'b0),
    .io_slave_awprot(3'b0),
    .io_slave_awqos(4'b0),
    .io_slave_awregion(4'b0),
    .io_slave_wready(),
    .io_slave_wvalid(1'b0),
    .io_slave_wdata(32'b0),
    .io_slave_wstrb(4'b0),
    .io_slave_wlast(1'b0),
    .io_slave_bready(1'b0),
    .io_slave_bvalid(),
    .io_slave_bresp(),
    .io_slave_bid(),
    .io_slave_arready(),
    .io_slave_arvalid(1'b0),
    .io_slave_araddr(32'b0),
    .io_slave_arid(4'b0),
    .io_slave_arlen(8'b0),
    .io_slave_arsize(3'b0),
    .io_slave_arburst(2'b0),
    .io_slave_arlock(1'b0),
    .io_slave_arcache(4'b0),
    .io_slave_arprot(3'b0),
    .io_slave_arqos(4'b0),
    .io_slave_arregion(4'b0),
    .io_slave_rready(1'b0),
    .io_slave_rvalid(),
    .io_slave_rresp(),
    .io_slave_rdata(),
    .io_slave_rlast(),
    .io_slave_rid()
  );

endmodule
