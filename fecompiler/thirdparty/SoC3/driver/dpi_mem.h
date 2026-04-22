#ifndef BAZEL_SOC_BIN_SIM_DPI_MEM_H_
#define BAZEL_SOC_BIN_SIM_DPI_MEM_H_

void dpi_load_image(const char *path);

extern "C" void flash_read(int addr, int *data);
extern "C" void mrom_read(int raddr, int *rdata);
extern "C" void psram_read(int addr, int *data);
extern "C" void psram_write(int addr, int mask, int data);
extern "C" long long mem_read(unsigned int raddr, unsigned int size);
extern "C" void mem_write(unsigned int waddr, unsigned int mask, unsigned int wdata);
extern "C" int difftest_step(int n, const void *info);

#endif  // BAZEL_SOC_BIN_SIM_DPI_MEM_H_
