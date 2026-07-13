// ECOS VexRiscv adapter for the ysyx AXI CPU socket.
//
// The selected LiteX Hub VexRiscv_Min snapshot exposes separate instruction
// and data Wishbone ports.  This adapter arbitrates those ports, maps them onto
// the ECOS AXI-like CPU socket, and provides a private socket module consumed
// only by the bundled cpu_top bridge.

`define ECOS_CPU_SOCKET_PORTS \
  input         clock, \
  input         reset, \
  input         io_interrupt, \
  input         io_master_awready, \
  output        io_master_awvalid, \
  output [31:0] io_master_awaddr, \
  output [3:0]  io_master_awid, \
  output [7:0]  io_master_awlen, \
  output [2:0]  io_master_awsize, \
  output [1:0]  io_master_awburst, \
  output        io_master_awlock, \
  output [3:0]  io_master_awcache, \
  output [2:0]  io_master_awprot, \
  output [3:0]  io_master_awqos, \
  output [3:0]  io_master_awregion, \
  input         io_master_wready, \
  output        io_master_wvalid, \
  output [31:0] io_master_wdata, \
  output [3:0]  io_master_wstrb, \
  output        io_master_wlast, \
  output        io_master_bready, \
  input         io_master_bvalid, \
  input  [1:0]  io_master_bresp, \
  input  [3:0]  io_master_bid, \
  input         io_master_arready, \
  output        io_master_arvalid, \
  output [31:0] io_master_araddr, \
  output [3:0]  io_master_arid, \
  output [7:0]  io_master_arlen, \
  output [2:0]  io_master_arsize, \
  output [1:0]  io_master_arburst, \
  output        io_master_arlock, \
  output [3:0]  io_master_arcache, \
  output [2:0]  io_master_arprot, \
  output [3:0]  io_master_arqos, \
  output [3:0]  io_master_arregion, \
  output        io_master_rready, \
  input         io_master_rvalid, \
  input  [1:0]  io_master_rresp, \
  input  [31:0] io_master_rdata, \
  input         io_master_rlast, \
  input  [3:0]  io_master_rid, \
  output        io_slave_awready, \
  input         io_slave_awvalid, \
  input  [31:0] io_slave_awaddr, \
  input  [3:0]  io_slave_awid, \
  input  [7:0]  io_slave_awlen, \
  input  [2:0]  io_slave_awsize, \
  input  [1:0]  io_slave_awburst, \
  input         io_slave_awlock, \
  input  [3:0]  io_slave_awcache, \
  input  [2:0]  io_slave_awprot, \
  input  [3:0]  io_slave_awqos, \
  input  [3:0]  io_slave_awregion, \
  output        io_slave_wready, \
  input         io_slave_wvalid, \
  input  [31:0] io_slave_wdata, \
  input  [3:0]  io_slave_wstrb, \
  input         io_slave_wlast, \
  input         io_slave_bready, \
  output        io_slave_bvalid, \
  output [1:0]  io_slave_bresp, \
  output [3:0]  io_slave_bid, \
  output        io_slave_arready, \
  input         io_slave_arvalid, \
  input  [31:0] io_slave_araddr, \
  input  [3:0]  io_slave_arid, \
  input  [7:0]  io_slave_arlen, \
  input  [2:0]  io_slave_arsize, \
  input  [1:0]  io_slave_arburst, \
  input         io_slave_arlock, \
  input  [3:0]  io_slave_arcache, \
  input  [2:0]  io_slave_arprot, \
  input  [3:0]  io_slave_arqos, \
  input  [3:0]  io_slave_arregion, \
  input         io_slave_rready, \
  output        io_slave_rvalid, \
  output [1:0]  io_slave_rresp, \
  output [31:0] io_slave_rdata, \
  output        io_slave_rlast, \
  output [3:0]  io_slave_rid

`define ECOS_CPU_SOCKET_CONNECT \
  .clock(clock), \
  .reset(reset), \
  .io_interrupt(io_interrupt), \
  .io_master_awready(io_master_awready), \
  .io_master_awvalid(io_master_awvalid), \
  .io_master_awaddr(io_master_awaddr), \
  .io_master_awid(io_master_awid), \
  .io_master_awlen(io_master_awlen), \
  .io_master_awsize(io_master_awsize), \
  .io_master_awburst(io_master_awburst), \
  .io_master_awlock(io_master_awlock), \
  .io_master_awcache(io_master_awcache), \
  .io_master_awprot(io_master_awprot), \
  .io_master_awqos(io_master_awqos), \
  .io_master_awregion(io_master_awregion), \
  .io_master_wready(io_master_wready), \
  .io_master_wvalid(io_master_wvalid), \
  .io_master_wdata(io_master_wdata), \
  .io_master_wstrb(io_master_wstrb), \
  .io_master_wlast(io_master_wlast), \
  .io_master_bready(io_master_bready), \
  .io_master_bvalid(io_master_bvalid), \
  .io_master_bresp(io_master_bresp), \
  .io_master_bid(io_master_bid), \
  .io_master_arready(io_master_arready), \
  .io_master_arvalid(io_master_arvalid), \
  .io_master_araddr(io_master_araddr), \
  .io_master_arid(io_master_arid), \
  .io_master_arlen(io_master_arlen), \
  .io_master_arsize(io_master_arsize), \
  .io_master_arburst(io_master_arburst), \
  .io_master_arlock(io_master_arlock), \
  .io_master_arcache(io_master_arcache), \
  .io_master_arprot(io_master_arprot), \
  .io_master_arqos(io_master_arqos), \
  .io_master_arregion(io_master_arregion), \
  .io_master_rready(io_master_rready), \
  .io_master_rvalid(io_master_rvalid), \
  .io_master_rresp(io_master_rresp), \
  .io_master_rdata(io_master_rdata), \
  .io_master_rlast(io_master_rlast), \
  .io_master_rid(io_master_rid), \
  .io_slave_awready(io_slave_awready), \
  .io_slave_awvalid(io_slave_awvalid), \
  .io_slave_awaddr(io_slave_awaddr), \
  .io_slave_awid(io_slave_awid), \
  .io_slave_awlen(io_slave_awlen), \
  .io_slave_awsize(io_slave_awsize), \
  .io_slave_awburst(io_slave_awburst), \
  .io_slave_awlock(io_slave_awlock), \
  .io_slave_awcache(io_slave_awcache), \
  .io_slave_awprot(io_slave_awprot), \
  .io_slave_awqos(io_slave_awqos), \
  .io_slave_awregion(io_slave_awregion), \
  .io_slave_wready(io_slave_wready), \
  .io_slave_wvalid(io_slave_wvalid), \
  .io_slave_wdata(io_slave_wdata), \
  .io_slave_wstrb(io_slave_wstrb), \
  .io_slave_wlast(io_slave_wlast), \
  .io_slave_bready(io_slave_bready), \
  .io_slave_bvalid(io_slave_bvalid), \
  .io_slave_bresp(io_slave_bresp), \
  .io_slave_bid(io_slave_bid), \
  .io_slave_arready(io_slave_arready), \
  .io_slave_arvalid(io_slave_arvalid), \
  .io_slave_araddr(io_slave_araddr), \
  .io_slave_arid(io_slave_arid), \
  .io_slave_arlen(io_slave_arlen), \
  .io_slave_arsize(io_slave_arsize), \
  .io_slave_arburst(io_slave_arburst), \
  .io_slave_arlock(io_slave_arlock), \
  .io_slave_arcache(io_slave_arcache), \
  .io_slave_arprot(io_slave_arprot), \
  .io_slave_arqos(io_slave_arqos), \
  .io_slave_arregion(io_slave_arregion), \
  .io_slave_rready(io_slave_rready), \
  .io_slave_rvalid(io_slave_rvalid), \
  .io_slave_rresp(io_slave_rresp), \
  .io_slave_rdata(io_slave_rdata), \
  .io_slave_rlast(io_slave_rlast), \
  .io_slave_rid(io_slave_rid)

