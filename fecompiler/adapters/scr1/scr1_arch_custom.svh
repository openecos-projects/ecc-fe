// ECOS SCR1 adapter configuration.
//
// The upstream SCR1 default configuration enables several SoC/debug features
// that are not needed for the ECOS smoke path.  This custom config keeps the
// core RV32IM-capable and aligns reset/trap addresses with the ECOS simulator
// memory map.

parameter bit [`SCR1_XLEN-1:0]          SCR1_ARCH_RST_VECTOR    = 32'h2000_0000;
parameter bit [`SCR1_XLEN-1:0]          SCR1_ARCH_MTVEC_BASE    = 32'h2000_0100;

parameter bit [`SCR1_DMEM_AWIDTH-1:0]   SCR1_TCM_ADDR_MASK      = 32'h0000_0000;
parameter bit [`SCR1_DMEM_AWIDTH-1:0]   SCR1_TCM_ADDR_PATTERN   = 32'hffff_ffff;

parameter bit [`SCR1_DMEM_AWIDTH-1:0]   SCR1_TIMER_ADDR_MASK    = 32'hffff_ffe0;
parameter bit [`SCR1_DMEM_AWIDTH-1:0]   SCR1_TIMER_ADDR_PATTERN = 32'h0049_0000;

`define SCR1_ARCH_BUILD_ID `SCR1_MIMPID
