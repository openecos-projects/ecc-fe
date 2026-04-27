#include "driver/difftest.h"

#include "VysyxSoCTop.h"
#include "VysyxSoCTop___024root.h"

#include <dlfcn.h>
#include <svdpi.h>

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

namespace {

constexpr uint32_t kDefaultResetVector = 0x80000000u;
constexpr size_t kPmemSize = 0x08000000u;
constexpr int kGprNum = 32;
constexpr int kCsrNum = 16;
constexpr uint16_t kCsrMstatus = 0x300;
constexpr uint16_t kCsrMtvec = 0x305;
constexpr uint16_t kCsrMepc = 0x341;
constexpr uint16_t kCsrMcause = 0x342;
constexpr uint16_t kCsrMtval = 0x343;
constexpr uint16_t kCsrMarchid = 0xf12;
constexpr uint16_t kCsrMimpid = 0xf13;
constexpr uint32_t kInstEcall = 0x00000073u;
constexpr uint32_t kInstMret = 0x30200073u;
constexpr uint32_t kSocUartData = 0x10000000u;
constexpr uint32_t kNemuSerialData = 0xa00003f8u;

enum DiffDirection {
  kDiffTestToDut = 0,
  kDiffTestToRef = 1,
};

struct CoreContext {
  uint32_t gpr[kGprNum];
  uint32_t csr[kCsrNum];
  uint32_t pc;
};

using RefMemcpy = void (*)(unsigned int addr, void *buf, size_t n, bool direction);
using RefRegcpy = void (*)(void *dut, bool direction);
using RefExec = void (*)(uint64_t n);
using RefInit = void (*)(int port);

CoreContext g_dut = {};
CoreContext g_ref = {};
const VysyxSoCTop *g_top = nullptr;
void *g_ref_handle = nullptr;
RefMemcpy g_ref_memcpy = nullptr;
RefRegcpy g_ref_regcpy = nullptr;
RefExec g_ref_exec = nullptr;
uint32_t g_reset_vector = kDefaultResetVector;
bool g_enabled = false;
bool g_started = false;
bool g_waiting_printed = false;
std::vector<uint8_t> g_ref_image;

#define SOC_ROOT_FIELD(name) \
  ysyxSoCTop__DOT__dut__DOT__asic__DOT__cpu__DOT__cpu__DOT__u_core__DOT__cl3_top__DOT__##name

[[noreturn]] void fatal(const char *msg) {
  std::fprintf(stderr, "[soc-sim][difftest] fatal: %s\n", msg);
  std::exit(1);
}

[[noreturn]] void fatal2(const char *msg, const char *arg) {
  std::fprintf(stderr, "[soc-sim][difftest] fatal: %s%s\n", msg, arg == nullptr ? "" : arg);
  std::exit(1);
}

template <typename T>
const T *array_ptr(svOpenArrayHandle handle) {
  return static_cast<const T *>(svGetArrayPtr(handle));
}

bool load_payload_image(const char *path, uint32_t image_offset) {
  if (path == nullptr || path[0] == '\0') {
    fatal("missing --image for difftest");
  }

  std::ifstream ifs(path, std::ios::binary);
  if (!ifs) {
    fatal2("failed to open image: ", path);
  }

  std::vector<uint8_t> image{std::istreambuf_iterator<char>(ifs), std::istreambuf_iterator<char>()};
  if (image_offset > image.size()) {
    std::fprintf(stderr,
                 "[soc-sim][difftest] image offset 0x%08x is past end of %s (%zu bytes)\n",
                 image_offset,
                 path,
                 image.size());
    return false;
  }

  const size_t payload_size = image.size() - image_offset;
  if (payload_size > kPmemSize) {
    std::fprintf(stderr,
                 "[soc-sim][difftest] payload too large: %zu bytes (max %zu)\n",
                 payload_size,
                 kPmemSize);
    return false;
  }

  g_ref_image.assign(image.begin() + image_offset, image.end());
  std::fprintf(stderr,
               "[soc-sim][difftest] image: %s, offset=0x%08x, payload=%zu bytes, ref_base=0x%08x\n",
               path,
               image_offset,
               g_ref_image.size(),
               g_reset_vector);
  return true;
}

void *load_symbol(const char *name) {
  dlerror();
  void *symbol = dlsym(g_ref_handle, name);
  const char *err = dlerror();
  if (err != nullptr || symbol == nullptr) {
    std::fprintf(stderr, "[soc-sim][difftest] missing symbol %s: %s\n", name, err == nullptr ? "" : err);
    std::exit(1);
  }
  return symbol;
}

uint32_t read_gpr(int idx) {
  assert(g_top != nullptr);
  const auto *root = g_top->rootp;
  switch (idx) {
    case 0:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_0);
    case 1:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_1);
    case 2:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_2);
    case 3:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_3);
    case 4:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_4);
    case 5:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_5);
    case 6:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_6);
    case 7:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_7);
    case 8:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_8);
    case 9:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_9);
    case 10:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_10);
    case 11:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_11);
    case 12:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_12);
    case 13:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_13);
    case 14:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_14);
    case 15:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_15);
    case 16:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_16);
    case 17:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_17);
    case 18:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_18);
    case 19:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_19);
    case 20:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_20);
    case 21:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_21);
    case 22:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_22);
    case 23:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_23);
    case 24:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_24);
    case 25:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_25);
    case 26:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_26);
    case 27:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_27);
    case 28:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_28);
    case 29:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_29);
    case 30:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_30);
    case 31:
      return root->SOC_ROOT_FIELD(core__DOT__issue__DOT__rf__DOT__regs_31);
    default:
      return 0;
  }
}

