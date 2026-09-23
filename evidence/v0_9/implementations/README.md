# Implementations and contract-driven validation

What ran on 2026-09-23 for alternative implementations (`compiler/implementations.py`), the implementation session (`agent/implementations.py`) and `cairn validate` (`verify/validation.py`), on `examples/implementations`. The machine is one 16-thread x86-64 host under WSL2, shared with five other agents while it ran, with clang 21.1.8 and g++ 13.3.0. Nothing here measures speed, and nothing ran on a GPU.

| File | What it is | How it was made |
|---|---|---|
| `session_first.json` | the scripted session with no kept case yet | `python3 examples/implementations/loop.py`, with `regressions/prefix.json` holding no case |
| `session_again.json` | the same session once the case is kept | `python3 examples/implementations/loop.py` |
| `prefix_by4.json` | `prefix_by4` against `prefix` | `cairn validate examples/implementations --symbol prefix_by4 --format json` |
| `prefix_lanes.json` | `prefix_lanes` against `prefix`, across its `n >= 65536` condition | the same with `--policy lanes_policy.json` (extents up to 140000, 64 cases) |

## The session

The host pinned the reference `prefix`, a zero tolerance, a test policy of 128 cases with seed 0, and extents up to 4096, each by digest. The scripted agent sent four fixed replies; the host's answers were computed on the run.

1. `candidates/prefix_blocks_wrong.cairn`, blocks of eight that each restart the sum at zero: refused as `E-VALIDATION`. The 17th generated case (`n = 16`, two tiles of the condition's 8, all ones) told it from the reference, and 32 shrinking runs reduced it to `n = 16` with a single 1 at `xs[7]`: the reference leaves 1 in `out[7..16]` and the implementation leaves 0 in `out[8..16]`. The case went into `regressions/prefix.json`.
2. The same reply with `"tolerance": {"absolute": 64.0}`: refused as `E-TOLERANCE` before anything compiled.
3. The same reply with `"domain": {"largest_extent": 8}`: refused as `E-DOMAIN`.
4. `candidates/prefix_blocks.cairn`, which carries the sum from block to block: validated on 129 cases, the kept one first, 41 of which met `n % 8 == 0` and ran the implementation itself; the other 88 ran the dispatch only.

In `session_again.json` the kept case is the first one run, and it refuses the wrong candidate on its own (1 case, 8 shrinking runs that found nothing smaller). Regenerating the regressions file from empty gave the committed file byte for byte. All four submissions reached the host's history callback, three refused and one validated, each with the digests of the pinned tolerance, tests and domain.

## The two implementations in the project

| Implementation | Cases | Ran it | Finite | Z3, apart |
|---|---|---|---|---|
| `prefix_by4`, `when n % 4 == 0` | 129 | 46 | passed | `smt-equivalent` where `n % 4 == 0` and `n <= 16`, the unrolling bound; nothing is decided above it |
| `prefix_lanes`, `when n >= 65536` | 65 | 10 | passed | `unknown`: `scan` is outside the modeled fragment |

The tiles `prefix_lanes` was tested at came from its condition (65535, 65536, 65537, 131071, 131072) and from the lane pool's cutoff of 16384, where a pooled region stops being the plain loop.

## What this establishes, and what it does not

Each pass is finite testing on the cases listed in the record. The reference is an independent algorithm, but the reference and every implementation are compiled by the same compiler and run on the same runtime, so a fault shared by both would agree with itself. Z3's answers hold only in the modeled fragment and only up to the bound each record names. The blocked implementations' Z3 queries ran past their 14-second limit and are `unknown`.

The device side (`verify/device_validation.py`) generated device tests for a `@device` implementation and compiled them for sm_120 in the default suite; running them under `memcheck`, `racecheck`, `initcheck` and `synccheck` is `make gpu` only, and has not run. The session keeps each submission in the candidate history (`agent/history.py`) when the host names a directory; `loop.py` passes a callback instead, and the transcript's `history` is what it received.
