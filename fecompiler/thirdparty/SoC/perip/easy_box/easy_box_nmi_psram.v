module nmi_psram (
  input         clk_i,
  input         rst_n_i,
  input         nmi_valid,
  input  [31:0] nmi_addr,
  input  [31:0] nmi_wdata,
  input  [3:0]  nmi_wstrb,
  output [31:0] nmi_rdata,
  output        nmi_ready,
  output        qspi_spi_sck_o,
  output [3:0]  qspi_spi_nss_o,
  output [3:0]  qspi_spi_io_en_o,
  input  [3:0]  qspi_spi_io_in_i,
  output [3:0]  qspi_spi_io_out_o,
  output        qspi_irq_o
);
  localparam integer MEM_WORDS = 1 << 20;  // 4 MiB
  reg [31:0] mem [0:MEM_WORDS - 1];
  wire [19:0] word_idx = nmi_addr[21:2];
  reg [31:0] rdata_q;

  always @(posedge clk_i) begin
    if (!rst_n_i) begin
      rdata_q <= 32'h0;
    end else if (nmi_valid) begin
      rdata_q <= mem[word_idx];
      if (|nmi_wstrb) begin
        if (nmi_wstrb[0]) mem[word_idx][7:0] <= nmi_wdata[7:0];
        if (nmi_wstrb[1]) mem[word_idx][15:8] <= nmi_wdata[15:8];
        if (nmi_wstrb[2]) mem[word_idx][23:16] <= nmi_wdata[23:16];
        if (nmi_wstrb[3]) mem[word_idx][31:24] <= nmi_wdata[31:24];
      end
    end
  end

  assign nmi_rdata = rdata_q;
  assign nmi_ready = 1'b1;

  assign qspi_spi_sck_o = 1'b0;
  assign qspi_spi_nss_o = 4'hf;
  assign qspi_spi_io_en_o = 4'h0;
  assign qspi_spi_io_out_o = 4'h0;
  assign qspi_irq_o = 1'b0;
endmodule
