# embedded

A sensor log arrives over a wire as comma-terminated decimal fields, some of them malformed. This program reports every bad field with the offset of the byte at fault, summarises the good ones with checked arithmetic, sorts them through a fixed histogram and prints all of it over a PL011 UART, on a machine with no operating system, no C library and no allocator.

```sh
cairn run   examples/embedded     # builds the image and runs it under QEMU
cairn build examples/embedded     # the ELF alone, plus the receipt beside it
```

```text
cairn freestanding: qemu virt, pl011 uart
log 23,19,31,7,42,19,x9,,99999999999999999999,
  bad digit at 17
  empty field at 20
  too large at 40
readings 6 total 141 max 42 mean 23
sorted 7 19 19 23 31 42
ok
```

The image exits with `fn main()`'s return value, 0 here, and `size` reports 5411 bytes of text with no data and no bss.

## What each file shows

`src/uart.cairn` is the driver, and `mmio_read[u32]` and `mmio_write[u32]` inside `unsafe { }` are the whole of it: poll the flag register until the transmit FIFO has room, then write the data register. `putu` formats a `u64` as decimal into twenty bytes of `stack` storage, the widest a `u64` can be, with every write bounds checked and nothing allocated.

`src/parse.cairn` answers with `enum Reading { Value(u64); Invalid(usize); Overflow(usize); Empty; }`. A field that is not a number is a value, not an errno or a sentinel, and it carries the offset of the first byte at fault. Overflow is detected before it happens, so the parser never wraps.

`src/main.cairn` walks the input once in `ingest` and hands each field to the parser as `text[start..i]`, a part of the one borrowed view: no copy, no allocation, one dynamic guard that the part is inside the string. The `match` has an arm per variant and no wildcard, so adding a variant would break the build rather than silently fall through. The totals use checked `+`, so a log that overflows a `u64` stops the machine instead of reporting a smaller number. `sort_small` counting-sorts through a `stack counts:usize[64]` histogram; a reading outside `0..63` trips the range-checked `u8` conversion or the bounds check rather than corrupting a neighbouring bucket.

There is no `buffer`, no `Buf`, no `parallel` and no `extern` anywhere, and there could not be: the freestanding build reads the effect row of every function and refuses the program by name if one of them needs something a hosted runtime would have to provide. See [docs/guide/freestanding.md](../../docs/guide/freestanding.md).

## The guard violation

`trap/` is the same machine and the same driver, reading one element past a four-element array with an index that comes from storage rather than from a literal. There is no MMU here, so the read would succeed and return whatever follows the array. The language's own bounds check is the only thing that stops it.

```sh
cairn run examples/embedded/trap
```

```text
trap demo: reading window[4] of 4
```

`unreachable` is never printed and QEMU exits 134, the status a hosted shell reports for `std::abort`. A guard on bare metal is as loud as a guard on a host.
