// ECOS SCR1 adapter for the ysyx AXI CPU socket.
//
// SCR1 exposes independent instruction/data memory ports internally.  This
// wrapper keeps SCR1 unmodified, arbitrates those ports, and presents the same
// CPU socket used by the ECOS SoC harnesses.

`include "scr1_arch_description.svh"
`include "scr1_memif.svh"
`ifdef SCR1_IPIC_EN
`include "scr1_ipic.svh"
`endif

module ecos_scr1_cpu_wrapper (
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

  localparam [31:0] HALT_ADDR = 32'h1000_000c;
  localparam [31:0] UART_ADDR = 32'h1000_0000;

  localparam [2:0] ST_IDLE = 3'd0;
  localparam [2:0] ST_READ_ADDR = 3'd1;
  localparam [2:0] ST_READ_DATA = 3'd2;
  localparam [2:0] ST_WRITE_ADDR_DATA = 3'd3;
  localparam [2:0] ST_WRITE_RESP = 3'd4;

  reg [2:0]  state_q;
  reg        serving_dmem_q;
  reg [31:0] axi_addr_q;
  reg [31:0] axi_wdata_q;
  reg [3:0]  axi_wstrb_q;
  reg [2:0]  axi_size_q;
  reg [1:0]  axi_addr_low_q;
  reg [1:0]  req_width_q;
  reg        aw_done_q;
  reg        w_done_q;

  wire        core_rst_n;
  wire        core_rdc_qlfy;
  wire        imem_req;
  wire        imem_req_ack;
  type_scr1_mem_cmd_e imem_cmd;
  wire [31:0] imem_addr;
  reg  [31:0] imem_rdata_q;
  type_scr1_mem_resp_e imem_resp_q;
  wire        dmem_req;
  wire        dmem_req_ack;
  wire        dmem_write_req;
  type_scr1_mem_cmd_e dmem_cmd;
  type_scr1_mem_width_e dmem_width;
  wire [31:0] dmem_addr;
  wire [31:0] dmem_wdata;
  reg  [31:0] dmem_rdata_q;
  type_scr1_mem_resp_e dmem_resp_q;

  wire        aw_fire = io_master_awvalid && io_master_awready;
  wire        w_fire = io_master_wvalid && io_master_wready;
  wire        aw_done_next = aw_done_q || aw_fire;
  wire        w_done_next = w_done_q || w_fire;

`ifdef SCR1_DBG_EN
  wire scr1_sys_rst_n;
  wire scr1_sys_rdc_qlfy;
  wire scr1_tdo;
  wire scr1_tdo_en;
`endif

  wire local_uart_write =
      (state_q == ST_IDLE) && dmem_req && dmem_write_req && (dmem_addr == UART_ADDR);
  wire local_halt_write =
      (state_q == ST_IDLE) && dmem_req && dmem_write_req && (dmem_addr == HALT_ADDR);
  wire local_write = local_uart_write || local_halt_write;

  function [2:0] width_to_axi_size;
    input [1:0] width;
    begin
      case (width)
        SCR1_MEM_WIDTH_BYTE: width_to_axi_size = 3'b000;
        SCR1_MEM_WIDTH_HWORD: width_to_axi_size = 3'b001;
        default: width_to_axi_size = 3'b010;
      endcase
    end
  endfunction

  function [3:0] width_to_wstrb;
    input [1:0] width;
    input [1:0] addr_low;
    begin
      case (width)
        SCR1_MEM_WIDTH_BYTE: width_to_wstrb = 4'b0001 << addr_low;
        SCR1_MEM_WIDTH_HWORD: width_to_wstrb = 4'b0011 << {addr_low[1], 1'b0};
        default: width_to_wstrb = 4'b1111;
      endcase
    end
  endfunction

  function [31:0] align_write_data;
    input [31:0] data;
    input [1:0] width;
    input [1:0] addr_low;
    begin
      case (width)
        SCR1_MEM_WIDTH_BYTE: align_write_data = {24'b0, data[7:0]} << (8 * addr_low);
        SCR1_MEM_WIDTH_HWORD: align_write_data = {16'b0, data[15:0]} << (8 * {addr_low[1], 1'b0});
        default: align_write_data = data;
      endcase
    end
  endfunction

  function [31:0] align_read_data;
    input [31:0] data;
    input [1:0] width;
    input [1:0] addr_low;
    begin
      case (width)
        SCR1_MEM_WIDTH_BYTE: align_read_data = data >> (8 * addr_low);
        SCR1_MEM_WIDTH_HWORD: align_read_data = data >> (8 * {addr_low[1], 1'b0});
        default: align_read_data = data;
      endcase
    end
  endfunction

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

  scr1_core_top core (
    .pwrup_rst_n          (~reset),
    .rst_n                (~reset),
    .cpu_rst_n            (~reset),
    .test_mode            (1'b0),
    .test_rst_n           (1'b1),
    .clk                  (clock),
    .core_rst_n_o         (core_rst_n),
    .core_rdc_qlfy_o      (core_rdc_qlfy),
`ifdef SCR1_DBG_EN
    .sys_rst_n_o          (scr1_sys_rst_n),
    .sys_rdc_qlfy_o       (scr1_sys_rdc_qlfy),
