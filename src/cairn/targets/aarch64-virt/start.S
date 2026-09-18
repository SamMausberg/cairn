// Start-up and exit for QEMU `virt` on AArch64. QEMU enters at the ELF entry point, at EL1,
// with the MMU and caches off, so this file owns everything a hosted C library would do:
// a stack, a zeroed .bss, the call to cf_main, and the one way out.
.section .text.boot, "ax"
.globl _start
_start:
  ldr x0, =__stack_top            // 16 MiB into DRAM; 16-byte aligned, as the AAPCS64 requires
  mov sp, x0
  ldr x0, =__bss_start            // both ends are ALIGN(16) in link.ld, so a doubleword loop fits
  ldr x1, =__bss_end
0:cmp x0, x1
  b.hs 1f
  str xzr, [x0], #8
  b 0b
1:mov x0, #(3 << 20)              // CPACR_EL1.FPEN: FP and SIMD are usable, so f32/f64 programs run
  msr cpacr_el1, x0
  isb
  bl cf_main                      // fn main() -> i32, returned in w0
  b cr_exit

// The exit path. ARM semihosting SYS_EXIT (0x18) takes, on AArch64, a two-word block holding a
// reason and a status; on ADP_Stopped_ApplicationExit (0x20026) QEMU exits with that status.
// This is the only channel that carries a value out of the machine, so `cairn run` and the tests
// read fn main()'s return value directly, and cr::trap() reports 134 through the same door.
// QEMU must be started with -semihosting. PSCI SYSTEM_OFF would stop the machine too, but it
// always exits 0, so a trap could not be told from success.
.globl cr_exit
cr_exit:
  sxtw x2, w0
  mov x1, #0x26
  movk x1, #0x2, lsl #16
  sub sp, sp, #16
  stp x1, x2, [sp]
  mov x1, sp
  mov w0, #0x18
  hlt #0xf000
2:wfi                             // unreachable unless semihosting is off: park the core instead
  b 2b

// The compiler lowers aggregate copies and zeroing to these two names whatever -ffreestanding
// says, and there is no C library to supply them. Byte at a time: with the MMU off every access
// is Device memory, where an unaligned or overlapping wide access is not architecturally allowed.
.globl memset
memset:
  mov x3, x0
3:cbz x2, 4f
  strb w1, [x3], #1
  sub x2, x2, #1
  b 3b
4:ret

.globl memcpy
memcpy:
  mov x3, x0
5:cbz x2, 6f
  ldrb w4, [x1], #1
  strb w4, [x3], #1
  sub x2, x2, #1
  b 5b
6:ret
