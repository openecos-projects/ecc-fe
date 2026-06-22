#ifndef FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_
#define FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_

#include <cstdint>
#include <cstddef>

class Vecos_sim_top;

void difftest_configure(const Vecos_sim_top *top,
                        const char *ref_so_file,
                        const char *image_file,
                        uint32_t image_offset,
                        uint32_t reset_vector);
bool difftest_enabled();
void difftest_dump_progress();

#endif  // FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_