void update_dut_state() {
  assert(g_top != nullptr);
  const auto *root = g_top->rootp;
  for (int i = 0; i < kGprNum; ++i) {
    g_dut.gpr[i] = read_gpr(i);
  }
  g_dut.csr[0] = root->SOC_ROOT_FIELD(core__DOT__csr__DOT__csr_rf__DOT__csr_mepc_q);
  g_dut.csr[1] = root->SOC_ROOT_FIELD(core__DOT__csr__DOT__csr_rf__DOT__csr_mcause_q);
  g_dut.csr[2] = root->SOC_ROOT_FIELD(core__DOT__csr__DOT__csr_rf__DOT__csr_mtvec_q);
  g_dut.csr[3] = root->SOC_ROOT_FIELD(core__DOT__csr__DOT__csr_rf__DOT__csr_sr_q);
  g_dut.csr[4] = root->SOC_ROOT_FIELD(core__DOT__csr__DOT__csr_rf__DOT__csr_mtval_q);
}

const char *gpr_name(int idx) {
  static const char *names[kGprNum] = {
      "x0/zero", "x1/ra", "x2/sp", "x3/gp", "x4/tp",  "x5/t0",  "x6/t1",  "x7/t2",
      "x8/s0",   "x9/s1", "x10/a0", "x11/a1", "x12/a2", "x13/a3", "x14/a4", "x15/a5",
      "x16/a6",  "x17/a7", "x18/s2", "x19/s3", "x20/s4", "x21/s5", "x22/s6", "x23/s7",
      "x24/s8",  "x25/s9", "x26/s10", "x27/s11", "x28/t3", "x29/t4", "x30/t5", "x31/t6"};
  return (idx >= 0 && idx < kGprNum) ? names[idx] : "x?";
}

void dump_regs() {
  std::fprintf(stderr, "[DIFFTEST] ===== GPR (DUT vs REF) =====\n");
  for (int i = 0; i < kGprNum; ++i) {
    const uint32_t dut = g_dut.gpr[i];
    const uint32_t ref = g_ref.gpr[i];
    if (dut != ref) {
      std::fprintf(stderr, "  %-8s : DUT=0x%08x REF=0x%08x <== MISMATCH\n", gpr_name(i), dut, ref);
    } else {
      std::fprintf(stderr, "  %-8s : DUT=0x%08x REF=0x%08x\n", gpr_name(i), dut, ref);
    }
  }
}

void dump_slots(int n,
                const uint32_t *pc,
                const uint32_t *npc,
                const uint32_t *inst,
                const uint16_t *commit,
                const uint16_t *irq_en) {
  for (int i = 0; i < n; ++i) {
    std::fprintf(stderr,
                 "[DIFFTEST] slot[%d]: commit=%u irq_en=%u pc=0x%08x npc=0x%08x inst=0x%08x\n",
                 i,
                 static_cast<unsigned>(commit[i]),
                 static_cast<unsigned>(irq_en[i]),
                 pc[i],
                 npc[i],
                 inst[i]);
  }
}

bool is_boot_or_mrom_pc(uint32_t pc) {
  return pc < g_reset_vector;
}

