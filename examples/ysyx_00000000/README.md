# ysyx_00000000 ECC-FE CPU

`ysyx_00000000` is the CPU implementation bundled with ECC-FE for the YSYX AM
SoC flow.

## CPU Overview

- ISA: RV32I + Zicsr, without RV32M.
- Native top module: `ysyx_00000000`.
- Reset vector: `0x2000_0000`.
- Interface: the ECC-FE YSYX AXI master/slave CPU socket.
- Simulation: a single-retirement difftest adapter is included in the filelist.

## ECC-FE Settings

Use these values in the frontend project wizard:

| Field | Value |
| --- | --- |
| CPU core | `My CPU Top` |
| CPU Top Module | `ysyx_00000000` |
| CPU filelist | `filelist.cpu.f` |
| SoC harness | `YSYX AM SoC Harness` |
| Toolchain | `RISC-V 32-bit ELF` |
| Test suite | `Smoke Tests` |

Compile test programs with `-march=rv32i_zicsr -mabi=ilp32`. The filelist
declares `ECOS_DIFFTEST`, so ECC-FE enables the DPI adapter and compares test
programs against the packaged RISC-V reference model. Selecting all eight RTL
files in the wizard is also supported: ECC-FE detects the adapter and writes
the same capability define into its generated filelist.
