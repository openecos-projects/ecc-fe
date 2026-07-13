// ECOS CVA6 adapter for the ysyx AXI CPU socket.
//
// CVA6 exposes a typed AXI master port.  This wrapper fixes CVA6 to a
// simulator-oriented RV32/32-bit-AXI profile and maps that typed AXI request
// onto the flat ECOS CPU socket used by the frontend SoC harnesses.

package ecos_cva6_axi32_pkg;
  localparam int unsigned IdWidth = 4;
  localparam int unsigned AddrWidth = 32;
  localparam int unsigned DataWidth = 32;
  localparam int unsigned UserWidth = ariane_pkg::AXI_USER_WIDTH;
  localparam int unsigned StrbWidth = DataWidth / 8;

  typedef logic [IdWidth-1:0] id_t;
  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [DataWidth-1:0] data_t;
  typedef logic [StrbWidth-1:0] strb_t;
  typedef logic [UserWidth-1:0] user_t;

  typedef struct packed {
    id_t              id;
    addr_t            addr;
    axi_pkg::len_t    len;
    axi_pkg::size_t   size;
    axi_pkg::burst_t  burst;
    logic             lock;
    axi_pkg::cache_t  cache;
    axi_pkg::prot_t   prot;
    axi_pkg::qos_t    qos;
    axi_pkg::region_t region;
    axi_pkg::atop_t   atop;
    user_t            user;
  } aw_chan_t;

  typedef struct packed {
    data_t data;
    strb_t strb;
    logic  last;
    user_t user;
  } w_chan_t;

  typedef struct packed {
    id_t            id;
    axi_pkg::resp_t resp;
    user_t          user;
  } b_chan_t;

  typedef struct packed {
    id_t             id;
    addr_t           addr;
    axi_pkg::len_t   len;
    axi_pkg::size_t  size;
    axi_pkg::burst_t burst;
    logic            lock;
    axi_pkg::cache_t cache;
    axi_pkg::prot_t  prot;
    axi_pkg::qos_t   qos;
    axi_pkg::region_t region;
    user_t           user;
  } ar_chan_t;

  typedef struct packed {
    id_t            id;
    data_t          data;
    axi_pkg::resp_t resp;
    logic           last;
    user_t          user;
  } r_chan_t;

  typedef struct packed {
    aw_chan_t aw;
    logic     aw_valid;
    w_chan_t  w;
    logic     w_valid;
    logic     b_ready;
    ar_chan_t ar;
    logic     ar_valid;
    logic     r_ready;
  } req_t;

  typedef struct packed {
    logic     aw_ready;
    logic     ar_ready;
    logic     w_ready;
    logic     b_valid;
    b_chan_t  b;
    logic     r_valid;
    r_chan_t  r;
  } resp_t;
endpackage

