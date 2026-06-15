#include "driver/difftest.h"

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
