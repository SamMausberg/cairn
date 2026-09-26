# Reading standard input into a Vec

How long reading all of standard input into a `Vec[u8]` takes, and how much memory it holds at its peak, before and after `std.vec` grew through views and `std.io.read_to_end` sized the rest of a file after its first read. A newcomer probe found that 77 MB read with the skill's loop took about a second and peaked at 2.6 times the input, with 58% of a run in `std.vec.reserve[u8]`.

Everything ran on 2026-09-25 on an AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18), with clang++ 21.1.8 and Python 3.12.3. Four other agents ran builds and test suites on the machine throughout; each record holds the load average at its start and end. Host timing only; no device code ran.

## What ran

```sh
git archive 0af59876 src bin | tar -x -C BEFORE
python3 bench/host/std_input.py --before BEFORE --runs 7 --megabytes 77 \
  --after-label "c12/std-ergonomics-vec-growth at 15f1d81" \
  --before-label "main at 0af5987, from git archive of src and bin" \
  --note "four other agents ran builds and tests on the machine throughout" --out evidence/v1_2/std/growth.json
```

`bench/host/std_input.py` builds three programs as `cairn build --kind exe` builds them, once with main at 0af5987 and once with this branch at 15f1d81, and runs each over the same 80,740,352 bytes of text, from the file itself (`< input`) and through a pipe from `cat`, seven times, the two builds alternating. It records the wall time and GNU time's peak resident set of each run, and keeps nothing unless every run printed the input's length. `chunk_loop` is the loop the skill's example and every probe task wrote (a 4096-byte `read_stdin`, then `extend_from`); `read_to_end` calls `io.read_to_end` on descriptor 0; `push_each` reads the input with `read_to_end` and then pushes it into a second `Vec` one byte at a time, so its growth is `reserve` alone.

## Results

`growth.json`, at a one-minute load average of 24 when it started and 9 when it ended. Median seconds of seven runs, with the fastest and slowest, and the largest peak resident set of the seven:

| Program | Input | Before, s | After, s | Before, MiB | After, MiB |
|---|---|---|---|---|---|
| `chunk_loop` | file | 1.18 (1.07 to 1.23) | 0.25 (0.23 to 0.26) | 195 | 195 |
| `chunk_loop` | pipe | 1.13 (1.02 to 1.28) | 0.26 (0.25 to 0.29) | 195 | 195 |
| `read_to_end` | file | 1.17 (1.02 to 1.29) | 0.09 (0.08 to 0.10) | 195 | 80 |
| `read_to_end` | pipe | 1.11 (0.99 to 1.29) | 0.29 (0.25 to 0.32) | 195 | 195 |
| `push_each` | file | 1.60 (1.46 to 1.73) | 0.44 (0.42 to 0.47) | 323 | 272 |
| `push_each` | pipe | 1.52 (1.48 to 1.77) | 0.61 (0.59 to 0.72) | 323 | 323 |

The ranges of the two arms do not overlap in any row. Growth was a loop of `swap(bigger[i], v.data[i])`, and each index of it was guarded against a `Buf` field that every store through a `u8` could have changed, so the compiled loop reloaded the field and checked both bounds for every byte. Now the elements move through two views of one extent, which need no guard, and the loop compiles to 16-byte loads and stores; `tests/language/test_std_growth.py` holds the emitted C++ to that. After its first read, `read_to_end` asks the file what is left of it, so a regular file takes a 4096-byte allocation and then one of its size, and the peak is the input itself.

A pipe still peaks at about 2.5 times its input: the old array and the doubled, zero-filled new one are both held while the elements move.

## A copy instead of the exchange

Growth could copy copyable elements instead of exchanging them, at the price of a second growth path beside `reserve`. `copy_variant.patch` is that variant, `extend_from` allocating the bigger `Buf` itself and filling it with `mem.copy`, and `copy_variant.json` holds it as the before arm against this branch, run the same way at a one-minute load average of 8 falling to 5:

```sh
git archive 15f1d81 src bin | tar -x -C VARIANT
patch -d VARIANT -p1 < evidence/v1_2/std/copy_variant.patch
python3 bench/host/std_input.py --before VARIANT --runs 7 --megabytes 77 \
  --after-label "c12/std-ergonomics-vec-growth at 15f1d81" \
  --before-label "15f1d81 with copy_variant.patch: extend_from grows by a copy" \
  --note "four other agents ran builds and tests on the machine throughout" --out evidence/v1_2/std/copy_variant.json
```

`chunk_loop`, the only program whose growth goes through `extend_from`, took 0.21 s (0.20 to 0.23) with the copy against 0.24 s (0.22 to 0.24) from a file, and 0.24 s (0.22 to 0.25) against 0.26 s (0.23 to 0.26) through a pipe, with the same peaks; the other rows are level. The ranges overlap, so this record does not show the copy faster, though its medians are about a tenth lower. The branch keeps one growth path; a quieter machine would have to settle whether a second one earns its place.

## What was not done

The zero fill is the rest of the pipe's cost and most of its peak. An unrecorded trial, one build with the runtime edited by hand to take a `Buf` of a trivially constructible type from `calloc`, whose large blocks come from pages the kernel already zeroed, ran `chunk_loop` from a file in 0.20 to 0.24 s at a peak of 131 MiB instead of 195 MiB. That is a change to `runtime/cairn_owners.hpp` for every program, not to `std`, and is its own pull request with its own record. g++ builds were not timed.