`define ECOS_CVA6_CPU_SOCKET_PORTS \
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

module ecos_cva6_cpu_wrapper (
  `ECOS_CVA6_CPU_SOCKET_PORTS
);
  import ariane_pkg::*;
  import ecos_cva6_axi32_pkg::*;

  localparam [31:0] RESET_PC = 32'h2000_0000;
  localparam ariane_pkg::ariane_cfg_t ECOS_CVA6_CFG = '{
    RASDepth: 2,
    BTBEntries: 32,
    BHTEntries: 128,
    NrNonIdempotentRules: 1,
    NonIdempotentAddrBase: {64'h1000_0000},
    NonIdempotentLength: {64'h0000_1000},
    NrExecuteRegionRules: 1,
    ExecuteRegionAddrBase: {64'h2000_0000},
    ExecuteRegionLength: {64'h0800_0000},
    NrCachedRegionRules: 1,
    CachedRegionAddrBase: {64'h2000_0000},
    CachedRegionLength: {64'h0800_0000},
    AxiCompliant: 1'b1,
    SwapEndianess: 1'b0,
    DmBaseAddress: 64'h0,
    NrPMPEntries: 8
  };

  ecos_cva6_axi32_pkg::req_t axi_req;
  ecos_cva6_axi32_pkg::resp_t axi_resp;
  cvxif_pkg::cvxif_req_t cvxif_req;
  cvxif_pkg::cvxif_resp_t cvxif_resp;

  assign io_master_awvalid = axi_req.aw_valid;
  assign io_master_awaddr = axi_req.aw.addr;
  assign io_master_awid = axi_req.aw.id;
  assign io_master_awlen = axi_req.aw.len;
  assign io_master_awsize = axi_req.aw.size;
  assign io_master_awburst = axi_req.aw.burst;
  assign io_master_awlock = axi_req.aw.lock;
  assign io_master_awcache = axi_req.aw.cache;
  assign io_master_awprot = axi_req.aw.prot;
  assign io_master_awqos = axi_req.aw.qos;
  assign io_master_awregion = axi_req.aw.region;

  assign io_master_wvalid = axi_req.w_valid;
  assign io_master_wdata = axi_req.w.data;
  assign io_master_wstrb = axi_req.w.strb;
  assign io_master_wlast = axi_req.w.last;
  assign io_master_bready = axi_req.b_ready;

  assign io_master_arvalid = axi_req.ar_valid;
  assign io_master_araddr = axi_req.ar.addr;
  assign io_master_arid = axi_req.ar.id;
  assign io_master_arlen = axi_req.ar.len;
  assign io_master_arsize = axi_req.ar.size;
  assign io_master_arburst = axi_req.ar.burst;
  assign io_master_arlock = axi_req.ar.lock;
  assign io_master_arcache = axi_req.ar.cache;
  assign io_master_arprot = axi_req.ar.prot;
  assign io_master_arqos = axi_req.ar.qos;
  assign io_master_arregion = axi_req.ar.region;
  assign io_master_rready = axi_req.r_ready;

  assign axi_resp.aw_ready = io_master_awready;
  assign axi_resp.w_ready = io_master_wready;
  assign axi_resp.b_valid = io_master_bvalid;
  assign axi_resp.b.id = io_master_bid;
  assign axi_resp.b.resp = axi_pkg::resp_t'(io_master_bresp);
  assign axi_resp.b.user = '0;
  assign axi_resp.ar_ready = io_master_arready;
  assign axi_resp.r_valid = io_master_rvalid;
  assign axi_resp.r.id = io_master_rid;
  assign axi_resp.r.data = io_master_rdata;
  assign axi_resp.r.resp = axi_pkg::resp_t'(io_master_rresp);
  assign axi_resp.r.last = io_master_rlast;
  assign axi_resp.r.user = '0;

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

  assign cvxif_resp = '0;

  cva6 #(
    .ArianeCfg(ECOS_CVA6_CFG),
    .AxiAddrWidth(32),
    .AxiDataWidth(32),
    .AxiIdWidth(4),
    .axi_ar_chan_t(ecos_cva6_axi32_pkg::ar_chan_t),
    .axi_aw_chan_t(ecos_cva6_axi32_pkg::aw_chan_t),
    .axi_w_chan_t(ecos_cva6_axi32_pkg::w_chan_t),
    .axi_req_t(ecos_cva6_axi32_pkg::req_t),
    .axi_rsp_t(ecos_cva6_axi32_pkg::resp_t)
  ) core (
    .clk_i(clock),
    .rst_ni(~reset),
    .boot_addr_i(RESET_PC),
    .hart_id_i('0),
    .irq_i({1'b0, io_interrupt}),
    .ipi_i(1'b0),
    .time_irq_i(1'b0),
    .debug_req_i(1'b0),
    .cvxif_req_o(cvxif_req),
    .cvxif_resp_i(cvxif_resp),
    .axi_req_o(axi_req),
    .axi_resp_i(axi_resp)
  );

  wire unused_inputs = ^{
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
    cvxif_req
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

  ecos_cva6_cpu_wrapper wrapper (
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
