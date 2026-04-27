#include "driver/dpi_mem.h"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <vector>

namespace {

std::vector<uint8_t> g_mrom_image;
std::vector<uint8_t> g_pmem_image;
bool g_pmem_preloaded_from_payload = false;
constexpr uint32_t kMromBase = 0x20000000u;
constexpr uint32_t kExecBase = 0x80000000u;
constexpr uint32_t kUncachedAliasBase = 0x90000000u;
constexpr uint32_t kBootAliasBase = 0x20000000u;
constexpr uint32_t kSocUartData = 0x10000000u;
constexpr uint32_t kNemuSerialData = 0xa00003f8u;
constexpr size_t kBootAliasSize = 0x00100000u;
constexpr size_t kPmemSize = 0x08000000u;
int g_mrom_log_cnt = 0;
int g_mem_r_log_cnt = 0;
int g_mem_w_log_cnt = 0;
int g_psram_r_log_cnt = 0;
int g_psram_w_log_cnt = 0;

void ensure_pmem_size(uint32_t offset, size_t width) {
  if (offset >= kPmemSize) {
    return;
  }
  const size_t required = static_cast<size_t>(offset) + width;
  const size_t capped = required > kPmemSize ? kPmemSize : required;
  if (g_pmem_image.size() < capped) {
    g_pmem_image.resize(capped, 0);
  }
}

uint32_t read_u32_le(const std::vector<uint8_t> &image, uint32_t offset) {
  uint32_t value = 0;
  for (uint32_t i = 0; i < 4; ++i) {
    const uint32_t idx = offset + i;
    if (idx < image.size()) {
      value |= static_cast<uint32_t>(image[idx]) << (8 * i);
    }
  }
  return value;
}

bool translate_pmem_addr(uint32_t addr, uint32_t *offset) {
  if (offset == nullptr) {
    return false;
  }
  if (addr >= kExecBase && static_cast<uint64_t>(addr) < static_cast<uint64_t>(kExecBase) + kPmemSize) {
    *offset = addr - kExecBase;
    return true;
  }
  if (addr >= kUncachedAliasBase && static_cast<uint64_t>(addr) < static_cast<uint64_t>(kUncachedAliasBase) + kPmemSize) {
    *offset = addr - kUncachedAliasBase;
    return true;
  }
  if (addr >= kBootAliasBase && static_cast<uint64_t>(addr) < static_cast<uint64_t>(kBootAliasBase) + kBootAliasSize) {
    *offset = addr - kBootAliasBase;
    return true;
  }
  return false;
}

bool write_serial_byte(uint32_t addr, uint32_t mask, uint32_t data) {
  if (addr != kSocUartData && addr != kNemuSerialData) {
    return false;
  }

  uint32_t byte = 0;
  for (; byte < 4; ++byte) {
    if ((mask >> byte) & 0x1u) {
      break;
    }
  }
  if (byte == 4) {
    byte = 0;
  }

  const int ch = static_cast<int>((data >> (8u * byte)) & 0xffu);
  std::fputc(ch, stdout);
  std::fflush(stdout);
  return true;
}

}  // namespace

void dpi_load_image(const char *path) {
  g_mrom_image.clear();
  g_pmem_image.clear();
  g_pmem_preloaded_from_payload = false;
  if (path == nullptr || path[0] == '\0') {
    std::fprintf(stderr, "[soc-sim] no --image provided, using zeroed memory\n");
    return;
  }

  std::ifstream ifs(path, std::ios::binary);
  if (!ifs) {
    std::fprintf(stderr, "[soc-sim] failed to open image: %s\n", path);
    std::exit(1);
  }

  g_mrom_image.assign(std::istreambuf_iterator<char>(ifs), std::istreambuf_iterator<char>());
  const size_t preload_size = g_mrom_image.size() > kPmemSize ? kPmemSize : g_mrom_image.size();
  g_pmem_image.assign(g_mrom_image.begin(), g_mrom_image.begin() + preload_size);
  std::fprintf(stderr, "[soc-sim] loaded image: %s (%zu bytes)\n", path, g_mrom_image.size());
}

void dpi_preload_pmem_from_image(const char *path, unsigned int image_offset) {
  if (path == nullptr || path[0] == '\0') {
    return;
  }

  std::ifstream ifs(path, std::ios::binary);
  if (!ifs) {
    std::fprintf(stderr, "[soc-sim] failed to open image for pmem preload: %s\n", path);
    std::exit(1);
  }

  std::vector<uint8_t> image{std::istreambuf_iterator<char>(ifs), std::istreambuf_iterator<char>()};
  if (static_cast<size_t>(image_offset) > image.size()) {
    std::fprintf(stderr,
                 "[soc-sim] pmem preload offset 0x%08x is past end of image (%zu bytes): %s\n",
                 image_offset,
                 image.size(),
                 path);
    std::exit(1);
  }

  const auto begin = image.begin() + static_cast<std::ptrdiff_t>(image_offset);
  const size_t payload_size = static_cast<size_t>(image.end() - begin);
  const size_t preload_size = payload_size > kPmemSize ? kPmemSize : payload_size;
  g_pmem_image.assign(begin, begin + static_cast<std::ptrdiff_t>(preload_size));
  g_pmem_preloaded_from_payload = true;
  std::fprintf(stderr,
               "[soc-sim] preloaded pmem from image offset 0x%08x (%zu bytes)\n",
               image_offset,
               g_pmem_image.size());
}

extern "C" void flash_read(int addr, int *data) {
  if (data == nullptr) {
    return;
  }
  *data = static_cast<int>(read_u32_le(g_mrom_image, static_cast<uint32_t>(addr)));
}