bool should_skip_csr(uint16_t csr) {
  return csr == kCsrMstatus || csr == kCsrMtvec || csr == kCsrMepc || csr == kCsrMcause ||
         csr == kCsrMtval || csr == kCsrMarchid || csr == kCsrMimpid;
}

bool should_skip_inst(uint32_t instruction) {
  return instruction == kInstEcall || instruction == kInstMret;
}

int32_t decode_store_imm(uint32_t instruction) {
  const uint32_t imm = ((instruction >> 7) & 0x1fu) | (((instruction >> 25) & 0x7fu) << 5);
  return static_cast<int32_t>(imm << 20) >> 20;
}

bool should_skip_mmio_store(uint32_t instruction) {
  if ((instruction & 0x7fu) != 0x23u) {
    return false;
  }
  const uint32_t rs1 = (instruction >> 15) & 0x1fu;
  const uint32_t addr = read_gpr(static_cast<int>(rs1)) + static_cast<uint32_t>(decode_store_imm(instruction));
  return addr == kSocUartData || addr == kNemuSerialData;
}

}  // namespace

void difftest_configure(const VysyxSoCTop *top,
                        const char *ref_so_file,
                        const char *image_file,
                        uint32_t image_offset,
                        uint32_t reset_vector) {
  if (top == nullptr) {
    fatal("top is null");
  }
  if (ref_so_file == nullptr || ref_so_file[0] == '\0') {
    fatal("missing reference shared object");
  }
  if (reset_vector == 0) {
    reset_vector = kDefaultResetVector;
  }

  g_top = top;
  g_reset_vector = reset_vector;
  g_ref_handle = dlopen(ref_so_file, RTLD_LAZY | RTLD_LOCAL);
  if (g_ref_handle == nullptr) {
    fatal2("dlopen failed: ", dlerror());
  }

  g_ref_memcpy = reinterpret_cast<RefMemcpy>(load_symbol("difftest_memcpy"));
  g_ref_regcpy = reinterpret_cast<RefRegcpy>(load_symbol("difftest_regcpy"));
  g_ref_exec = reinterpret_cast<RefExec>(load_symbol("difftest_exec"));
  RefInit ref_init = reinterpret_cast<RefInit>(load_symbol("difftest_init"));

  if (!load_payload_image(image_file, image_offset)) {
    std::exit(1);
  }

  std::memset(&g_dut, 0, sizeof(g_dut));
  std::memset(&g_ref, 0, sizeof(g_ref));
  g_dut.pc = g_reset_vector;
  g_ref.pc = g_reset_vector;
  g_dut.csr[3] = 0x1800u;
  g_ref.csr[3] = 0x1800u;

  std::fprintf(stderr, "[soc-sim][difftest] ref: %s\n", ref_so_file);
  ref_init(80);
  if (!g_ref_image.empty()) {
    g_ref_memcpy(g_reset_vector, g_ref_image.data(), g_ref_image.size(), kDiffTestToRef);
  }
  g_ref_regcpy(&g_ref, kDiffTestToRef);
  g_enabled = true;
  g_started = false;
  g_waiting_printed = false;
  std::fprintf(stderr, "[soc-sim][difftest] enabled\n");
}

