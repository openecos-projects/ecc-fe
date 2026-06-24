#include "trap.h"

#define COREMARK_ITEMS 64
#define COREMARK_ITERATIONS 128
#define COREMARK_EXPECTED 0x3df51153u

static uint32_t data[COREMARK_ITEMS];

static uint32_t rotl32(uint32_t value, int shift) {
  return shift == 0 ? value : (value << shift) | (value >> (32 - shift));
}

static void init_data(void) {
  uint32_t state = 0x3412abcdu;
  for (int i = 0; i < COREMARK_ITEMS; i++) {
    state = state * 1664525u + 1013904223u;
    data[i] = state ^ (state >> 16);
  }
}

static uint32_t run_coremark_smoke(void) {
  uint32_t crc = 0;
  for (int iteration = 0; iteration < COREMARK_ITERATIONS; iteration++) {
    for (int i = 0; i < COREMARK_ITEMS; i++) {
      uint32_t x = data[(i + iteration) & (COREMARK_ITEMS - 1)];
      uint32_t y = data[(i * 7 + iteration) & (COREMARK_ITEMS - 1)];
      uint32_t z = rotl32(x, i & 7) ^ (y + (uint32_t)iteration) ^ ((uint32_t)i * 0x45d9f3bu);
      data[i] = z * 33u + (crc ^ (uint32_t)i);
      crc = rotl32(crc, 5) ^ (data[i] + 0x9e3779b9u + (uint32_t)iteration);
    }
  }
  return crc;
}

int main(void) {
  init_data();
  uint32_t crc = run_coremark_smoke();
  printf("coremark: iterations=%d items=%d crc=0x%x expected=0x%x\n",
         COREMARK_ITERATIONS, COREMARK_ITEMS, crc, COREMARK_EXPECTED);
  check(crc == COREMARK_EXPECTED);
  return 0;
}
