module difftest_wrapper (
  /* verilator lint_off UNUSED */
  input logic clock,
  input logic reset,

  input logic [31:0] diff_info_0_pc,
  input logic [31:0] diff_info_0_npc,
  input logic [31:0] diff_info_0_inst,
  input logic [4:0]  diff_info_0_rdIdx,
  input logic        diff_info_0_wen,
  input logic [31:0] diff_info_0_wdata,
  input logic        diff_info_0_commit,
  input logic        diff_info_0_skip,
  input logic        diff_info_0_csr_wen,
  input logic [31:0] diff_info_0_csr_wdata,
  input logic [11:0] diff_info_0_csr_waddr,
  input logic        diff_info_0_irq_en,

  input logic [31:0] diff_info_1_pc,
  input logic [31:0] diff_info_1_npc,
  input logic [31:0] diff_info_1_inst,
  input logic [4:0]  diff_info_1_rdIdx,
  input logic        diff_info_1_wen,
  input logic [31:0] diff_info_1_wdata,
  input logic        diff_info_1_commit,
  input logic        diff_info_1_skip,
  input logic        diff_info_1_csr_wen,
  input logic [31:0] diff_info_1_csr_wdata,
  input logic [11:0] diff_info_1_csr_waddr,
  input logic        diff_info_1_irq_en
  /* verilator lint_on UNUSED */
);

endmodule