`endif
    .core_fuse_mhartid_i  (32'b0),
`ifdef SCR1_DBG_EN
    .tapc_fuse_idcode_i   (`SCR1_TAP_IDCODE),
`endif
`ifdef SCR1_IPIC_EN
    .core_irq_lines_i     ({{(SCR1_IRQ_LINES_NUM-1){1'b0}}, io_interrupt}),
`else
    .core_irq_ext_i       (io_interrupt),
`endif
    .core_irq_soft_i      (1'b0),
    .core_irq_mtimer_i    (1'b0),
    .core_mtimer_val_i    (64'b0),
`ifdef SCR1_DBG_EN
    .tapc_trst_n          (1'b1),
    .tapc_tck             (1'b0),
    .tapc_tms             (1'b0),
    .tapc_tdi             (1'b0),
    .tapc_tdo             (scr1_tdo),
    .tapc_tdo_en          (scr1_tdo_en),
`endif
    .imem2core_req_ack_i  (imem_req_ack),
    .core2imem_req_o      (imem_req),
    .core2imem_cmd_o      (imem_cmd),
    .core2imem_addr_o     (imem_addr),
    .imem2core_rdata_i    (imem_rdata_q),
    .imem2core_resp_i     (imem_resp_q),
    .dmem2core_req_ack_i  (dmem_req_ack),
    .core2dmem_req_o      (dmem_req),
    .core2dmem_cmd_o      (dmem_cmd),
    .core2dmem_width_o    (dmem_width),
    .core2dmem_addr_o     (dmem_addr),
    .core2dmem_wdata_o    (dmem_wdata),
    .dmem2core_rdata_i    (dmem_rdata_q),
    .dmem2core_resp_i     (dmem_resp_q)
  );

  assign dmem_write_req = (dmem_cmd == SCR1_MEM_CMD_WR);

  assign imem_req_ack = (state_q == ST_IDLE) && !dmem_req && imem_req;
  assign dmem_req_ack = (state_q == ST_IDLE) && dmem_req;

  assign io_master_awvalid = (state_q == ST_WRITE_ADDR_DATA) && !aw_done_q;
  assign io_master_awaddr = axi_addr_q;
  assign io_master_awid = 4'b0001;
  assign io_master_awlen = 8'b0000_0000;
  assign io_master_awsize = axi_size_q;
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
  assign io_master_arid = serving_dmem_q ? 4'b0001 : 4'b0000;
  assign io_master_arlen = 8'b0000_0000;
  assign io_master_arsize = axi_size_q;
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
      serving_dmem_q <= 1'b0;
      axi_addr_q <= 32'b0;
      axi_wdata_q <= 32'b0;
      axi_wstrb_q <= 4'b0000;
      axi_size_q <= 3'b010;
      axi_addr_low_q <= 2'b00;
      req_width_q <= SCR1_MEM_WIDTH_WORD;
      aw_done_q <= 1'b0;
      w_done_q <= 1'b0;
      imem_rdata_q <= 32'b0;
      imem_resp_q <= SCR1_MEM_RESP_NOTRDY;
      dmem_rdata_q <= 32'b0;
      dmem_resp_q <= SCR1_MEM_RESP_NOTRDY;
    end else begin
      imem_resp_q <= SCR1_MEM_RESP_NOTRDY;
      dmem_resp_q <= SCR1_MEM_RESP_NOTRDY;

      if (local_uart_write) begin
`ifndef SYNTHESIS
        $write("%c", wstrb_byte(dmem_wdata, width_to_wstrb(dmem_width, dmem_addr[1:0])));
        $fflush();
`endif
        dmem_resp_q <= SCR1_MEM_RESP_RDY_OK;
      end

      if (local_halt_write) begin
        dmem_resp_q <= SCR1_MEM_RESP_RDY_OK;
`ifndef SYNTHESIS
        if (dmem_wdata == 32'b0) begin
          $display("HIT GOOD TRAP");
          $finish;
        end else begin
          $fatal(1, "HIT BAD TRAP, code=%0d", dmem_wdata);
        end
`endif
      end

      case (state_q)
        ST_IDLE: begin
          aw_done_q <= 1'b0;
          w_done_q <= 1'b0;
          if (!local_write) begin
            if (dmem_req) begin
              serving_dmem_q <= 1'b1;
              axi_addr_q <= {dmem_addr[31:2], 2'b00};
              axi_wdata_q <= align_write_data(dmem_wdata, dmem_width, dmem_addr[1:0]);
              axi_wstrb_q <= width_to_wstrb(dmem_width, dmem_addr[1:0]);
              axi_size_q <= width_to_axi_size(dmem_width);
              axi_addr_low_q <= dmem_addr[1:0];
              req_width_q <= dmem_width;
              state_q <= dmem_write_req ? ST_WRITE_ADDR_DATA : ST_READ_ADDR;
            end else if (imem_req) begin
              serving_dmem_q <= 1'b0;
              axi_addr_q <= {imem_addr[31:2], 2'b00};
              axi_wdata_q <= 32'b0;
              axi_wstrb_q <= 4'b0000;
              axi_size_q <= 3'b010;
              axi_addr_low_q <= imem_addr[1:0];
              req_width_q <= SCR1_MEM_WIDTH_WORD;
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
              $fatal(1, "SCR1 AXI read error: resp=%0d addr=0x%08x", io_master_rresp, axi_addr_q);
`endif
            end
            if (serving_dmem_q) begin
              dmem_rdata_q <= align_read_data(io_master_rdata, req_width_q, axi_addr_low_q);
              dmem_resp_q <= SCR1_MEM_RESP_RDY_OK;
            end else begin
              imem_rdata_q <= io_master_rdata;
              imem_resp_q <= SCR1_MEM_RESP_RDY_OK;
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
              $fatal(1, "SCR1 AXI write error: resp=%0d addr=0x%08x", io_master_bresp, axi_addr_q);
`endif
            end
            dmem_resp_q <= SCR1_MEM_RESP_RDY_OK;
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

module ecos_internal_cpu_socket (
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
  ecos_scr1_cpu_wrapper wrapper (
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
