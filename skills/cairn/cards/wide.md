# The wide card

Selected by Cache load_wide store_wide. Codes: E-WIDE.

```text
let v = load_wide[4](x, i); reads x[i..i + 4] as one Array[f32, 4] and store_wide(out, i, v); writes one back, each a single access on the device (LDG.E.128). K is a constant power of two and K elements of a scalar or storage float fill at most 16 bytes: 4 f32, 8 f16, 2 f64, 16 u8 (E-WIDE); store_wide takes an Array of the array's own element type. Both trap unless i + K <= len(x) and x[i] sits on K * sizeof(T) bytes, which a view the caller passes need not. A third or fourth argument hints the device's caches by name: Cache.all (default), Cache.l2 (.cg), Cache.streaming (.cs), Cache.last_use (.lu, loads only), Cache.read_only (.nc, like __ldg, loads of an ro @device view only); shared arrays take no hint, and the host does K plain accesses and ignores it (E-WIDE). Every rule sees the part x[i..i + K]: in parallel i in m a lane may store out[4 * i..4 * i + 4] (E-PARALLEL-RACE otherwise), and the phase and global rules count all K elements. cairn explain lists each access with its width.
```
