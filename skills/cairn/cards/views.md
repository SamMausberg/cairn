# The views card

Sent to an agent when the program uses `buffer`, `len`, `stack`. Codes: `E-ALIAS`, `E-ARITY`.

```text
ro<T>[n] and rw<T>[n] borrow host storage; @host is optional, not a transfer. An extent is a literal or an earlier immutable usize parameter; a call omits all or none (E-ARITY): f(xs) is f(len(xs),xs). Indexes are usize and bounds-checked; len(view) reads metadata. Read-only views may alias; an rw view is disjoint from every other view of the call (E-ALIAS). Entry checks cover null/alignment/overflow/overlap numerically; the caller supplies live, initialized, typed storage with no conflicting access. A declared field extent gives len(c.price) the identity c.rows: no part. No view returns, resizing, implicit copy or parallelism.
```
