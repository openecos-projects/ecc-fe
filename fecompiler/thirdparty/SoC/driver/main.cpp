#include "VysyxSoCTop.h"
#include "driver/dpi_mem.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

#include <getopt.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>

namespace {

struct Args {
  const char *image = nullptr;
  const char *wave = nullptr;
  uint64_t max_cycles = 2000000;
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
            << "  --help                Show this help message\n";
}

ParseResult parse_args(int argc, char **argv, Args *args) {
  static struct option long_options[] = {
      {"image", required_argument, nullptr, 'i'},
      {"wave", required_argument, nullptr, 'w'},
      {"max-cycles", required_argument, nullptr, 'm'},
      {"help", no_argument, nullptr, 'h'},
      {nullptr, 0, nullptr, 0},
  };

  int opt = 0;
  int option_idx = 0;
  while ((opt = getopt_long(argc, argv, "i:w:m:h", long_options, &option_idx)) != -1) {
    switch (opt) {
      case 'i':
        args->image = optarg;
        break;
      case 'w':
        args->wave = optarg;
        break;
      case 'm': {
        char *end = nullptr;
        const unsigned long long parsed = std::strtoull(optarg, &end, 10);
        if (end == optarg || *end != '\0') {
          std::cerr << "Invalid --max-cycles value: " << optarg << "\n";
          return ParseResult::kError;
        }
        args->max_cycles = static_cast<uint64_t>(parsed);
        break;
      }
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

void tick(VysyxSoCTop *top, VerilatedContext *contextp, VerilatedVcdC *tfp) {
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

  Verilated::commandArgs(argc, argv);
  Verilated::traceEverOn(true);
  const std::unique_ptr<VerilatedContext> contextp{new VerilatedContext};
  const std::unique_ptr<VysyxSoCTop> top{new VysyxSoCTop{contextp.get(), ""}};

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
  while (!contextp->gotFinish() && cycles < args.max_cycles) {
    tick(top.get(), contextp.get(), tfp);
    ++cycles;
  }

  if (tfp != nullptr) {
    tfp->close();
    delete tfp;
  }
  top->final();

  if (cycles >= args.max_cycles) {
    std::cerr << "[soc-sim] timeout after " << cycles << " cycles\n";
    return 1;
  }
  return 0;
}
