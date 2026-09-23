# The closures card

Sent to an agent when the program uses `dyn`.

```text
fn(u64) -> u64 is a copyable code pointer to a plain declared function of values. ro<fn(u64) -> u64> is a borrowed callable: pass a declared function or write the closure in place, apply(n, xs, |x:u64| -> u64 { return x + bias; }). A closure captures its scope by reference, exists only as that argument, never allocates, and its effects belong to the function that wrote it. What it captures it borrows for that call (rw where it writes), so the same call cannot lend, move or write those places.

ro<dyn Shape> and rw<dyn Shape> parameters take any named place whose type implements the trait; calls through them add the dispatch effect and the effects of every implementation. Dynamic references are never values; Dyn[Shape](value) is the owned form, an affine heap value (alloc, free) that dispatches and lends itself as dyn.
```
