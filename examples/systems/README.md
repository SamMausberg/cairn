# systems

Two systems idioms in one project: a decimal parser that reports the offset of the byte at fault, and a counting sort whose input and output views stay disjoint.

```sh
cairn run  examples/systems     # "status": "program-exited", "exit_code": 0
cairn test examples/systems     # decimal_kind: 8 cases, sorted_even: 7 cases, both passed
```

`src/parse.cairn` answers with a sum (`Value(u64)`, `Invalid(at)`, `Overflow(at)`, `Empty`) and never a sentinel, and it detects overflow before it happens. `src/sort.cairn` keeps its histogram in `stack counts:usize[256]`, so nothing is allocated and the `rw` output cannot alias the `ro` input.
