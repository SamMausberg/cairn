# The compact card

Sent to an agent when the program uses `compact`. Codes: `E-COLLECT-BINDING`, `E-COLLECT-CAPACITY`, `E-COLLECT-SELF-READ`.

```text
let used = compact out for i in n where p yield v; out is an rw borrow or scoped buffer of capacity exactly n (or len(out)); p is bool, v has the element type, and neither may read out or call a writing or allocating function. Selected values fill a stable prefix in order; the tail is unchanged; no allocation or synchronization; the private cursor emits at most once per input. Its arithmetic certificates do not prove the compiler, ownership rules or native backend.
```
