#include "driver/difftest.h"

#include <svdpi.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>

void difftest_configure(const Vecos_sim_top *top,
                        const char *ref_so_file,
                        const char *image_file,
                        uint32_t image_offset,
                        uint32_t reset_vector) {
  (void)top;
  (void)ref_so_file;
  (void)image_file;
  (void)image_offset;
  (void)reset_vector;
  std::fprintf(stderr, "[soc-sim][difftest] unsupported for this CPU wrapper\n");
  std::exit(1);
}

bool difftest_enabled() {
  return false;
}

bool difftest_check_complete() {
  return false;
}

void difftest_dump_progress() {}

extern "C" int difftest_step(int n,
                             const svOpenArrayHandle pc_h,
                             const svOpenArrayHandle npc_h,
                             const svOpenArrayHandle inst_h,
                             const svOpenArrayHandle rdidx_h,
                             const svOpenArrayHandle wen_h,
                             const svOpenArrayHandle wdata_h,
                             const svOpenArrayHandle commit_h,
                             const svOpenArrayHandle skip_h,
                             const svOpenArrayHandle csr_wen_h,
                             const svOpenArrayHandle csr_wdata_h,
                             const svOpenArrayHandle csr_waddr_h,
                             const svOpenArrayHandle irq_en_h) {
  (void)n;
  (void)pc_h;
  (void)npc_h;
  (void)inst_h;
  (void)rdidx_h;
  (void)wen_h;
  (void)wdata_h;
  (void)commit_h;
  (void)skip_h;
  (void)csr_wen_h;
  (void)csr_wdata_h;
  (void)csr_waddr_h;
  (void)irq_en_h;
  return 0;
}
