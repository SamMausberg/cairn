# sketch

A host-bound sketch. The signature, the task and the reference belong to the host; the only thing searched is one named choice. `before.cairn` returns `(x + y) / 2`, which overflows, and `after.cairn` is what the deterministic search picks.

```sh
python3 tools/ai/sketch_demo.py --out /tmp/sketchdemo
# {"status": "passed", "native_cases": 81, "model_used": false}
# searches: 4 candidates, 4 semantic checker calls, 8 SMT queries; then 3 calls and 6 queries from cache
```

`average_0_reference-totality.smt2` and `average_1_equivalence.smt2` are the two queries that settled it, in that order. `choices.json` holds the winning expression and `finite_task.json` the 81 cases the result is then run against. No model is called: the search is over a fixed list of four expressions.