bool difftest_enabled() {
  return g_enabled;
}

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
  if (!g_enabled) {
    return 0;
  }

  const uint32_t *pc = array_ptr<uint32_t>(pc_h);
  const uint32_t *npc = array_ptr<uint32_t>(npc_h);
  const uint32_t *inst = array_ptr<uint32_t>(inst_h);
  const uint16_t *rdidx = array_ptr<uint16_t>(rdidx_h);
  const uint16_t *wen = array_ptr<uint16_t>(wen_h);
  const uint32_t *wdata = array_ptr<uint32_t>(wdata_h);
  const uint16_t *commit = array_ptr<uint16_t>(commit_h);
  const uint16_t *skip = array_ptr<uint16_t>(skip_h);
  const uint16_t *csr_wen = array_ptr<uint16_t>(csr_wen_h);
  const uint32_t *csr_wdata = array_ptr<uint32_t>(csr_wdata_h);
  const uint16_t *csr_waddr = array_ptr<uint16_t>(csr_waddr_h);
  const uint16_t *irq_en = array_ptr<uint16_t>(irq_en_h);

  if (pc == nullptr || npc == nullptr || inst == nullptr || rdidx == nullptr || wen == nullptr ||
      wdata == nullptr || commit == nullptr || skip == nullptr || csr_wen == nullptr ||
      csr_wdata == nullptr || csr_waddr == nullptr || irq_en == nullptr) {
    std::fprintf(stderr, "[soc-sim][difftest] null DPI open array\n");
    return 1;
  }

  bool saw_commit = false;
  for (int i = 0; i < n; ++i) {
    if (commit[i] == 0) {
      continue;
    }
    saw_commit = true;

    if (is_boot_or_mrom_pc(pc[i])) {
      if (!g_waiting_printed) {
        std::fprintf(stderr,
                     "[soc-sim][difftest] skipping low boot PC 0x%08x; compare starts at >= 0x%08x\n",
                     pc[i],
                     g_reset_vector);
        g_waiting_printed = true;
      }
      continue;
    }

    if (!g_started) {
      g_started = true;
      std::fprintf(stderr, "[soc-sim][difftest] compare starts at pc=0x%08x\n", pc[i]);
      update_dut_state();
      uint32_t last_npc = npc[i];
      for (int j = i + 1; j < n; ++j) {
        if (commit[j] != 0 && !is_boot_or_mrom_pc(pc[j])) {
          last_npc = npc[j];
        }
      }
      g_dut.pc = last_npc;
      g_ref_regcpy(&g_dut, kDiffTestToRef);
      g_ref_regcpy(&g_ref, kDiffTestToDut);
      return 0;
    }

    if (skip[i] != 0 || should_skip_inst(inst[i]) || should_skip_csr(csr_waddr[i]) ||
        should_skip_mmio_store(inst[i]) || irq_en[i] != 0) {
      update_dut_state();
      uint32_t last_npc = npc[i];
      for (int j = i + 1; j < n; ++j) {
        if (commit[j] != 0 && !is_boot_or_mrom_pc(pc[j])) {
          last_npc = npc[j];
        }
      }
      g_dut.pc = last_npc;
      g_ref_regcpy(&g_dut, kDiffTestToRef);
      g_ref_regcpy(&g_ref, kDiffTestToDut);
      break;
    }

    try {
      g_ref_exec(1);
      g_ref_regcpy(&g_ref, kDiffTestToDut);
    } catch (...) {
      std::fprintf(stderr, "[DIFFTEST] reference threw while executing DUT pc=0x%08x inst=0x%08x\n", pc[i], inst[i]);
      return 1;
    }

    bool mismatch = false;
    if (g_ref.pc != npc[i]) {
      std::fprintf(stderr,
                   "[DIFFTEST] PC mismatch at DUT pc=0x%08x: DUT npc=0x%08x REF pc=0x%08x\n",
                   pc[i],
                   npc[i],
                   g_ref.pc);
      mismatch = true;
    }
    if (wen[i] != 0 && rdidx[i] != 0 && wdata[i] != g_ref.gpr[rdidx[i]]) {
      std::fprintf(stderr,
                   "[DIFFTEST] GPR mismatch at pc=0x%08x: rd=%u DUT=0x%08x REF=0x%08x\n",
                   pc[i],
                   static_cast<unsigned>(rdidx[i]),
                   wdata[i],
                   g_ref.gpr[rdidx[i]]);
      mismatch = true;
    }
    if (mismatch) {
      update_dut_state();
      dump_slots(n, pc, npc, inst, commit, irq_en);
      dump_regs();
      return 1;
    }
  }

  if (!saw_commit || !g_started) {
    return 0;
  }

  update_dut_state();
  uint32_t gpr_mask = 0;
  for (int i = 0; i < kGprNum; ++i) {
    if (g_dut.gpr[i] != g_ref.gpr[i]) {
      std::fprintf(stderr, "[DIFFTEST] GPR[%d]: DUT=0x%08x REF=0x%08x\n", i, g_dut.gpr[i], g_ref.gpr[i]);
      gpr_mask |= (1U << i);
    }
  }

  if (gpr_mask != 0) {
    std::fprintf(stderr, "[DIFFTEST] Mismatch in double check: DUT state changed unexpectedly.\n");
    dump_slots(n, pc, npc, inst, commit, irq_en);
    dump_regs();
    return 1;
  }

  (void)csr_wen;
  (void)csr_wdata;
  return 0;
}

#undef SOC_ROOT_FIELD
