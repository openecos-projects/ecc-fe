// ECOS SERV adapter for the ysyx AXI CPU socket.
//
// SERV exposes separate instruction/data Wishbone-like ports.  This adapter
// arbitrates those ports and presents the same AXI-like socket used by the
// existing ECOS SoC wrappers.

module ecos_serv_cpu_wrapper (
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

  wire [31:0] ibus_addr;
  wire        ibus_cyc;
  reg  [31:0] ibus_rdata_q;
  reg         ibus_ack_q;
  wire [31:0] dbus_addr;
  wire [31:0] dbus_wdata;
  wire [3:0]  dbus_sel;
  wire        dbus_we;
  wire        dbus_cyc;
  reg  [31:0] dbus_rdata_q;
  reg         dbus_ack_q;

  wire [31:0] active_addr = serving_dbus_q ? dbus_addr : ibus_addr;
  wire [31:0] active_wdata = dbus_wdata;
  wire [3:0]  active_wstrb = dbus_sel;
  wire        local_uart_write = (state_q == ST_IDLE) && dbus_cyc && dbus_we && (dbus_addr == UART_ADDR);
  wire        local_halt_write = (state_q == ST_IDLE) && dbus_cyc && dbus_we && (dbus_addr == HALT_ADDR);
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

  serv_rf_top #(
    .RESET_PC(RESET_PC),
    .COMPRESSED(1'b0),
    .ALIGN(1'b0),
    .MDU(1'b0),
    .WITH_CSR(1),
    .W(1)
  ) core (
    .clk(clock),
    .i_rst(reset),
    .i_timer_irq(io_interrupt),
    .o_ibus_adr(ibus_addr),
    .o_ibus_cyc(ibus_cyc),
    .i_ibus_rdt(ibus_rdata_q),
    .i_ibus_ack(ibus_ack_q),
    .o_dbus_adr(dbus_addr),
    .o_dbus_dat(dbus_wdata),
    .o_dbus_sel(dbus_sel),
    .o_dbus_we(dbus_we),
    .o_dbus_cyc(dbus_cyc),
    .i_dbus_rdt(dbus_rdata_q),
    .i_dbus_ack(dbus_ack_q),
    .o_ext_rs1(),
    .o_ext_rs2(),
    .o_ext_funct3(),
    .i_ext_rd(32'b0),
    .i_ext_ready(1'b0),
    .o_mdu_valid()
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
        if (dbus_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", dbus_wdata);
        end
      end

      case (state_q)
        ST_IDLE: begin
          aw_done_q <= 1'b0;
          w_done_q <= 1'b0;
          if (!local_write) begin
            if (dbus_cyc) begin
              serving_dbus_q <= 1'b1;
              axi_addr_q <= {dbus_addr[31:2], 2'b00};
              axi_wdata_q <= dbus_wdata;
              axi_wstrb_q <= dbus_sel;
              state_q <= dbus_we ? ST_WRITE_ADDR_DATA : ST_READ_ADDR;
            end else if (ibus_cyc) begin
              serving_dbus_q <= 1'b0;
              axi_addr_q <= {ibus_addr[31:2], 2'b00};
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
              $fatal(1, "SERV AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
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
              $fatal(1, "SERV AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
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
  ecos_serv_cpu_wrapper adapter (
    .clock(clock),
    .reset(reset),
    .io_interrupt(io_interrupt),
    .io_master_awready(io_master_awready),
    .io_master_awvalid(io_master_awvalid),
    .io_master_awaddr(io_master_awaddr),
    .io_master_awid(io_master_awid),
    .io_master_awlen(io_master_awlen),
    .io_master_awsize(io_master_awsize),
    .io_master_awburst(io_master_awburst),
    .io_master_awlock(io_master_awlock),
    .io_master_awcache(io_master_awcache),
    .io_master_awprot(io_master_awprot),
    .io_master_awqos(io_master_awqos),
    .io_master_awregion(io_master_awregion),
    .io_master_wready(io_master_wready),
    .io_master_wvalid(io_master_wvalid),
    .io_master_wdata(io_master_wdata),
    .io_master_wstrb(io_master_wstrb),
    .io_master_wlast(io_master_wlast),
    .io_master_bready(io_master_bready),
    .io_master_bvalid(io_master_bvalid),
    .io_master_bresp(io_master_bresp),
    .io_master_bid(io_master_bid),
    .io_master_arready(io_master_arready),
    .io_master_arvalid(io_master_arvalid),
    .io_master_araddr(io_master_araddr),
    .io_master_arid(io_master_arid),
    .io_master_arlen(io_master_arlen),
    .io_master_arsize(io_master_arsize),
    .io_master_arburst(io_master_arburst),
    .io_master_arlock(io_master_arlock),
    .io_master_arcache(io_master_arcache),
    .io_master_arprot(io_master_arprot),
    .io_master_arqos(io_master_arqos),
    .io_master_arregion(io_master_arregion),
    .io_master_rready(io_master_rready),
    .io_master_rvalid(io_master_rvalid),
    .io_master_rresp(io_master_rresp),
    .io_master_rdata(io_master_rdata),
    .io_master_rlast(io_master_rlast),
    .io_master_rid(io_master_rid),
    .io_slave_awready(io_slave_awready),
    .io_slave_awvalid(io_slave_awvalid),
    .io_slave_awaddr(io_slave_awaddr),
    .io_slave_awid(io_slave_awid),
    .io_slave_awlen(io_slave_awlen),
    .io_slave_awsize(io_slave_awsize),
    .io_slave_awburst(io_slave_awburst),
    .io_slave_awlock(io_slave_awlock),
    .io_slave_awcache(io_slave_awcache),
    .io_slave_awprot(io_slave_awprot),
    .io_slave_awqos(io_slave_awqos),
    .io_slave_awregion(io_slave_awregion),
    .io_slave_wready(io_slave_wready),
    .io_slave_wvalid(io_slave_wvalid),
    .io_slave_wdata(io_slave_wdata),
    .io_slave_wstrb(io_slave_wstrb),
    .io_slave_wlast(io_slave_wlast),
    .io_slave_bready(io_slave_bready),
    .io_slave_bvalid(io_slave_bvalid),
    .io_slave_bresp(io_slave_bresp),
    .io_slave_bid(io_slave_bid),
    .io_slave_arready(io_slave_arready),
    .io_slave_arvalid(io_slave_arvalid),
    .io_slave_araddr(io_slave_araddr),
    .io_slave_arid(io_slave_arid),
    .io_slave_arlen(io_slave_arlen),
    .io_slave_arsize(io_slave_arsize),
    .io_slave_arburst(io_slave_arburst),
    .io_slave_arlock(io_slave_arlock),
    .io_slave_arcache(io_slave_arcache),
    .io_slave_arprot(io_slave_arprot),
    .io_slave_arqos(io_slave_arqos),
    .io_slave_arregion(io_slave_arregion),
    .io_slave_rready(io_slave_rready),
    .io_slave_rvalid(io_slave_rvalid),
    .io_slave_rresp(io_slave_rresp),
    .io_slave_rdata(io_slave_rdata),
    .io_slave_rlast(io_slave_rlast),
    .io_slave_rid(io_slave_rid)
  );
endmodule
