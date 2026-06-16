// Minimal ECOS RISC-V SoC harness.
//
// This harness is intentionally small: it exposes the stable simulator-facing
// ecos_sim_top contract and connects one ECOS CPU socket to DPI-backed memory.
// CPU wrappers may handle ECOS UART/HALT locally; this harness also catches
// those MMIO writes so a wrapper can delegate trap reporting to the SoC.

module ecos_sim_top (
  input  wire        clock,
  input  wire        reset,
  input  wire        uart_rx,
  output wire        uart_tx,
  output wire        trap_valid,
  output wire [31:0] trap_code
);
  import "DPI-C" function longint mem_read(input int unsigned raddr, input int unsigned size);
  import "DPI-C" function void mem_write(input int unsigned waddr, input int unsigned mask, input int unsigned wdata);

  localparam [31:0] UART_ADDR = 32'h1000_0000;
  localparam [31:0] HALT_ADDR = 32'h1000_000c;

  wire        cpu_awready;
  wire        cpu_awvalid;
  wire [31:0] cpu_awaddr;
  wire [3:0]  cpu_awid;
  wire [7:0]  cpu_awlen;
  wire [2:0]  cpu_awsize;
  wire [1:0]  cpu_awburst;
  wire        cpu_awlock;
  wire [3:0]  cpu_awcache;
  wire [2:0]  cpu_awprot;
  wire [3:0]  cpu_awqos;
  wire [3:0]  cpu_awregion;
  wire        cpu_wready;
  wire        cpu_wvalid;
  wire [31:0] cpu_wdata;
  wire [3:0]  cpu_wstrb;
  wire        cpu_wlast;
  wire        cpu_bready;
  wire        cpu_bvalid;
  wire [1:0]  cpu_bresp;
  wire [3:0]  cpu_bid;
  wire        cpu_arready;
  wire        cpu_arvalid;
  wire [31:0] cpu_araddr;
  wire [3:0]  cpu_arid;
  wire [7:0]  cpu_arlen;
  wire [2:0]  cpu_arsize;
  wire [1:0]  cpu_arburst;
  wire        cpu_arlock;
  wire [3:0]  cpu_arcache;
  wire [2:0]  cpu_arprot;
  wire [3:0]  cpu_arqos;
  wire [3:0]  cpu_arregion;
  wire        cpu_rready;
  wire        cpu_rvalid;
  wire [1:0]  cpu_rresp;
  wire [31:0] cpu_rdata;
  wire        cpu_rlast;
  wire [3:0]  cpu_rid;

  reg        aw_pending_q;
  reg [31:0] awaddr_q;
  reg [3:0]  awid_q;
  reg [7:0]  awlen_q;
  reg [2:0]  awsize_q;
  reg        bvalid_q;
  reg [3:0]  bid_q;

  reg        rvalid_q;
  reg [31:0] raddr_q;
  reg [7:0]  rlen_q;
  reg [2:0]  rsize_q;
  reg [31:0] rdata_q;
  reg        rlast_q;
  reg [3:0]  rid_q;
  reg        trap_valid_q;
  reg [31:0] trap_code_q;

  function automatic [31:0] beat_bytes;
    input [2:0] size;
    begin
      beat_bytes = 32'd1 << size;
    end
  endfunction

  function automatic [31:0] dpi_read32;
    input [31:0] addr;
    input [2:0]  size;
    reg [63:0] raw;
    begin
      raw = mem_read(addr, {29'b0, size});
      dpi_read32 = raw[31:0];
    end
  endfunction

  function automatic is_local_mmio;
    input [31:0] addr;
    begin
      is_local_mmio = (addr == UART_ADDR) || (addr == HALT_ADDR);
    end
  endfunction

  ysyx_00000000 cpu (
    .clock(clock),
    .reset(reset),
    .io_interrupt(1'b0),
    .io_master_awready(cpu_awready),
    .io_master_awvalid(cpu_awvalid),
    .io_master_awaddr(cpu_awaddr),
    .io_master_awid(cpu_awid),
    .io_master_awlen(cpu_awlen),
    .io_master_awsize(cpu_awsize),
    .io_master_awburst(cpu_awburst),
    .io_master_awlock(cpu_awlock),
    .io_master_awcache(cpu_awcache),
    .io_master_awprot(cpu_awprot),
    .io_master_awqos(cpu_awqos),
    .io_master_awregion(cpu_awregion),
    .io_master_wready(cpu_wready),
    .io_master_wvalid(cpu_wvalid),
    .io_master_wdata(cpu_wdata),
    .io_master_wstrb(cpu_wstrb),
    .io_master_wlast(cpu_wlast),
    .io_master_bready(cpu_bready),
    .io_master_bvalid(cpu_bvalid),
    .io_master_bresp(cpu_bresp),
    .io_master_bid(cpu_bid),
    .io_master_arready(cpu_arready),
    .io_master_arvalid(cpu_arvalid),
    .io_master_araddr(cpu_araddr),
    .io_master_arid(cpu_arid),
    .io_master_arlen(cpu_arlen),
    .io_master_arsize(cpu_arsize),
    .io_master_arburst(cpu_arburst),
    .io_master_arlock(cpu_arlock),
    .io_master_arcache(cpu_arcache),
    .io_master_arprot(cpu_arprot),
    .io_master_arqos(cpu_arqos),
    .io_master_arregion(cpu_arregion),
    .io_master_rready(cpu_rready),
    .io_master_rvalid(cpu_rvalid),
    .io_master_rresp(cpu_rresp),
    .io_master_rdata(cpu_rdata),
    .io_master_rlast(cpu_rlast),
    .io_master_rid(cpu_rid),
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

  assign cpu_awready = !aw_pending_q && !bvalid_q;
  assign cpu_wready = aw_pending_q && !bvalid_q;
  assign cpu_bvalid = bvalid_q;
  assign cpu_bresp = 2'b00;
  assign cpu_bid = bid_q;

  assign cpu_arready = !rvalid_q;
  assign cpu_rvalid = rvalid_q;
  assign cpu_rresp = 2'b00;
  assign cpu_rdata = rdata_q;
  assign cpu_rlast = rlast_q;
  assign cpu_rid = rid_q;

  assign uart_tx = 1'b1;
  assign trap_valid = trap_valid_q;
  assign trap_code = trap_code_q;

  always @(posedge clock) begin
    if (reset) begin
      aw_pending_q <= 1'b0;
      awaddr_q <= 32'b0;
      awid_q <= 4'b0;
      awlen_q <= 8'b0;
      awsize_q <= 3'b010;
      bvalid_q <= 1'b0;
      bid_q <= 4'b0;
      trap_valid_q <= 1'b0;
      trap_code_q <= 32'b0;
    end else begin
      if (bvalid_q && cpu_bready) begin
        bvalid_q <= 1'b0;
      end

      if (!aw_pending_q && !bvalid_q && cpu_awvalid) begin
        aw_pending_q <= 1'b1;
        awaddr_q <= cpu_awaddr;
        awid_q <= cpu_awid;
        awlen_q <= cpu_awlen;
        awsize_q <= cpu_awsize;
      end

      if (aw_pending_q && !bvalid_q && cpu_wvalid) begin
        if (!is_local_mmio(awaddr_q)) begin
          mem_write(awaddr_q, {28'b0, cpu_wstrb}, cpu_wdata);
        end else if (awaddr_q == UART_ADDR) begin
`ifndef SYNTHESIS
          $write("%c", cpu_wdata[7:0]);
          $fflush();
`endif
        end else if (awaddr_q == HALT_ADDR) begin
          trap_valid_q <= 1'b1;
          trap_code_q <= cpu_wdata;
        end

        if (cpu_wlast || awlen_q == 8'b0) begin
          aw_pending_q <= 1'b0;
          bvalid_q <= 1'b1;
          bid_q <= awid_q;
        end else begin
          awaddr_q <= awaddr_q + beat_bytes(awsize_q);
          awlen_q <= awlen_q - 8'd1;
        end
      end
    end
  end

  always @(posedge clock) begin
    if (reset) begin
      rvalid_q <= 1'b0;
      raddr_q <= 32'b0;
      rlen_q <= 8'b0;
      rsize_q <= 3'b010;
      rdata_q <= 32'b0;
      rlast_q <= 1'b0;
      rid_q <= 4'b0;
    end else begin
      if (!rvalid_q && cpu_arvalid) begin
        rvalid_q <= 1'b1;
        raddr_q <= cpu_araddr;
        rlen_q <= cpu_arlen;
        rsize_q <= cpu_arsize;
        rdata_q <= dpi_read32(cpu_araddr, cpu_arsize);
        rlast_q <= cpu_arlen == 8'b0;
        rid_q <= cpu_arid;
      end else if (rvalid_q && cpu_rready) begin
        if (rlast_q) begin
          rvalid_q <= 1'b0;
        end else begin
          raddr_q <= raddr_q + beat_bytes(rsize_q);
          rlen_q <= rlen_q - 8'd1;
          rdata_q <= dpi_read32(raddr_q + beat_bytes(rsize_q), rsize_q);
          rlast_q <= rlen_q == 8'd1;
        end
      end
    end
  end

endmodule
