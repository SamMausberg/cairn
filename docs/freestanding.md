# Projects and the freestanding target


A freestanding build produces one ELF image that runs on bare hardware: no operating system, no C library, no C++ runtime, no dynamic loader, no start files and no unwinder. `[build] target` in `cairn.toml` selects it, `--target` on `cairn build` and `cairn run` overrides it, and the default is `"hosted"`, which is byte for byte the build this compiler has always produced.

```toml
[project]
name = "embedded"
sources = ["src/uart.cairn", "src/parse.cairn", "src/main.cairn"]

[build]
kind = "exe"
target = "aarch64-virt"
```

```sh
cairn build examples/embedded   # the ELF image plus a receipt, in a fresh directory
cairn run   examples/embedded   # the same image, under the target's emulator
```

`cairn run` reports the UART transcript as `stdout` and the emulator's exit status as `exit_code`. Here is the deliberate guard violation in `examples/embedded/trap/`, which reads one element past a four-element array:

```json
{"status": "program-exited", "exit_code": 134,
 "stdout": "trap demo: reading window[4] of 4\n",
 "emulator": ["/usr/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72",
              "-nographic", "-semihosting", "-kernel", ".../trapdemo.elf"]}
```

## What the profile guarantees

The whole language, minus what a host provides. Checked `+ - * / %`, bounds, extent, null, alignment, overlap and tag guards, `stack` storage, records, sums, `match`, generics, traits, closures, `compact`, `reduce` and `derive wire` all behave exactly as they do hosted. `f32` and `f64` work, because the start-up code enables FP and SIMD at EL1.

A refusal instead of a link error. The build reads each function's effect row out of the frontend receipt and rejects the program by name if any row holds an effect a hosted runtime would have to supply: `alloc`, `free`, `io`, `gpu_*`, `transfer:*`, `par:*` or `ffi:*`. That is every `buffer`, `Buf`, `std.vec`, `parallel`, device region and `extern` call, caught before a compiler runs. `mmio_read`, `mmio_write` and `asm` remain available inside `unsafe`.

```json
{"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT",
 "message": "A freestanding target has no hosted runtime: main has effect 'alloc'."}
```

No undefined symbols. The only symbols the C++ needs from outside the translation unit, `memset`, `memcpy` and the exit path, are defined in the target's `start.S`, and `cr::trap()` no longer calls `std::abort`.

```text
$ nm -u examples/embedded/build/embedded-*/embedded.elf     # nothing is undefined
$ size examples/embedded/build/embedded-*/embedded.elf
   text	   data	    bss	    dec	    hex	filename
   5411	      0	      0	   5411	   1523	embedded.elf
```

A trap is distinguishable from success. `fn main() -> i32` returns its value as the emulator's exit status, and a failed guard leaves through the same door with status 134, the status a hosted shell reports for `std::abort`.

One command, one fresh directory. The image, the generated C++, every runtime header and the receipt land together; nothing is reused between builds and nothing is downloaded.

## What it does not guarantee

No allocator, so no growth. `buffer`, `Buf` and `std.vec` are rejected, not emulated. Storage is `stack` arrays, statics and `ro<u8>[n]` string views.

No concurrency and no device: `parallel` and every `@device` placement are rejected.

No MMU, no caches, no interrupts, no timer. The image runs in the state QEMU hands it, flat physical memory at EL1 with the MMU off, so every access is Device-nGnRnE memory. The build therefore passes `-mstrict-align`, since an unaligned access to Device memory is not architecturally allowed, and performance numbers from this profile are not comparable to hosted ones. Nothing here installs a vector table, so a hardware exception hangs the machine rather than reporting; the language's own guards are what stop a bad index or an overflow.

A trap does not unwind, log or roll back. It stops the machine. `defer` and owner release do not run, exactly as a hosted abort does not.

Cross compilation is still not offered. A target names a host family and is refused on any other host, rather than guessing at a sysroot.