extern "C" void mrom_read(int raddr, int *rdata) {
  if (rdata == nullptr) {
    return;
  }
  const uint32_t addr = static_cast<uint32_t>(raddr);
  // Some RTL paths provide absolute address (>= kMromBase), others provide
  // local offset directly. Support both forms.
  const uint32_t offset = (addr >= kMromBase) ? (addr - kMromBase) : addr;
  *rdata = static_cast<int>(read_u32_le(g_mrom_image, offset));
  if (g_mrom_log_cnt < 32) {
    std::fprintf(stderr, "[soc-sim][mrom] addr=0x%08x data=0x%08x\n", addr, static_cast<uint32_t>(*rdata));
    ++g_mrom_log_cnt;
  }
}

extern "C" void psram_read(int addr, int *data) {
  if (data == nullptr) {
    return;
  }
  const uint32_t offset = static_cast<uint32_t>(addr) & 0x00ffffffu;
  *data = static_cast<int>(read_u32_le(g_pmem_image, offset));
  if (g_psram_r_log_cnt < 64) {
    std::fprintf(stderr, "[soc-sim][psram-r] off=0x%08x data=0x%08x\n", offset, static_cast<uint32_t>(*data));
    ++g_psram_r_log_cnt;
  }
}

extern "C" void psram_write(int addr, int mask, int data) {
  const uint32_t offset = static_cast<uint32_t>(addr) & 0x00ffffffu;
  if (offset >= kPmemSize) {
    return;
  }
  ensure_pmem_size(offset, 4);
  for (uint32_t byte = 0; byte < 4; ++byte) {
    if ((static_cast<uint32_t>(mask) >> byte) & 0x1u) {
      const size_t idx = static_cast<size_t>(offset) + byte;
      if (idx < g_pmem_image.size()) {
        g_pmem_image[idx] = static_cast<uint8_t>((static_cast<uint32_t>(data) >> (8u * byte)) & 0xffu);
      }
    }
  }
  if (g_psram_w_log_cnt < 128 || offset == 0x0000000cU) {
    std::fprintf(stderr, "[soc-sim][psram-w] off=0x%08x mask=0x%x data=0x%08x\n",
                 offset,
                 static_cast<uint32_t>(mask),
                 static_cast<uint32_t>(data));
    ++g_psram_w_log_cnt;
  }
}

extern "C" long long mem_read(unsigned int raddr, unsigned int size) {
  if (g_pmem_preloaded_from_payload && raddr >= kBootAliasBase &&
      static_cast<uint64_t>(raddr) < static_cast<uint64_t>(kBootAliasBase) + kBootAliasSize) {
    const uint32_t offset = raddr - kBootAliasBase;
    const size_t width = (size == 3u) ? 8u : 4u;
    uint64_t value = 0;
    for (size_t i = 0; i < width; ++i) {
      const size_t idx = static_cast<size_t>(offset) + i;
      if (idx < g_mrom_image.size()) {
        value |= static_cast<uint64_t>(g_mrom_image[idx]) << (8u * i);
      }
    }
    if (g_mem_r_log_cnt < 32) {
      std::fprintf(stderr, "[soc-sim][mem-r] addr=0x%08x size=%u data=0x%llx\n", raddr, size, static_cast<unsigned long long>(value));
      ++g_mem_r_log_cnt;
    }
    return static_cast<long long>(value);
  }

  uint32_t offset = 0;
  if (!translate_pmem_addr(raddr, &offset)) {
    return 0;
  }
  const size_t width = (size == 3u) ? 8u : 4u;
  if (static_cast<size_t>(offset) >= g_pmem_image.size()) {
    return 0;
  }

  uint64_t value = 0;
  for (size_t i = 0; i < width; ++i) {
    const size_t idx = static_cast<size_t>(offset) + i;
    if (idx < g_pmem_image.size()) {
      value |= static_cast<uint64_t>(g_pmem_image[idx]) << (8u * i);
    }
  }
  if (g_mem_r_log_cnt < 32) {
    std::fprintf(stderr, "[soc-sim][mem-r] addr=0x%08x size=%u data=0x%llx\n", raddr, size, static_cast<unsigned long long>(value));
    ++g_mem_r_log_cnt;
  }
  return static_cast<long long>(value);
}

extern "C" void mem_write(unsigned int waddr, unsigned int mask, unsigned int wdata) {
  if (write_serial_byte(waddr, mask, wdata)) {
    return;
  }

  if (g_pmem_preloaded_from_payload && waddr >= kBootAliasBase &&
      static_cast<uint64_t>(waddr) < static_cast<uint64_t>(kBootAliasBase) + kBootAliasSize) {
    return;
  }

  uint32_t offset = 0;
  if (!translate_pmem_addr(waddr, &offset)) {
    return;
  }
  if (offset >= kPmemSize) {
    return;
  }
  ensure_pmem_size(offset, 4);
  for (uint32_t byte = 0; byte < 4; ++byte) {
    if ((mask >> byte) & 0x1u) {
      const size_t idx = static_cast<size_t>(offset) + byte;
      if (idx < g_pmem_image.size()) {
        g_pmem_image[idx] = static_cast<uint8_t>((wdata >> (8u * byte)) & 0xffu);
      }
    }
  }
  if (g_mem_w_log_cnt < 64 || waddr == 0x1000000cU) {
    std::fprintf(stderr, "[soc-sim][mem-w] addr=0x%08x mask=0x%x data=0x%08x\n", waddr, mask, wdata);
    ++g_mem_w_log_cnt;
  }
}
