# The lends card

Sent to an agent when the program uses `lends`. Codes: `E-LENDS`.

```text
struct Vec[T] { data:Buf[T]; len:usize; lends data[0..len]; } lends that part: named where a view is expected, v is v.data[0..v.len], guarded and leased as written, and for x in v walks it. Bounds are literals or usize fields (E-LENDS).
```
