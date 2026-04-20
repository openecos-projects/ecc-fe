#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM_BIN="${ROOT}/build/soc_top"
IMAGE="${ROOT}/tests/out/min2.soc.bin"
MAX_CYCLES=2000000
WAVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sim-bin)
      SIM_BIN="$2"
      shift 2
      ;;
    --image)
      IMAGE="$2"
      shift 2
      ;;
    --max-cycles)
      MAX_CYCLES="$2"
      shift 2
      ;;
    --wave)
      WAVE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--sim-bin <bin>] [--image <soc.bin>] [--max-cycles <n>] [--wave <file>]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -x "${SIM_BIN}" ]]; then
  "${ROOT}/scripts/build_soc_sim.sh" "${SIM_BIN}"
fi

if [[ ! -f "${IMAGE}" ]]; then
  echo "image not found: ${IMAGE}" >&2
  exit 1
fi

CMD=("${SIM_BIN}" --image "${IMAGE}" --max-cycles "${MAX_CYCLES}")
if [[ -n "${WAVE}" ]]; then
  CMD+=(--wave "${WAVE}")
fi

"${CMD[@]}"
