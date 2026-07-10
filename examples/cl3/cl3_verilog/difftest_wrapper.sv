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

  import "DPI-C" function int difftest_step(
    int n,
    int unsigned      pc       [],
    int unsigned      npc      [],
    int unsigned      inst     [],
    shortint unsigned rd_idx   [],
    shortint unsigned wen      [],
    int unsigned      wdata    [],
    shortint unsigned commit   [],
    shortint unsigned skip     [],
    shortint unsigned csr_wen  [],
    int unsigned      csr_wdata[],
    shortint unsigned csr_waddr[],
    shortint unsigned irq_en   []
  );

  localparam int COMMIT_PORTS = 2;

  int unsigned      pc_q       [COMMIT_PORTS];
  int unsigned      npc_q      [COMMIT_PORTS];
  int unsigned      inst_q     [COMMIT_PORTS];
  shortint unsigned rd_idx_q   [COMMIT_PORTS];
  shortint unsigned wen_q      [COMMIT_PORTS];
  int unsigned      wdata_q    [COMMIT_PORTS];
  shortint unsigned commit_q   [COMMIT_PORTS];
  shortint unsigned skip_q     [COMMIT_PORTS];
  shortint unsigned csr_wen_q  [COMMIT_PORTS];
  int unsigned      csr_wdata_q[COMMIT_PORTS];
  shortint unsigned csr_waddr_q[COMMIT_PORTS];
  shortint unsigned irq_en_q   [COMMIT_PORTS];
  int difftest_result;

  always_ff @(posedge clock) begin
    if (reset) begin
      for (int i = 0; i < COMMIT_PORTS; i++) begin
        pc_q[i]        <= '0;
        npc_q[i]       <= '0;
        inst_q[i]      <= '0;
        rd_idx_q[i]    <= '0;
        wen_q[i]       <= '0;
        wdata_q[i]     <= '0;
        commit_q[i]    <= '0;
        skip_q[i]      <= '0;
        csr_wen_q[i]   <= '0;
        csr_wdata_q[i] <= '0;
        csr_waddr_q[i] <= '0;
        irq_en_q[i]    <= '0;
      end
    end else begin
      pc_q[0]        <= diff_info_0_pc;
      npc_q[0]       <= diff_info_0_npc;
      inst_q[0]      <= diff_info_0_inst;
      rd_idx_q[0]    <= {11'b0, diff_info_0_rdIdx};
      wen_q[0]       <= {15'b0, diff_info_0_wen};
      wdata_q[0]     <= diff_info_0_wdata;
      commit_q[0]    <= {15'b0, diff_info_0_commit};
      skip_q[0]      <= {15'b0, diff_info_0_skip};
      csr_wen_q[0]   <= {15'b0, diff_info_0_csr_wen};
      csr_wdata_q[0] <= diff_info_0_csr_wdata;
      csr_waddr_q[0] <= {4'b0, diff_info_0_csr_waddr};
      irq_en_q[0]    <= {15'b0, diff_info_0_irq_en};

      pc_q[1]        <= diff_info_1_pc;
      npc_q[1]       <= diff_info_1_npc;
      inst_q[1]      <= diff_info_1_inst;
      rd_idx_q[1]    <= {11'b0, diff_info_1_rdIdx};
      wen_q[1]       <= {15'b0, diff_info_1_wen};
      wdata_q[1]     <= diff_info_1_wdata;
      commit_q[1]    <= {15'b0, diff_info_1_commit};
      skip_q[1]      <= {15'b0, diff_info_1_skip};
      csr_wen_q[1]   <= {15'b0, diff_info_1_csr_wen};
      csr_wdata_q[1] <= diff_info_1_csr_wdata;
      csr_waddr_q[1] <= {4'b0, diff_info_1_csr_waddr};
      irq_en_q[1]    <= {15'b0, diff_info_1_irq_en};

      difftest_result = difftest_step(
        COMMIT_PORTS,
        pc_q,
        npc_q,
        inst_q,
        rd_idx_q,
        wen_q,
        wdata_q,
        commit_q,
        skip_q,
        csr_wen_q,
        csr_wdata_q,
        csr_waddr_q,
        irq_en_q
      );
      if (difftest_result != 0) begin
        $fatal(1, "HIT BAD TRAP: difftest mismatch");
      end
    end
  end

endmodule
