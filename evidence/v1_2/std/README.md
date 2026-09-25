# Reading input and counting words with std

How long reading all of standard input into a `Vec[u8]` takes, and how much memory it holds at its peak, before and after `std.vec` grew through views and `std.io.read_to_end` sized a regular file first. A newcomer probe found that 77 MB read with the skill's loop took about a second and peaked at 2.6 times the input, with 58% of a run in `std.vec.reserve[u8]`. Below that, a word count keyed by `Vec[u8]` against the one the probe wrote.

Everything ran on 2026-09-25 on an AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18), with clang++ 21.1.8 and Python 3.12.3. Four other agents ran builds and test suites on the machine throughout; the load average was between 8 and 49. Host timing only; no device code ran.

## What ran

```sh
git archive 0af59876 src bin | tar -x -C BEFORE
python3 bench/host/std_input.py --before BEFORE --runs 7 --megabytes 77 --out evidence/v1_2/std/growth.json
```

`bench/host/std_input.py` builds three programs as `cairn build --kind exe` builds them, once with main at 0af5987 and once with this branch, and runs each over the same 80,740,352 bytes of text, from the file itself (`< input`) and through a pipe from `cat`, seven times, the two builds alternating. It records the wall time and GNU time's peak resident set of each run, and keeps nothing unless every run printed the input's length. `chunk_loop` is the loop the skill's example and every probe task wrote (a 4096-byte `read_stdin`, then `extend_from`); `read_to_end` calls `io.read_to_end` on descriptor 0; `push_each` reads the input with `read_to_end` and then pushes it into a second `Vec` one byte at a time, so its growth is `reserve` alone.

## Results

Median seconds of seven runs, with the fastest and slowest, and the largest peak resident set of the seven:

| Program | Input | Before, s | After, s | Before, MiB | After, MiB |
|---|---|---|---|---|---|
| `chunk_loop` | file | 1.15 (1.04 to 1.21) | 0.28 (0.24 to 0.29) | 195 | 195 |
| `chunk_loop` | pipe | 1.10 (1.01 to 1.23) | 0.30 (0.29 to 0.32) | 195 | 195 |
| `read_to_end` | file | 1.08 (1.01 to 1.16) | 0.08 (0.07 to 0.10) | 195 | 80 |
| `read_to_end` | pipe | 1.18 (1.02 to 1.34) | 0.32 (0.29 to 0.33) | 195 | 195 |
| `push_each` | file | 1.66 (1.46 to 1.91) | 0.48 (0.45 to 0.58) | 323 | 272 |
| `push_each` | pipe | 1.71 (1.41 to 1.81) | 0.66 (0.63 to 0.82) | 323 | 323 |

The ranges of the two arms do not overlap in any row. Growth was a loop of `swap(bigger[i], v.data[i])`, and each index of it was guarded against a `Buf` field that every store through a `u8` could have changed, so the compiled loop reloaded the field and checked both bounds for every byte. Now the elements move through two views of one extent, which need no guard, and the loop compiles to 16-byte loads and stores. A regular file is asked for what is left of it, so `read_to_end` makes one allocation of the file's size and the peak is the input itself.

A pipe still peaked at about 2.5 times its input: the old array and the doubled, zero-filled new one are both held while the elements move. "The zero fill" below is what changed that.

## A copy instead of the exchange

Growth could copy copyable elements instead of exchanging them, at the price of a second growth path beside `reserve`. `copy_variant.json` holds that variant (`extend_from` allocating the bigger `Buf` and filling it with `mem.copy`) as its before arm against this branch, run the same way at load 16 to 54: `chunk_loop` took 0.27 s (0.22 to 0.30) against 0.30 s (0.27 to 0.35) from a file and 0.34 s against 0.35 s through a pipe, with the same peaks. The ranges overlap, so the branch keeps one growth path.

## Words as map keys

The word-frequency probe keyed its map by a record wrapping a `Vec[u8]`, with its own `Hash` and `Eq`, and refilled a scratch key for every lookup. `words/word_freq_probe.cairn` is that program as the probe wrote it; `words/word_freq_view.cairn` is the same program keyed by `Vec[u8]` itself, filled by `map.entry_view` from a view of the input and read with `io.read_stdin_to_end`. Both were built by `cairn build --kind exe` from the branch that adds `entry_view`, and run seven times each, alternating, under GNU time over 29,362,089 bytes of lines of 1000 words drawn with Zipf weights from 50,000 random lowercase words (Python's `random.Random(3)`), at load 9 to 18. Both printed the same ten lines.

| Program | Seconds, median (fastest to slowest) | Peak, MiB |
|---|---|---|
| `word_freq_probe.cairn` | 0.22 (0.19 to 0.24) | 51 |
| `word_freq_view.cairn` | 0.15 (0.14 to 0.18) | 37 |

The difference is the whole program an agent writes, not the map alone: the probe's version also reads its input 4096 bytes at a time, and copies each word into its scratch key before every lookup.

## The zero fill

A pipe peaked at 2.5 times its input because the doubled array was zero-filled by `new T[n]()`, which writes every byte and so makes every page resident, while the old array was still held. CAIRN cannot say otherwise: every `Buf` is zeroed by the language, so the choice is the runtime's. `runtime/cairn_owners.hpp` now takes a `Buf` of a type that is trivially constructible and destructible, and aligned no more than `max_align_t`, from `calloc`, whose large blocks come from pages the kernel has already zeroed, and releases it with `free`; every other `Buf` is made as before.

```sh
git archive 9ad4365 src bin | tar -x -C BEFORE
python3 bench/host/std_input.py --before BEFORE --runs 7 --megabytes 77 --out evidence/v1_2/std/zeroed.json
```

`zeroed.json`, run the same way at load 3 to 32, the growth change above as the before arm:

| Program | Input | Before, s | After, s | Before, MiB | After, MiB |
|---|---|---|---|---|---|
| `chunk_loop` | file | 0.24 (0.24 to 0.26) | 0.22 (0.21 to 0.26) | 195 | 131 |
| `chunk_loop` | pipe | 0.27 (0.25 to 0.28) | 0.29 (0.27 to 0.32) | 195 | 131 |
| `read_to_end` | file | 0.09 (0.08 to 0.10) | 0.07 (0.07 to 0.07) | 80 | 80 |
| `read_to_end` | pipe | 0.28 (0.27 to 0.29) | 0.25 (0.22 to 0.27) | 195 | 131 |
| `push_each` | file | 0.44 (0.42 to 0.46) | 0.41 (0.38 to 0.45) | 272 | 208 |
| `push_each` | pipe | 0.60 (0.60 to 0.64) | 0.59 (0.50 to 0.63) | 323 | 208 |

The peaks fall by a third, and a pipe now peaks at 1.7 times its input: the new array's pages become resident only as the elements are moved into them and the reads fill the rest. The times move by less than their ranges in every row but one, the file read, so this record claims memory, not speed. g++ builds were not timed.
