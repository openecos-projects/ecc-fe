module sdram_top_axi(
  input         clock,
  input         reset,
  output        in_awready,
  input         in_awvalid,
  input  [31:0] in_awaddr,
  input  [3:0]  in_awid,
  input  [7:0]  in_awlen,
  input  [2:0]  in_awsize,
  input  [1:0]  in_awburst,
  output        in_wready,
  input         in_wvalid,
  input  [31:0] in_wdata,
  input  [3:0]  in_wstrb,
  input         in_wlast,
  input         in_bready,
  output        in_bvalid,
  output [1:0]  in_bresp,
  output [3:0]  in_bid,
  output        in_arready,
  input         in_arvalid,
  input  [31:0] in_araddr,
  input  [3:0]  in_arid,
  input  [7:0]  in_arlen,
  input  [2:0]  in_arsize,
  input  [1:0]  in_arburst,
  input         in_rready,
  output        in_rvalid,
  output [1:0]  in_rresp,
  output [31:0] in_rdata,
  output        in_rlast,
  output [3:0]  in_rid,

  output        sdram_clk,
  output        sdram_cke,
  output        sdram_cs,
  output        sdram_ras,
  output        sdram_cas,
  output        sdram_we,
  output [12:0] sdram_a,
  output [ 1:0] sdram_ba,
  output [ 1:0] sdram_dqm,
  output        sdram_en,
  output [15:0] sdram_data_o,
  input  [15:0] sdram_data_i
);
  import "DPI-C" function longint mem_read(input int unsigned raddr, input int unsigned size);
  import "DPI-C" function void mem_write(input int unsigned waddr, input int unsigned mask, input int unsigned wdata);

  reg        aw_active_q;
  reg [31:0] aw_addr_q;
  reg [3:0]  aw_id_q;
  reg [7:0]  aw_beats_left_q;
  reg [2:0]  aw_size_q;
  reg [1:0]  aw_burst_q;

  reg        bvalid_q;
  reg [3:0]  bid_q;

  reg        ar_active_q;
  reg [31:0] ar_addr_q;
  reg [3:0]  ar_id_q;
  reg [7:0]  ar_beats_left_q;
  reg [2:0]  ar_size_q;
  reg [1:0]  ar_burst_q;

  reg        rvalid_q;
  reg [3:0]  rid_q;
  reg [31:0] rdata_q;
  reg        rlast_q;

  wire aw_fire = in_awready & in_awvalid;
  wire w_fire = in_wready & in_wvalid;
  wire b_fire = in_bvalid & in_bready;
  wire ar_fire = in_arready & in_arvalid;
  wire r_fire = in_rvalid & in_rready;

  function [31:0] next_addr;
    input [31:0] addr;
    input [2:0]  size;
    input [1:0]  burst;
    begin
      if (burst == 2'b01) begin
        next_addr = addr + (32'd1 << size);
      end else begin
        next_addr = addr;
      end
    end
  endfunction

  assign in_awready = ~aw_active_q & ~bvalid_q;
  assign in_wready = aw_active_q;
  assign in_arready = ~rvalid_q;

  assign in_bvalid = bvalid_q;
  assign in_bid = bid_q;
  assign in_bresp = 2'b00;

  assign in_rvalid = rvalid_q;
  assign in_rid = rid_q;
  assign in_rdata = rdata_q;
  assign in_rresp = 2'b00;
  assign in_rlast = rlast_q;

  assign sdram_clk = clock;
  assign sdram_cke = 1'b1;
  assign sdram_cs = 1'b1;
  assign sdram_ras = 1'b1;
  assign sdram_cas = 1'b1;
  assign sdram_we = 1'b1;
  assign sdram_a = 13'h0;
  assign sdram_ba = 2'b00;
  assign sdram_dqm = 2'b00;
  assign sdram_en = 1'b0;
  assign sdram_data_o = 16'h0;

  always @(posedge clock) begin
    if (reset) begin
      aw_active_q <= 1'b0;
      aw_addr_q <= 32'h0;
      aw_id_q <= 4'h0;
      aw_beats_left_q <= 8'h0;
      aw_size_q <= 3'h0;
      aw_burst_q <= 2'b01;
      bvalid_q <= 1'b0;
      bid_q <= 4'h0;
      ar_active_q <= 1'b0;
      ar_addr_q <= 32'h0;
      ar_id_q <= 4'h0;
      ar_beats_left_q <= 8'h0;
      ar_size_q <= 3'h0;
      ar_burst_q <= 2'b01;
      rvalid_q <= 1'b0;
      rid_q <= 4'h0;
      rdata_q <= 32'h0;
      rlast_q <= 1'b0;
    end else begin
      if (aw_fire) begin
        aw_active_q <= 1'b1;
        aw_addr_q <= in_awaddr;
        aw_id_q <= in_awid;
        aw_beats_left_q <= in_awlen;
        aw_size_q <= in_awsize;
        aw_burst_q <= in_awburst;
      end

      if (w_fire) begin
        mem_write(aw_addr_q, {28'h0, in_wstrb}, in_wdata);
        if (aw_beats_left_q == 8'h0 || in_wlast) begin
          aw_active_q <= 1'b0;
          bvalid_q <= 1'b1;
          bid_q <= aw_id_q;
        end else begin
          aw_beats_left_q <= aw_beats_left_q - 8'h1;
          aw_addr_q <= next_addr(aw_addr_q, aw_size_q, aw_burst_q);
        end
      end

      if (b_fire) begin
        bvalid_q <= 1'b0;
      end

      if (ar_fire) begin
        longint read_data;
        read_data = mem_read(in_araddr, {29'h0, in_arsize});
        ar_active_q <= 1'b1;
        ar_addr_q <= in_araddr;
        ar_id_q <= in_arid;
        ar_beats_left_q <= in_arlen;
        ar_size_q <= in_arsize;
        ar_burst_q <= in_arburst;
        rvalid_q <= 1'b1;
        rid_q <= in_arid;
        rdata_q <= read_data[31:0];
        rlast_q <= (in_arlen == 8'h0);
      end

      if (r_fire) begin
        if (ar_beats_left_q == 8'h0) begin
          ar_active_q <= 1'b0;
          rvalid_q <= 1'b0;
          rlast_q <= 1'b0;
        end else begin
          longint read_data_next;
          reg [31:0] addr_next;
          addr_next = next_addr(ar_addr_q, ar_size_q, ar_burst_q);
          read_data_next = mem_read(addr_next, {29'h0, ar_size_q});
          ar_addr_q <= addr_next;
          ar_beats_left_q <= ar_beats_left_q - 8'h1;
          rid_q <= ar_id_q;
          rdata_q <= read_data_next[31:0];
          rlast_q <= (ar_beats_left_q == 8'h1);
          rvalid_q <= 1'b1;
        end
      end
    end
  end
endmodule
