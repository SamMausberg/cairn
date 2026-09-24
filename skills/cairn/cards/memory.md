# The memory card

Sent to an agent when the program uses `buffer`, `stack`. Codes: `E-OWNER-ELEMENT`, `E-OWNER-EXTENT`, `E-STACK-EXTENT`, `E-STACK-LIMIT`.

```text
buffer scratch:u64[n] = zeroed; explicitly allocates zeroed heap storage; stack scratch:u64[32] = zeroed; explicitly reserves zeroed stack storage. Elements are non-linear values (E-LINEAR-STORAGE). Stack capacity is a literal; a function's stack declarations total at most 65536 bytes (E-STACK-LIMIT), not a bound on recursion, spills or native stack. Bind computed heap capacity to an immutable usize first (E-OWNER-EXTENT). The array is neither copyable nor returnable; lend it to helpers. len(scratch) is metadata; elements are writable without mut. Buf[u64](n) is the same array as a movable owner. Release is at normal scope exit, return, break and continue. Allocation failure and guards abort, promising no cleanup. No manual free or escaping borrow. Receipts expose alloc/free/zero_init and private reads/writes.
```
