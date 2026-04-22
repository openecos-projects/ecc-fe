  .section .text
  .globl _start
_start:
  lui t0, %hi(_payload_src)
  addi t0, t0, %lo(_payload_src)
  lui t1, %hi(_payload_dst)
  addi t1, t1, %lo(_payload_dst)
  lui t2, %hi(_payload_size)
  addi t2, t2, %lo(_payload_size)

  # Copy word-by-word first for much faster boot on high-latency buses.
  srli t4, t2, 2
0:
  beqz t4, 1f
  lw t5, 0(t0)
  sw t5, 0(t1)
  addi t0, t0, 4
  addi t1, t1, 4
  addi t4, t4, -1
  j 0b

  # Copy trailing bytes (size % 4).
1:
  andi t2, t2, 3
2:
  beqz t2, 2f
  lbu t3, 0(t0)
  sb t3, 0(t1)
  addi t0, t0, 1
  addi t1, t1, 1
  addi t2, t2, -1
  j 2b

2:
  # CL3 currently does not implement fence/fence.i reliably in this flow.
  nop
  lui t0, %hi(_payload_exec)
  addi t0, t0, %lo(_payload_exec)
  jr t0