`cairn test` still runs on the host. A task contract is a finite scalar comparison of one symbol, so it builds a hosted shared library whatever `target` says. What runs on the machine is `cairn run` and `tests/projects/test_freestanding.py`.

The backend is not verified. `formal_status` stays `not-verified` here as everywhere else.

## The `aarch64-virt` target

QEMU's `virt` machine with a Cortex-A72, the board CAIRN's evidence is captured on.

| Address | What |
| --- | --- |
| `0x0900_0000` | PL011 UART0 data register; `0x0900_0018` is the flag register, bit 5 = TX FIFO full |
| `0x4000_0000` | DRAM base, 128 MiB by default |
| `0x4008_0000` | image load address: `.text`, `.rodata`, `.data`, `.bss` |
| `0x4100_0000` | stack top, growing down; 16 MiB clear of the image and of the device tree QEMU writes just past it |

`src/cairn/targets/aarch64-virt/start.S` sets `sp`, zeroes `.bss`, enables FP and SIMD through `CPACR_EL1`, calls `cf_main` and branches to `cr_exit`. `cr_exit` issues ARM semihosting `SYS_EXIT` (`0x18`) with `ADP_Stopped_ApplicationExit` and the status, which QEMU turns into its own exit status; that is why the emulator is started with `-semihosting`. PSCI `SYSTEM_OFF` would also stop the machine, but it always exits 0, so a trap could not be told from a success, which is the experiment that settled the choice. `start.S` also defines `memset` and `memcpy`, byte at a time, because the compiler lowers aggregate copies to those names whatever `-ffreestanding` says.

The build is one command line, from the receipt of `cairn build examples/embedded`:

```text
clang++ -std=c++20 -O3 -ffp-contract=off -fno-fast-math -fno-exceptions -fno-rtti
  -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-variable -Wno-unused-but-set-variable
  -DCAIRN_FREESTANDING=1 -ffreestanding -nostdlib -static -fno-stack-protector
  -fno-threadsafe-statics -fno-PIC -fno-PIE -fno-unwind-tables -fno-asynchronous-unwind-tables
  -Wl,--build-id=none -Wno-unused-command-line-argument -mstrict-align -march=armv8-a
  -Wl,-T,<target>/link.ld <build>/program.cpp <target>/start.S -o <build>/embedded.elf
```

`-Wno-unused-command-line-argument` is there because the C++ options are unused on the `start.S` job and `-Werror` would otherwise reject them.

## Running it under QEMU

```sh
qemu-system-aarch64 -M virt -cpu cortex-a72 -nographic -semihosting -kernel <image>.elf
echo $?      # fn main()'s return value, or 134 for a failed guard
```

`cairn run --target aarch64-virt <project>` runs exactly that command. QEMU loads the ELF by its program headers and enters at `_start`, so no raw binary and no bootloader is needed. `examples/embedded/` is the worked application and `examples/embedded/trap/` the guard violation; `evidence/v1_0/embedded/` holds a captured transcript, `size`, `nm` and tool versions.

## Adding a target

1. Create `src/cairn/targets/<name>/` with `link.ld` and `start.S`. The start-up code owns the stack, `.bss`, the call to `extern "C" cf_main()`, `memset`, `memcpy`, and `extern "C" [[noreturn]] void cr_exit(int)`, the one exit path, which must carry a status out so a trap (134) is distinguishable from any value `fn main() -> i32` can return.
2. Add one row to `TARGETS` in `src/cairn/projects/toolchain.py` naming the host `family`, the `-march` `arch`, any extra `flags`, and the `run` command that executes an image, ending in the option that takes the image path.
3. Add the directory's `*.S` and `*.ld` to `package-data` in `pyproject.toml` if the glob does not already cover it, and extend `tests/projects/test_freestanding.py`.

Nothing else in the compiler knows about targets: the profile is a flag set, a linker script, a start-up file and an effect refusal, not a second code generator.
