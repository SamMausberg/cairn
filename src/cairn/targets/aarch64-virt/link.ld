/* QEMU `virt` on AArch64. DRAM is 0x4000_0000 and 128 MiB by default (-m changes the size,
   never the base). The image is placed 512 KiB in, the same offset a Linux kernel uses, which
   leaves QEMU's boot stub and the device tree it writes just past the image undisturbed. The
   stack top is fixed 16 MiB into DRAM and grows down, so it cannot reach either of them. */
ENTRY(_start)
__stack_top = 0x41000000;
SECTIONS {
  . = 0x40080000;
  .text : { KEEP(*(.text.boot)) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  . = ALIGN(16);
  __bss_start = .;
  .bss : ALIGN(16) { *(.bss*) *(COMMON) }
  . = ALIGN(16);
  __bss_end = .;
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
