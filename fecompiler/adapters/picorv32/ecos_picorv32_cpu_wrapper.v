// ECOS PicoRV32 adapter for the ECOS AXI-like CPU socket.
//
// The public adapter name remains ecos_picorv32_cpu_wrapper. This file also
// exposes the SoC-facing cpu_top entrypoint used by the fixed ECOS SoC.

module ecos_picorv32_cpu_wrapper (
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

  localparam [31:0] PROGADDR_RESET = 32'h2000_0000;
  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

  localparam [2:0] ST_IDLE = 3'd0;
  localparam [2:0] ST_READ_ADDR = 3'd1;
  localparam [2:0] ST_READ_DATA = 3'd2;
  localparam [2:0] ST_WRITE_ADDR_DATA = 3'd3;
  localparam [2:0] ST_WRITE_RESP = 3'd4;

  reg [2:0]  state_q;
  reg [31:0] axi_addr_q;
  reg [31:0] axi_wdata_q;
  reg [3:0]  axi_wstrb_q;
  reg        aw_done_q;
  reg        w_done_q;

  wire        pico_trap;
  wire        pico_mem_valid;
  wire        pico_mem_instr;
  wire        pico_mem_ready;
  wire [31:0] pico_mem_addr;
  wire [31:0] pico_mem_wdata;
  wire [3:0]  pico_mem_wstrb;
  wire [31:0] pico_mem_rdata;
  wire        pico_mem_la_read;
  wire        pico_mem_la_write;
  wire [31:0] pico_mem_la_addr;
  wire [31:0] pico_mem_la_wdata;
  wire [3:0]  pico_mem_la_wstrb;
  wire        pico_pcpi_valid;
  wire [31:0] pico_pcpi_insn;
  wire [31:0] pico_pcpi_rs1;
  wire [31:0] pico_pcpi_rs2;
  wire [31:0] pico_eoi;
  wire        pico_trace_valid;
  wire [35:0] pico_trace_data;

  wire pico_write_req = pico_mem_valid && (pico_mem_wstrb != 4'b0000);
  wire pico_read_req = pico_mem_valid && (pico_mem_wstrb == 4'b0000);
  wire local_uart_write = (state_q == ST_IDLE) && pico_write_req && (pico_mem_addr == UART_ADDR);
  wire local_halt_write = (state_q == ST_IDLE) && pico_write_req && (pico_mem_addr == HALT_ADDR);
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

  picorv32 #(
    .ENABLE_COUNTERS      (1),
    .ENABLE_COUNTERS64    (1),
    .ENABLE_REGS_16_31    (1),
    .ENABLE_REGS_DUALPORT (1),
    .LATCHED_MEM_RDATA    (0),
    .TWO_STAGE_SHIFT      (1),
    .BARREL_SHIFTER       (0),
    .TWO_CYCLE_COMPARE    (0),
    .TWO_CYCLE_ALU        (0),
    .COMPRESSED_ISA       (0),
    .CATCH_MISALIGN       (1),
    .CATCH_ILLINSN        (1),
    .ENABLE_PCPI          (0),
    .ENABLE_MUL           (1),
    .ENABLE_FAST_MUL      (0),
    .ENABLE_DIV           (1),
    .ENABLE_IRQ           (0),
    .ENABLE_IRQ_QREGS     (0),
    .ENABLE_IRQ_TIMER     (0),
    .ENABLE_TRACE         (0),
    .REGS_INIT_ZERO       (1),
    .PROGADDR_RESET       (PROGADDR_RESET),
    .PROGADDR_IRQ         (32'h0000_0010),
    .STACKADDR            (32'hffff_ffff)
  ) core (
    .clk           (clock),
    .resetn        (~reset),
    .trap          (pico_trap),
    .mem_valid     (pico_mem_valid),
    .mem_instr     (pico_mem_instr),
    .mem_ready     (pico_mem_ready),
    .mem_addr      (pico_mem_addr),
    .mem_wdata     (pico_mem_wdata),
    .mem_wstrb     (pico_mem_wstrb),
    .mem_rdata     (pico_mem_rdata),
    .mem_la_read   (pico_mem_la_read),
    .mem_la_write  (pico_mem_la_write),
    .mem_la_addr   (pico_mem_la_addr),
    .mem_la_wdata  (pico_mem_la_wdata),
    .mem_la_wstrb  (pico_mem_la_wstrb),
    .pcpi_valid    (pico_pcpi_valid),
    .pcpi_insn     (pico_pcpi_insn),
    .pcpi_rs1      (pico_pcpi_rs1),
    .pcpi_rs2      (pico_pcpi_rs2),
    .pcpi_wr       (1'b0),
    .pcpi_rd       (32'b0),
    .pcpi_wait     (1'b0),
    .pcpi_ready    (1'b0),
    .irq           ({31'b0, io_interrupt}),
    .eoi           (pico_eoi),
    .trace_valid   (pico_trace_valid),
    .trace_data    (pico_trace_data)
  );

  assign pico_mem_ready = local_write ||
                          ((state_q == ST_READ_DATA) && io_master_rvalid) ||
                          ((state_q == ST_WRITE_RESP) && io_master_bvalid);
  assign pico_mem_rdata = ((state_q == ST_READ_DATA) && io_master_rvalid) ? io_master_rdata : 32'b0;

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
      axi_addr_q <= 32'b0;
      axi_wdata_q <= 32'b0;
      axi_wstrb_q <= 4'b0000;
      aw_done_q <= 1'b0;
      w_done_q <= 1'b0;
    end else begin
      if (local_uart_write) begin
`ifndef SYNTHESIS
        $write("%c", wstrb_byte(pico_mem_wdata, pico_mem_wstrb));
        $fflush();
`endif
      end
      if (local_halt_write) begin
`ifndef SYNTHESIS
        if (pico_mem_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", pico_mem_wdata);
        end
`endif
      end
      if (pico_trap && !local_halt_write) begin
`ifndef SYNTHESIS
        $fatal(1, "PicoRV32 trap before ECOS halt MMIO");
`endif
      end

      case (state_q)
        ST_IDLE: begin
          aw_done_q <= 1'b0;
          w_done_q <= 1'b0;
          if (pico_mem_valid && !local_write) begin
            axi_addr_q <= {pico_mem_addr[31:2], 2'b00};
            axi_wdata_q <= pico_mem_wdata;
            axi_wstrb_q <= pico_mem_wstrb;
            if (pico_write_req) begin
              state_q <= ST_WRITE_ADDR_DATA;
            end else if (pico_read_req) begin
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
              $fatal(1, "PicoRV32 AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
`endif
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
              $fatal(1, "PicoRV32 AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
`endif
            end
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

  ecos_picorv32_cpu_wrapper wrapper (
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