module ecos_vexriscv_cpu_wrapper (
  `ECOS_CPU_SOCKET_PORTS
);

  localparam [31:0] RESET_PC = 32'h2000_0000;
  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

  localparam [2:0] ST_IDLE = 3'd0;
  localparam [2:0] ST_READ_ADDR = 3'd1;
  localparam [2:0] ST_READ_DATA = 3'd2;
  localparam [2:0] ST_WRITE_ADDR_DATA = 3'd3;
  localparam [2:0] ST_WRITE_RESP = 3'd4;

  reg [2:0]  state_q;
  reg        serving_dbus_q;
  reg [31:0] axi_addr_q;
  reg [31:0] axi_wdata_q;
  reg [3:0]  axi_wstrb_q;
  reg        aw_done_q;
  reg        w_done_q;
  reg [31:0] ibus_rdata_q;
  reg [31:0] dbus_rdata_q;
  reg        ibus_ack_q;
  reg        dbus_ack_q;

  wire        ibus_cyc;
  wire        ibus_stb;
  wire        ibus_we;
  wire [29:0] ibus_adr;
  wire [31:0] ibus_wdata;
  wire [3:0]  ibus_sel;
  wire [2:0]  ibus_cti;
  wire [1:0]  ibus_bte;
  wire        dbus_cyc;
  wire        dbus_stb;
  wire        dbus_we;
  wire [29:0] dbus_adr;
  wire [31:0] dbus_wdata;
  wire [3:0]  dbus_sel;
  wire [2:0]  dbus_cti;
  wire [1:0]  dbus_bte;

  wire [31:0] ibus_addr = {ibus_adr, 2'b00};
  wire [31:0] dbus_addr = {dbus_adr, 2'b00};
  wire        dbus_req = dbus_cyc && dbus_stb;
  wire        ibus_req = ibus_cyc && ibus_stb;
  wire        ready_for_new_req = !dbus_ack_q && !ibus_ack_q;
  wire        local_uart_write =
      ready_for_new_req && (state_q == ST_IDLE) && dbus_req && dbus_we && (dbus_addr == UART_ADDR);
  wire        local_halt_write =
      ready_for_new_req && (state_q == ST_IDLE) && dbus_req && dbus_we && (dbus_addr == HALT_ADDR);
  wire        local_write = local_uart_write || local_halt_write;
  wire        aw_fire = io_master_awvalid && io_master_awready;
  wire        w_fire = io_master_wvalid && io_master_wready;
  wire        aw_done_next = aw_done_q || aw_fire;
  wire        w_done_next = w_done_q || w_fire;

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

  VexRiscv core (
    .externalResetVector(RESET_PC),
    .timerInterrupt(1'b0),
    .softwareInterrupt(1'b0),
    .externalInterruptArray({31'b0, io_interrupt}),
    .iBusWishbone_CYC(ibus_cyc),
    .iBusWishbone_STB(ibus_stb),
    .iBusWishbone_ACK(ibus_ack_q),
    .iBusWishbone_WE(ibus_we),
    .iBusWishbone_ADR(ibus_adr),
    .iBusWishbone_DAT_MISO(ibus_rdata_q),
    .iBusWishbone_DAT_MOSI(ibus_wdata),
    .iBusWishbone_SEL(ibus_sel),
    .iBusWishbone_ERR(1'b0),
    .iBusWishbone_CTI(ibus_cti),
    .iBusWishbone_BTE(ibus_bte),
    .dBusWishbone_CYC(dbus_cyc),
    .dBusWishbone_STB(dbus_stb),
    .dBusWishbone_ACK(dbus_ack_q),
    .dBusWishbone_WE(dbus_we),
    .dBusWishbone_ADR(dbus_adr),
    .dBusWishbone_DAT_MISO(dbus_rdata_q),
    .dBusWishbone_DAT_MOSI(dbus_wdata),
    .dBusWishbone_SEL(dbus_sel),
    .dBusWishbone_ERR(1'b0),
    .dBusWishbone_CTI(dbus_cti),
    .dBusWishbone_BTE(dbus_bte),
    .clk(clock),
    .reset(reset)
  );

  assign io_master_awvalid = (state_q == ST_WRITE_ADDR_DATA) && !aw_done_q;
  assign io_master_awaddr = axi_addr_q;
  assign io_master_awid = 4'b0000;
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
  assign io_master_arid = 4'b0000;
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
      serving_dbus_q <= 1'b0;
      axi_addr_q <= 32'b0;
      axi_wdata_q <= 32'b0;
      axi_wstrb_q <= 4'b0000;
      aw_done_q <= 1'b0;
      w_done_q <= 1'b0;
      ibus_rdata_q <= 32'b0;
      dbus_rdata_q <= 32'b0;
      ibus_ack_q <= 1'b0;
      dbus_ack_q <= 1'b0;
    end else begin
      ibus_ack_q <= 1'b0;
      dbus_ack_q <= 1'b0;

      if (local_uart_write) begin
`ifndef SYNTHESIS
        $write("%c", wstrb_byte(dbus_wdata, dbus_sel));
        $fflush();
`endif
        dbus_ack_q <= 1'b1;
      end

      if (local_halt_write) begin
        dbus_ack_q <= 1'b1;
`ifndef SYNTHESIS
        if (dbus_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", dbus_wdata);
        end
`endif
      end

      case (state_q)
        ST_IDLE: begin
          aw_done_q <= 1'b0;
          w_done_q <= 1'b0;
          if (ready_for_new_req && !local_write) begin
            if (dbus_req) begin
              serving_dbus_q <= 1'b1;
              axi_addr_q <= dbus_addr;
              axi_wdata_q <= dbus_wdata;
              axi_wstrb_q <= dbus_sel;
              state_q <= dbus_we ? ST_WRITE_ADDR_DATA : ST_READ_ADDR;
            end else if (ibus_req) begin
              serving_dbus_q <= 1'b0;
              axi_addr_q <= ibus_addr;
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
              $fatal(1, "VexRiscv AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
`endif
            end
            if (serving_dbus_q) begin
              dbus_rdata_q <= io_master_rdata;
              dbus_ack_q <= 1'b1;
            end else begin
              ibus_rdata_q <= io_master_rdata;
              ibus_ack_q <= 1'b1;
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
              $fatal(1, "VexRiscv AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
`endif
            end
            dbus_ack_q <= 1'b1;
            state_q <= ST_IDLE;
          end
        end
        default: begin
          state_q <= ST_IDLE;
        end
      endcase
    end
  end

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

  ecos_vexriscv_cpu_wrapper wrapper (
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
