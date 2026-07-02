#!/usr/bin/env bash
set -euo pipefail

tracked_waveforms="$(git ls-files \
  '*.vcd' '**/*.vcd' \
  '*.vcd.gz' '**/*.vcd.gz' \
  '*.fst' '**/*.fst' \
  '*.fsdb' '**/*.fsdb' \
  '*.vpd' '**/*.vpd' \
  '*.ghw' '**/*.ghw' \
  '*.wlf' '**/*.wlf' \
  '*.lxt' '**/*.lxt' \
  '*.lxt2' '**/*.lxt2')"

if [[ -n "${tracked_waveforms}" ]]; then
  echo "Tracked waveform files must not be shipped in ecc-fe releases:" >&2
  echo "${tracked_waveforms}" >&2
  exit 1
fi
