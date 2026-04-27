#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --src <test.c> --name <name> [--out_dir <dir>]" >&2
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC=""
NAME=""
OUT_DIR="${ROOT}/tests/out"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)
      SRC="$2"
      shift 2
      ;;
    --name)
      NAME="$2"
      shift 2
      ;;
    --out_dir)
      OUT_DIR="$2"
      shift 2
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${NAME}" ]]; then
  usage
  exit 1
fi

if [[ -z "${SRC}" ]]; then
  SRC="${ROOT}/tests/programs/${NAME}.c"
fi

IS_RTTHREAD=0
if [[ "${NAME}" == "rtthread" || "${SRC}" == "rtthread" ]]; then
  IS_RTTHREAD=1
  NAME="rtthread"
fi

if [[ "${IS_RTTHREAD}" != "1" && ! -f "${SRC}" ]]; then
  echo "test source not found: ${SRC}" >&2
  exit 1
fi

if [[ -n "${RISCV_PREFIX:-}" ]]; then
  CROSS_COMPILE="${RISCV_PREFIX}"
elif command -v riscv64-none-elf-gcc >/dev/null 2>&1; then
  CROSS_COMPILE="riscv64-none-elf-"
elif command -v riscv-none-elf-gcc >/dev/null 2>&1; then
  CROSS_COMPILE="riscv-none-elf-"
elif command -v riscv64-unknown-linux-gnu-gcc >/dev/null 2>&1; then
  CROSS_COMPILE="riscv64-unknown-linux-gnu-"
elif command -v riscv64-linux-gnu-gcc >/dev/null 2>&1; then
  CROSS_COMPILE="riscv64-linux-gnu-"
else
  echo "No RISC-V GCC toolchain found in PATH" >&2
  exit 1
fi

AS="${CROSS_COMPILE}gcc"
CC="${CROSS_COMPILE}gcc"
LD="${CROSS_COMPILE}ld"
OBJDUMP="${CROSS_COMPILE}objdump"
OBJCOPY="${CROSS_COMPILE}objcopy"
if ! command -v "${CC}" >/dev/null 2>&1; then
  echo "Configured cross toolchain prefix is invalid: ${CROSS_COMPILE}" >&2
  exit 1
fi

HEXDUMP="${HEXDUMP_BIN:-hexdump}"
if ! command -v "${HEXDUMP}" >/dev/null 2>&1; then
  echo "hexdump tool not found: ${HEXDUMP}" >&2
  exit 1
fi

COMMON_CFLAGS="-fno-pic -march=rv32im_zicsr -mcmodel=medany -mstrict-align -mabi=ilp32"
CFLAGS="-DMAINARGS=\"\" -lm -g -O2 -Wall ${COMMON_CFLAGS} -I${ROOT}/tests/include -I${ROOT}/tests/common/include -I${ROOT}/tests/common -fno-asynchronous-unwind-tables -fno-builtin -fno-stack-protector -Wno-main -U_FORTIFY_SOURCE -fvisibility=hidden -fdata-sections -ffunction-sections"
ASFLAGS="${COMMON_CFLAGS} -I${ROOT}/tests/include -I${ROOT}/tests/common/include -I${ROOT}/tests/common"
if [[ "${IS_RTTHREAD}" == "1" ]]; then
  SOC_USE_BOOTLOADER="${SOC_USE_BOOTLOADER:-1}"
  SOC_FAST_DIFF_BOOT="${SOC_FAST_DIFF_BOOT:-1}"
else
  SOC_USE_BOOTLOADER="${SOC_USE_BOOTLOADER:-0}"
  SOC_FAST_DIFF_BOOT="${SOC_FAST_DIFF_BOOT:-0}"
fi
if [[ "${SOC_USE_BOOTLOADER}" == "1" ]]; then
  PMEM_START=0x80000000
else
  PMEM_START=0x20000000
fi

