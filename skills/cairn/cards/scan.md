# The scan card

Selected by scan. Codes: E-SCAN-EXTENT E-SCAN-OP E-SCAN-ORDER E-SCAN-TARGET.

```text
let total = scan + out for i in n yield v; writes out[i] = v(0) + ... + v(i) and binds the whole; scan + exclusive out ... writes what came before i, so out[0] is the identity; a scan nobody reads the total of is a statement. The operators are reduce's: add_wrap, mul_wrap, & | ^ min max on integers, checked + on unsigned integers only (it traps exactly when the in-order total overflows), + and * on floats only in a sequential for scan (E-SCAN-OP, E-SCAN-ORDER). out is an rw view or buffer of exactly n elements (E-SCAN-TARGET, E-SCAN-EXTENT); v runs once per i and reads out only at out[i]. scan op parallel runs two passes on the lane pool and gives the in-order answer; over @device views it is CUB's scan, which takes device scratch (gpu_alloc, gpu_free).
```
