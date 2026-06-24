#include "Vecos_sim_top.h"
#include "driver/difftest.h"
#include "driver/dpi_mem.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

#include <getopt.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>

#ifndef SOC_DEFAULT_REF_SO
#define SOC_DEFAULT_REF_SO nullptr
#endif

namespace {

struct Args {
  const char *image = nullptr;
  const char *wave = nullptr;
  const char *ref = SOC_DEFAULT_REF_SO;
  uint64_t max_cycles = 2000000;
  uint32_t diff_image_offset = 0;
  uint32_t diff_reset_vector = 0x80000000u;
  bool diff = false;
  bool timeout_ok = false;
};

enum class ParseResult {
  kOk,
  kHelp,
  kError,
};

void print_usage(const char *prog_name) {
  std::cerr << "Usage: " << prog_name << " [options]\n"
            << "Options:\n"
            << "  --image <file>        Boot image for flash/mrom\n"
            << "  --wave <file>         Dump VCD waveform\n"
            << "  --max-cycles <n>      Stop after n cycles (default 2000000)\n"
            << "  --diff                Enable difftest\n"
            << "  --ref <file>          Reference shared object (default built into sim)\n"
            << "  --diff-image-offset <n>  Offset copied to ref memory (hex accepted)\n"
            << "  --diff-reset-vector <n>  Ref reset/base PC (default 0x80000000)\n"
            << "  --timeout-ok          Return success when --max-cycles is reached\n"
            << "  --help                Show this help message\n";
}

bool parse_u64(const char *text, uint64_t *value) {
  if (text == nullptr || value == nullptr) {
    return false;
  }
  char *end = nullptr;
  const unsigned long long parsed = std::strtoull(text, &end, 0);
  if (end == text || *end != '\0') {
    return false;
  }
  *value = static_cast<uint64_t>(parsed);
  return true;
}

ParseResult parse_args(int argc, char **argv, Args *args) {
  enum {
    kOptDiffImageOffset = 1000,
    kOptDiffResetVector,
    kOptTimeoutOk,
  };
  static struct option long_options[] = {
      {"image", required_argument, nullptr, 'i'},
      {"wave", required_argument, nullptr, 'w'},
      {"max-cycles", required_argument, nullptr, 'm'},
      {"diff", no_argument, nullptr, 'd'},
      {"ref", required_argument, nullptr, 'r'},
      {"diff-image-offset", required_argument, nullptr, kOptDiffImageOffset},
      {"diff-reset-vector", required_argument, nullptr, kOptDiffResetVector},
      {"timeout-ok", no_argument, nullptr, kOptTimeoutOk},
      {"help", no_argument, nullptr, 'h'},
      {nullptr, 0, nullptr, 0},
  };

  int opt = 0;
  int option_idx = 0;
  while ((opt = getopt_long(argc, argv, "i:w:m:dr:h", long_options, &option_idx)) != -1) {
    switch (opt) {
      case 'i':
        args->image = optarg;
        break;
      case 'w':
        args->wave = optarg;
        break;
      case 'm': {
        uint64_t parsed = 0;
        if (!parse_u64(optarg, &parsed)) {
          std::cerr << "Invalid --max-cycles value: " << optarg << "\n";
          return ParseResult::kError;
        }
        args->max_cycles = parsed;
        break;
      }
      case 'd':
        args->diff = true;
        break;
      case 'r':
        args->ref = optarg;
        break;
      case kOptDiffImageOffset: {
        uint64_t parsed = 0;
        if (!parse_u64(optarg, &parsed) || parsed > UINT32_MAX) {
          std::cerr << "Invalid --diff-image-offset value: " << optarg << "\n";
          return ParseResult::kError;
        }
        args->diff_image_offset = static_cast<uint32_t>(parsed);
        break;
      }
      case kOptDiffResetVector: {
        uint64_t parsed = 0;
        if (!parse_u64(optarg, &parsed) || parsed > UINT32_MAX) {
          std::cerr << "Invalid --diff-reset-vector value: " << optarg << "\n";
          return ParseResult::kError;
        }
        args->diff_reset_vector = static_cast<uint32_t>(parsed);
        break;
      }
      case kOptTimeoutOk:
        args->timeout_ok = true;
        break;
      case 'h':
        print_usage(argv[0]);
        return ParseResult::kHelp;
      default:
        print_usage(argv[0]);
        return ParseResult::kError;
    }
  }
  return ParseResult::kOk;
}

void tick(Vecos_sim_top *top, VerilatedContext *contextp, VerilatedVcdC *tfp) {
  top->clock = 0;
  top->eval();
  if (tfp != nullptr) {
    tfp->dump(contextp->time());
  }
  contextp->timeInc(1);

  top->clock = 1;
  top->eval();
  if (tfp != nullptr) {
    tfp->dump(contextp->time());
  }
  contextp->timeInc(1);
}

}  // namespace

int main(int argc, char **argv, char **) {
  Args args;
  const ParseResult parse_result = parse_args(argc, argv, &args);
  if (parse_result == ParseResult::kHelp) {
    return 0;
  }
  if (parse_result == ParseResult::kError) {
    return 1;
  }

  dpi_load_image(args.image);
  if (args.diff && args.diff_image_offset != 0) {
    dpi_preload_pmem_from_image(args.image, args.diff_image_offset);
  }

  const std::unique_ptr<VerilatedContext> contextp{new VerilatedContext};
  contextp->commandArgs(argc, argv);
  contextp->traceEverOn(true);
  Verilated::traceEverOn(true);
  const std::unique_ptr<Vecos_sim_top> top{new Vecos_sim_top{contextp.get(), ""}};
  if (args.diff) {
    difftest_configure(top.get(), args.ref, args.image, args.diff_image_offset, args.diff_reset_vector);
  }

  VerilatedVcdC *tfp = nullptr;
  if (args.wave != nullptr) {
    tfp = new VerilatedVcdC;
    top->trace(tfp, 99);
    tfp->open(args.wave);
    std::cerr << "[soc-sim] waveform: " << args.wave << "\n";
  }

  top->reset = 1;
  for (int i = 0; i < 20; ++i) {
    tick(top.get(), contextp.get(), tfp);
  }
  top->reset = 0;

  uint64_t cycles = 0;
  bool trap_seen = false;
  uint32_t trap_code = 0;
  while (!contextp->gotFinish() && cycles < args.max_cycles) {
    tick(top.get(), contextp.get(), tfp);
    ++cycles;
    if (top->trap_valid) {
      trap_seen = true;
      trap_code = static_cast<uint32_t>(top->trap_code);
      break;
    }
  }

  if (tfp != nullptr) {
    tfp->close();
    delete tfp;
  }
  top->final();

  if (trap_seen) {
    if (trap_code == 0) {
      std::cerr << "[soc-sim] HIT GOOD TRAP after " << cycles << " cycles\n";
      return 0;
    }
    std::cerr << "[soc-sim] HIT BAD TRAP, code=" << trap_code
              << " after " << cycles << " cycles\n";
    return 1;
  }

  if (cycles >= args.max_cycles) {
    difftest_dump_progress();
    std::cerr << "[soc-sim] timeout after " << cycles << " cycles\n";
    return args.timeout_ok ? 0 : 1;
  }
  std::cerr << "[soc-sim] finish after " << cycles << " cycles\n";
  return 0;
}
