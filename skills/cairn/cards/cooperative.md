# The cooperative card

Sent to an agent when the program uses `barrier`, `shuffle`, `shuffle_down`, `shuffle_xor`, `warp`. Codes: `E-COOP-BARRIER`, `E-COOP-CONFLICT`, `E-COOP-GLOBAL`, `E-COOP-REUSE`, `E-COOP-SHAPE`, `E-COOP-SHARED`, `E-COOP-UNDECIDED`, `E-COOP-UNORDERED`, `E-COOP-WARP`.

```text
blocks b in g threads t in 256 { ... } runs g blocks of 256 threads, b and t usize; up to three names a side, fastest first (blocks bx, by in gx, gy threads tx, ty in 32, 8: thread tx + 32 * ty). Thread extents are literals or constants, whole warps, 32 to 1024 in all (E-COOP-SHAPE). shared tile:f32[1056] = zeroed; declares directly in the body one array per block, zeroed where each block starts, 48 KiB in all (E-COOP-SHARED). barrier; waits for every thread of the block: never under a condition on a thread name, nor in a loop with break or continue (E-COOP-BARRIER). Runs on the device when a view it indexes is @device, else on host threads; its row gains par:device or par:host and zero_init.

Between two barriers no two threads touch one element of a shared array where either writes: two writes E-COOP-CONFLICT, a read of what another thread wrote earlier in the phase E-COOP-UNORDERED (a barrier between them fixes it), a write over what another may still be reading E-COOP-REUSE, an index read from data or differing by a run-time value beside a write E-COOP-UNDECIDED. An array from outside: each element written by one thread of one block, at an index like b * 256 + t or (bx * 32 + r) * h + by * 32 + tx, and read only by the thread that writes it (E-COOP-GLOBAL); guard a single writer with if t == 0.

shuffle(v, lane), shuffle_xor(v, mask), shuffle_down(v, delta) and let s = reduce + warp yield v; (reduce's operators, a fixed butterfly order) need whole warps: only under conditions warps agree on, t < 32, t / 32 == w, ty < 4 when tx counts 32 (E-COOP-WARP). A thread obeys the lane rules: no outer scalar writes, I/O or nested regions.
```
