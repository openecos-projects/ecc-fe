#ifndef FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_
#define FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_

#include <cstdint>
#include <cstddef>

class VysyxSoCTop;

void difftest_configure(const VysyxSoCTop *top,
                        const char *ref_so_file,
                        const char *image_file,
                        uint32_t image_offset,
                        uint32_t reset_vector);
bool difftest_enabled();
void difftest_dump_progress();

#endif  // FE_COMPILER_THIRDPARTY_SOC_DRIVER_DIFFTEST_H_
