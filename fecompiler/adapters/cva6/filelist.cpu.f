+define+WT_DCACHE

+incdir+../../thirdparty/cva6/core/include
+incdir+../../thirdparty/cva6/corev_apu/axi/include
+incdir+../../thirdparty/cva6/common/submodules/common_cells/include
+incdir+../../thirdparty/cva6/common/submodules/common_cells/src
+incdir+../../thirdparty/cva6/common/local/util
+incdir+../../thirdparty/cva6/core/fpu/src/common_cells/include

../../thirdparty/cva6/core/include/cv32a6_imac_sv32_config_pkg.sv
../../thirdparty/cva6/core/include/riscv_pkg.sv
../../thirdparty/cva6/corev_apu/riscv-dbg/src/dm_pkg.sv
../../thirdparty/cva6/core/include/ariane_pkg.sv
../../thirdparty/cva6/corev_apu/axi/src/axi_pkg.sv
../../thirdparty/cva6/core/include/ariane_rvfi_pkg.sv
../../thirdparty/cva6/core/include/ariane_axi_pkg.sv
../../thirdparty/cva6/core/include/wt_cache_pkg.sv
../../thirdparty/cva6/core/include/std_cache_pkg.sv
../../thirdparty/cva6/core/include/axi_intf.sv
../../thirdparty/cva6/core/include/instr_tracer_pkg.sv
../../thirdparty/cva6/core/include/cvxif_pkg.sv

../../thirdparty/cva6/common/submodules/common_cells/src/cf_math_pkg.sv
../../thirdparty/cva6/common/submodules/common_cells/src/fifo_v3.sv
../../thirdparty/cva6/common/submodules/common_cells/src/lfsr.sv
../../thirdparty/cva6/common/submodules/common_cells/src/lzc.sv
../../thirdparty/cva6/common/submodules/common_cells/src/rr_arb_tree.sv
../../thirdparty/cva6/common/submodules/common_cells/src/shift_reg.sv
../../thirdparty/cva6/common/submodules/common_cells/src/unread.sv
../../thirdparty/cva6/common/submodules/common_cells/src/popcount.sv
../../thirdparty/cva6/common/submodules/common_cells/src/exp_backoff.sv

../../thirdparty/cva6/core/fpu/src/fpnew_pkg.sv
../../thirdparty/cva6/core/fpu/src/fpnew_cast_multi.sv
../../thirdparty/cva6/core/fpu/src/fpnew_classifier.sv
../../thirdparty/cva6/core/fpu/src/fpnew_divsqrt_multi.sv
../../thirdparty/cva6/core/fpu/src/fpnew_fma_multi.sv
../../thirdparty/cva6/core/fpu/src/fpnew_fma.sv
../../thirdparty/cva6/core/fpu/src/fpnew_noncomp.sv
../../thirdparty/cva6/core/fpu/src/fpnew_opgroup_block.sv
../../thirdparty/cva6/core/fpu/src/fpnew_opgroup_fmt_slice.sv
../../thirdparty/cva6/core/fpu/src/fpnew_opgroup_multifmt_slice.sv
../../thirdparty/cva6/core/fpu/src/fpnew_rounding.sv
../../thirdparty/cva6/core/fpu/src/fpnew_top.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/defs_div_sqrt_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/control_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/div_sqrt_top_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/iteration_div_sqrt_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/norm_div_sqrt_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/nrbd_nrsc_mvp.sv
../../thirdparty/cva6/core/fpu/src/fpu_div_sqrt_mvp/hdl/preprocess_mvp.sv

../../thirdparty/cva6/core/cvxif_fu.sv
../../thirdparty/cva6/core/ariane.sv
../../thirdparty/cva6/core/cva6.sv
../../thirdparty/cva6/core/alu.sv
../../thirdparty/cva6/core/fpu_wrap.sv
../../thirdparty/cva6/core/branch_unit.sv
../../thirdparty/cva6/core/compressed_decoder.sv
../../thirdparty/cva6/core/controller.sv
../../thirdparty/cva6/core/csr_buffer.sv
../../thirdparty/cva6/core/csr_regfile.sv
../../thirdparty/cva6/core/decoder.sv
../../thirdparty/cva6/core/ex_stage.sv
../../thirdparty/cva6/core/instr_realign.sv
../../thirdparty/cva6/core/id_stage.sv
../../thirdparty/cva6/core/issue_read_operands.sv
../../thirdparty/cva6/core/issue_stage.sv
../../thirdparty/cva6/core/load_unit.sv
../../thirdparty/cva6/core/load_store_unit.sv
../../thirdparty/cva6/core/lsu_bypass.sv
../../thirdparty/cva6/core/mult.sv
../../thirdparty/cva6/core/multiplier.sv
../../thirdparty/cva6/core/serdiv.sv
../../thirdparty/cva6/core/perf_counters.sv
../../thirdparty/cva6/core/ariane_regfile_ff.sv
../../thirdparty/cva6/core/re_name.sv
../../thirdparty/cva6/core/scoreboard.sv
../../thirdparty/cva6/core/store_buffer.sv
../../thirdparty/cva6/core/amo_buffer.sv
../../thirdparty/cva6/core/store_unit.sv
../../thirdparty/cva6/core/commit_stage.sv
../../thirdparty/cva6/core/axi_shim.sv

../../thirdparty/cva6/core/frontend/btb.sv
../../thirdparty/cva6/core/frontend/bht.sv
../../thirdparty/cva6/core/frontend/ras.sv
../../thirdparty/cva6/core/frontend/instr_scan.sv
../../thirdparty/cva6/core/frontend/instr_queue.sv
../../thirdparty/cva6/core/frontend/frontend.sv

../../thirdparty/cva6/core/cache_subsystem/wt_dcache_ctrl.sv
../../thirdparty/cva6/core/cache_subsystem/wt_dcache_mem.sv
../../thirdparty/cva6/core/cache_subsystem/wt_dcache_missunit.sv
../../thirdparty/cva6/core/cache_subsystem/wt_dcache_wbuffer.sv
../../thirdparty/cva6/core/cache_subsystem/wt_dcache.sv
../../thirdparty/cva6/core/cache_subsystem/cva6_icache.sv
../../thirdparty/cva6/core/cache_subsystem/wt_cache_subsystem.sv
../../thirdparty/cva6/core/cache_subsystem/wt_axi_adapter.sv

../../thirdparty/cva6/core/pmp/src/pmp.sv
../../thirdparty/cva6/core/pmp/src/pmp_entry.sv

../../thirdparty/cva6/common/local/util/instr_tracer_if.sv
../../thirdparty/cva6/common/local/util/instr_tracer.sv
../../thirdparty/cva6/common/local/util/tc_sram_wrapper.sv
../../thirdparty/cva6/corev_apu/src/tech_cells_generic/src/rtl/tc_sram.sv
../../thirdparty/cva6/common/local/util/sram.sv

../../thirdparty/cva6/core/mmu_sv32/cva6_mmu_sv32.sv
../../thirdparty/cva6/core/mmu_sv32/cva6_ptw_sv32.sv
../../thirdparty/cva6/core/mmu_sv32/cva6_tlb_sv32.sv

ecos_cva6_cpu_wrapper.sv
../cpu_top_bridge.v
