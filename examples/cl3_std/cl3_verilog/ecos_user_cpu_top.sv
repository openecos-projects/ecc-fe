module ecos_user_cpu_top (
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
  input  [3:0]  io_master_rid
);

  assign io_master_awqos = 4'b0000;
  assign io_master_awregion = 4'b0000;
  assign io_master_arqos = 4'b0000;
  assign io_master_arregion = 4'b0000;

  CL3Top cl3_top (
    .clock(clock),
    .reset(reset),
    .io_extIrq(io_interrupt),
    .io_timerIrq(1'b0),

    .io_master_aw_ready(io_master_awready),
    .io_master_aw_valid(io_master_awvalid),
    .io_master_aw_bits_awaddr(io_master_awaddr),
    .io_master_aw_bits_awid(io_master_awid),
    .io_master_aw_bits_awlen(io_master_awlen),
    .io_master_aw_bits_awsize(io_master_awsize),
    .io_master_aw_bits_awburst(io_master_awburst),
    .io_master_aw_bits_awlock(io_master_awlock),
    .io_master_aw_bits_awcache(io_master_awcache),
    .io_master_aw_bits_awprot(io_master_awprot),

    .io_master_w_ready(io_master_wready),
    .io_master_w_valid(io_master_wvalid),
    .io_master_w_bits_wdata(io_master_wdata),
    .io_master_w_bits_wstrb(io_master_wstrb),
    .io_master_w_bits_wlast(io_master_wlast),

    .io_master_b_ready(io_master_bready),
    .io_master_b_valid(io_master_bvalid),
    .io_master_b_bits_bresp(io_master_bresp),
    .io_master_b_bits_bid(io_master_bid),

    .io_master_ar_ready(io_master_arready),
    .io_master_ar_valid(io_master_arvalid),
    .io_master_ar_bits_araddr(io_master_araddr),
    .io_master_ar_bits_arid(io_master_arid),
    .io_master_ar_bits_arlen(io_master_arlen),
    .io_master_ar_bits_arsize(io_master_arsize),
    .io_master_ar_bits_arburst(io_master_arburst),
    .io_master_ar_bits_arlock(io_master_arlock),
    .io_master_ar_bits_arcache(io_master_arcache),
    .io_master_ar_bits_arprot(io_master_arprot),

    .io_master_r_ready(io_master_rready),
    .io_master_r_valid(io_master_rvalid),
    .io_master_r_bits_rresp(io_master_rresp),
    .io_master_r_bits_rdata(io_master_rdata),
    .io_master_r_bits_rlast(io_master_rlast),
    .io_master_r_bits_rid(io_master_rid)
  );

endmodule
