#include "driver/dpi_mem.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <svdpi.h>
#include <vector>

namespace {

std::vector<uint8_t> g_mrom_image;
std::vector<uint8_t> g_pmem_image;
constexpr uint32_t kMromBase = 0x20000000u;
constexpr uint32_t kExecBase = 0x80000000u;
constexpr uint32_t kUncachedAliasBase = 0x90000000u;
constexpr uint32_t kBootAliasBase = 0x20000000u;
constexpr size_t kBootAliasSize = 0x00100000u;
constexpr size_t kPmemSize = 0x08000000u;
bool g_difftest_warned = false;
int g_mrom_log_cnt = 0;
int g_mem_r_log_cnt = 0;
int g_mem_w_log_cnt = 0;
int g_psram_r_log_cnt = 0;
int g_psram_w_log_cnt = 0;
int g_diff_calls = 0;
int g_diff_commit_log_cnt = 0;
bool g_seen_boot_jump = false;
bool g_seen_payload_pc = false;
constexpr int kDiffCommitLogLimit = 64;

struct DiffInfoPacked {
  uint16_t csr_waddr;
  uint32_t csr_wdata;
  uint16_t csr_wen;
  uint16_t skip;
  uint16_t commit;
  uint32_t wdata;
  uint16_t wen;
  uint16_t rdIdx;
  uint32_t inst;
  uint32_t npc;
  uint32_t pc;
} __attribute__((packed));

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

}  // namespace

void dpi_load_image(const char *path) {
  g_mrom_image.clear();
  g_pmem_image.clear();
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

extern "C" int difftest_step(int n, const void *info) {
  ++g_diff_calls;
  if (!g_difftest_warned) {
    std::fprintf(stderr, "[soc-sim] warning: difftest_step is stubbed (always pass)\n");
    g_difftest_warned = true;
  }
  if (g_diff_calls <= 16) {
    std::fprintf(stderr, "[soc-sim][difftest] call=%d\n", g_diff_calls);
  }
  if (info != nullptr) {
    const auto *arr = static_cast<const DiffInfoPacked *>(
        svGetArrayPtr(reinterpret_cast<svOpenArrayHandle>(const_cast<void *>(info))));
    if (arr == nullptr) {
      return 0;
    }
    for (int i = 0; i < n; ++i) {
      if (arr[i].commit != 0) {
        if (!g_seen_boot_jump && arr[i].pc == 0x20000040u) {
          std::fprintf(stderr, "[soc-sim][milestone] reached boot jump @0x20000040\n");
          g_seen_boot_jump = true;
        }
        if (!g_seen_payload_pc && arr[i].pc >= 0x80000000u && arr[i].pc < 0x80000100u) {
          std::fprintf(stderr, "[soc-sim][milestone] entered payload pc=0x%08x\n", arr[i].pc);
          g_seen_payload_pc = true;
        }
        if (g_diff_commit_log_cnt < kDiffCommitLogLimit) {
          std::fprintf(
              stderr,
              "[soc-sim][commit] pc=0x%08x npc=0x%08x inst=0x%08x rd=%u wen=%u wdata=0x%08x skip=%u\n",
              arr[i].pc,
              arr[i].npc,
              arr[i].inst,
              static_cast<unsigned>(arr[i].rdIdx),
              static_cast<unsigned>(arr[i].wen),
              arr[i].wdata,
              static_cast<unsigned>(arr[i].skip));
        }
        ++g_diff_commit_log_cnt;
      }
    }
  }
  return 0;
}