LDFLAGS="-z noexecstack -melf32lriscv -T ${ROOT}/tests/common/linker.ld --defsym=_pmem_start=${PMEM_START} --defsym=_entry_offset=0x0 --gc-sections -e _start"
BOOT_SRC_BASE=0x20000000
BOOT_PAYLOAD_OFFSET=0x100
BOOT_MROM_SIZE=0x100000
BOOT_DST_BASE=0x90000000
BOOT_EXEC_BASE=0x80000000

mkdir -p "${OUT_DIR}"
PREFIX="${OUT_DIR}/${NAME}"

TMPDIR="${OUT_DIR}/.tmp_${NAME}"
rm -rf "${TMPDIR}"
mkdir -p "${TMPDIR}"
trap 'rm -rf "${TMPDIR}"' EXIT

if [[ "${IS_RTTHREAD}" == "1" ]]; then
  RTTHREAD_AM_ROOT="${RTTHREAD_AM_ROOT:-${ROOT}/../rt-thread-am}"
  RTTHREAD_BSP="${RTTHREAD_AM_ROOT}/bsp/abstract-machine"
  RTTHREAD_ARCH="${RTTHREAD_ARCH:-riscv32-nemu}"
  if [[ ! -d "${RTTHREAD_BSP}" ]]; then
    echo "rt-thread-am BSP not found: ${RTTHREAD_BSP}" >&2
    exit 1
  fi
  if [[ -z "${AM_HOME:-}" || ! -f "${AM_HOME}/Makefile" ]]; then
    if [[ -f "/home/luyoung/ysyx-workbench/abstract-machine/Makefile" ]]; then
      export AM_HOME="/home/luyoung/ysyx-workbench/abstract-machine"
    else
      echo "AM_HOME must point to an AbstractMachine repo" >&2
      exit 1
    fi
  fi
  if ! command -v scons >/dev/null 2>&1; then
    echo "scons is required to build rt-thread-am" >&2
    exit 1
  fi
  export RTT_CC_PREFIX="${CROSS_COMPILE}"
  make -C "${RTTHREAD_BSP}" ARCH="${RTTHREAD_ARCH}" CROSS_COMPILE="${CROSS_COMPILE}" init

  RTTHREAD_WRAPPER_MK="$(cd "${TMPDIR}" && pwd)/rtthread-am.mk"
  {
    echo "include Makefile"
    echo "CFLAGS += -Wno-error -DECC_FE_SOC"
  } > "${RTTHREAD_WRAPPER_MK}"
  make -C "${RTTHREAD_BSP}" -f "${RTTHREAD_WRAPPER_MK}" ARCH="${RTTHREAD_ARCH}" CROSS_COMPILE="${CROSS_COMPILE}" image

  RTTHREAD_IMAGE="${RTTHREAD_BSP}/build/rtthread-${RTTHREAD_ARCH}"
  cp -f "${RTTHREAD_IMAGE}.elf" "${PREFIX}.elf"
  cp -f "${RTTHREAD_IMAGE}.bin" "${PREFIX}.bin"
  "${OBJDUMP}" -d "${PREFIX}.elf" > "${PREFIX}.txt"
  "${OBJCOPY}" -O verilog --change-addresses -"${PMEM_START}" --verilog-data-width 4 "${PREFIX}.elf" "${PREFIX}.hex"
  "${HEXDUMP}" -v -e '/4 "%08x\n"' "${PREFIX}.bin" > "${PREFIX}.mem"
