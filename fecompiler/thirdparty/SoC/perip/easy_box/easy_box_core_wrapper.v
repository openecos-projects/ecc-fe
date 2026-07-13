module core_wrapper (
  input         clock,
  input         reset,
  input  [1:0]  core_sel,
  input         io_interrupt,
  input         io_master_awready,
  output        io_master_awvalid,
  output [3:0]  io_master_awid,
  output [31:0] io_master_awaddr,
  output [7:0]  io_master_awlen,
  output [2:0]  io_master_awsize,
  output [1:0]  io_master_awburst,
  input         io_master_wready,
  output        io_master_wvalid,
  output [31:0] io_master_wdata,
  output [3:0]  io_master_wstrb,
  output        io_master_wlast,
  output        io_master_bready,
  input         io_master_bvalid,
  input  [3:0]  io_master_bid,
  input  [1:0]  io_master_bresp,
  input         io_master_arready,
  output        io_master_arvalid,
  output [3:0]  io_master_arid,
  output [31:0] io_master_araddr,
  output [7:0]  io_master_arlen,
  output [2:0]  io_master_arsize,
  output [1:0]  io_master_arburst,
  output        io_master_rready,
  input         io_master_rvalid,
  input  [3:0]  io_master_rid,
  input  [31:0] io_master_rdata,
  input  [1:0]  io_master_rresp,
  input         io_master_rlast,
  output        io_slave_awready,
  input         io_slave_awvalid,
  input  [3:0]  io_slave_awid,
  input  [31:0] io_slave_awaddr,
  input  [7:0]  io_slave_awlen,
  input  [2:0]  io_slave_awsize,
  input  [1:0]  io_slave_awburst,
  output        io_slave_wready,
  input         io_slave_wvalid,
  input  [31:0] io_slave_wdata,
  input  [3:0]  io_slave_wstrb,
  input         io_slave_wlast,
  input         io_slave_bready,
  output        io_slave_bvalid,
  output [3:0]  io_slave_bid,
  output [1:0]  io_slave_bresp,
  output        io_slave_arready,
  input         io_slave_arvalid,
  input  [3:0]  io_slave_arid,
  input  [31:0] io_slave_araddr,
  input  [7:0]  io_slave_arlen,
  input  [2:0]  io_slave_arsize,
  input  [1:0]  io_slave_arburst,
  input         io_slave_rready,
  output        io_slave_rvalid,
  output [3:0]  io_slave_rid,
  output [31:0] io_slave_rdata,
  output [1:0]  io_slave_rresp,
  output        io_slave_rlast
);
  // ECOS owns this fixed CPU socket. User designs provide cpu_top directly;
  // no YSYX-named compatibility module is part of the user contract.
  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

  reg        aw_pending_q;
  reg [31:0] aw_addr_q;

  wire aw_fire = io_master_awvalid & io_master_awready;
  wire w_fire  = io_master_wvalid & io_master_wready;
  wire [31:0] write_addr = aw_fire ? io_master_awaddr : aw_addr_q;
  wire halt_write_fire = w_fire & (aw_pending_q | aw_fire) & (write_addr == HALT_ADDR);
  wire uart_write_fire = w_fire & (aw_pending_q | aw_fire) & (write_addr == UART_ADDR);

  function automatic [7:0] axi_wstrb_byte;
    input [31:0] data;
    input [3:0] strb;
    begin
      casez (strb)
        4'b???1: axi_wstrb_byte = data[7:0];
        4'b??10: axi_wstrb_byte = data[15:8];
        4'b?100: axi_wstrb_byte = data[23:16];
        4'b1000: axi_wstrb_byte = data[31:24];
        default: axi_wstrb_byte = data[7:0];
      endcase
    end
  endfunction

  cpu_top u_core (
    .clock                  (clock),
    .reset                  (reset),
    .io_extIrq              (io_interrupt),
    .io_timerIrq            (1'b0),
    .io_master_aw_ready     (io_master_awready),
    .io_master_aw_valid     (io_master_awvalid),
    .io_master_aw_bits_awaddr(io_master_awaddr),
    .io_master_aw_bits_awid (io_master_awid),
    .io_master_aw_bits_awlen(io_master_awlen),
    .io_master_aw_bits_awsize(io_master_awsize),
    .io_master_aw_bits_awburst(io_master_awburst),
    .io_master_aw_bits_awlock(),
    .io_master_aw_bits_awcache(),
    .io_master_aw_bits_awprot(),
    .io_master_w_ready      (io_master_wready),
    .io_master_w_valid      (io_master_wvalid),
    .io_master_w_bits_wdata (io_master_wdata),
    .io_master_w_bits_wstrb (io_master_wstrb),
    .io_master_w_bits_wlast (io_master_wlast),
    .io_master_b_ready      (io_master_bready),
    .io_master_b_valid      (io_master_bvalid),
    .io_master_b_bits_bresp (io_master_bresp),
    .io_master_b_bits_bid   (io_master_bid),
    .io_master_ar_ready     (io_master_arready),
    .io_master_ar_valid     (io_master_arvalid),
    .io_master_ar_bits_araddr(io_master_araddr),
    .io_master_ar_bits_arid (io_master_arid),
    .io_master_ar_bits_arlen(io_master_arlen),
    .io_master_ar_bits_arsize(io_master_arsize),
    .io_master_ar_bits_arburst(io_master_arburst),
    .io_master_ar_bits_arlock(),
    .io_master_ar_bits_arcache(),
    .io_master_ar_bits_arprot(),
    .io_master_r_ready      (io_master_rready),
    .io_master_r_valid      (io_master_rvalid),
    .io_master_r_bits_rresp (io_master_rresp),
    .io_master_r_bits_rdata (io_master_rdata),
    .io_master_r_bits_rlast (io_master_rlast),
    .io_master_r_bits_rid   (io_master_rid)
  );

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
      aw_pending_q <= 1'b0;
      aw_addr_q <= 32'b0;
    end else begin
      if (aw_fire) begin
        aw_pending_q <= 1'b1;
        aw_addr_q <= io_master_awaddr;
      end
      if (w_fire) begin
        aw_pending_q <= 1'b0;
      end
`ifndef SYNTHESIS
      if (uart_write_fire) begin
        $write("%c", axi_wstrb_byte(io_master_wdata, io_master_wstrb));
        $fflush();
      end
`endif
      if (halt_write_fire) begin
        if (io_master_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", io_master_wdata);
        end
      end
    end
  end
endmodule