else
  mapfile -t COMMON_SRCS < <(
    find -L "${ROOT}/tests/common" -type f \( -name '*.c' -o -name '*.S' \) ! -path "${ROOT}/tests/common/soc_bootloader.S" | sort
  )

  OBJS=()
  INDEX=0
  for FILE in "${SRC}" "${COMMON_SRCS[@]}"; do
    OBJ="${TMPDIR}/obj_${INDEX}.o"
    INDEX=$((INDEX + 1))
    case "${FILE}" in
      *.c)
        "${CC}" -std=gnu11 ${CFLAGS} -c -o "${OBJ}" "${FILE}"
        ;;
      *.S)
        "${AS}" ${ASFLAGS} -c -o "${OBJ}" "${FILE}"
        ;;
      *)
        echo "Unsupported source: ${FILE}" >&2
        exit 1
        ;;
    esac
    OBJS+=("${OBJ}")
  done

  "${LD}" ${LDFLAGS} -o "${PREFIX}.elf" --start-group "${OBJS[@]}" --end-group
  "${OBJDUMP}" -d "${PREFIX}.elf" > "${PREFIX}.txt"
  "${OBJCOPY}" -S --set-section-flags .bss=alloc,contents -O binary "${PREFIX}.elf" "${PREFIX}.bin"
  "${OBJCOPY}" -O verilog --change-addresses -"${PMEM_START}" --verilog-data-width 4 "${PREFIX}.elf" "${PREFIX}.hex"
  "${HEXDUMP}" -v -e '/4 "%08x\n"' "${PREFIX}.bin" > "${PREFIX}.mem"
fi

PAYLOAD_SIZE="$(wc -c < "${PREFIX}.bin")"
if [[ "${SOC_USE_BOOTLOADER}" != "1" ]]; then
  cp -f "${PREFIX}.bin" "${PREFIX}.soc.bin"
  echo "[build_test] built: ${PREFIX}.soc.bin"
  exit 0
fi

if (( BOOT_PAYLOAD_OFFSET + PAYLOAD_SIZE > BOOT_MROM_SIZE )); then
  echo "payload too large for MROM image layout" >&2
  exit 1
fi

PAYLOAD_SRC_ADDR=$((BOOT_SRC_BASE + BOOT_PAYLOAD_OFFSET))
BOOT_COPY_SIZE="${PAYLOAD_SIZE}"
if [[ "${SOC_FAST_DIFF_BOOT}" == "1" ]]; then
  BOOT_COPY_SIZE=0
fi
BOOT_OBJ="${TMPDIR}/soc_bootloader.o"
BOOT_BIN="${TMPDIR}/soc_bootloader.bin"

"${AS}" ${ASFLAGS} -c -o "${BOOT_OBJ}" "${ROOT}/tests/common/soc_bootloader.S"
"${LD}" -z noexecstack -melf32lriscv -T "${ROOT}/tests/common/soc_bootloader.ld" --gc-sections -e _start \
  --defsym=_payload_src=${PAYLOAD_SRC_ADDR} \
  --defsym=_payload_dst=${BOOT_DST_BASE} \
  --defsym=_payload_exec=${BOOT_EXEC_BASE} \
  --defsym=_payload_size=${BOOT_COPY_SIZE} \
  -o "${TMPDIR}/soc_bootloader.elf" "${BOOT_OBJ}"
"${OBJCOPY}" -S -O binary "${TMPDIR}/soc_bootloader.elf" "${BOOT_BIN}"

BOOT_BIN_SIZE="$(wc -c < "${BOOT_BIN}")"
if (( BOOT_BIN_SIZE > BOOT_PAYLOAD_OFFSET )); then
  echo "bootloader overlaps payload area" >&2
  exit 1
fi

FINAL_IMG_SIZE=$((BOOT_PAYLOAD_OFFSET + PAYLOAD_SIZE))
if (( BOOT_BIN_SIZE > FINAL_IMG_SIZE )); then
  FINAL_IMG_SIZE="${BOOT_BIN_SIZE}"
fi
truncate -s "${FINAL_IMG_SIZE}" "${PREFIX}.soc.bin"
dd if="${BOOT_BIN}" of="${PREFIX}.soc.bin" bs=1 conv=notrunc status=none
dd if="${PREFIX}.bin" of="${PREFIX}.soc.bin" bs=1 seek="$((BOOT_PAYLOAD_OFFSET))" conv=notrunc status=none

echo "[build_test] built with bootloader: ${PREFIX}.soc.bin"
