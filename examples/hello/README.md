# hello

The smallest complete project: a manifest, two modules and one finite task contract.

```sh
cairn run  examples/hello     # "status": "program-exited", "exit_code": 0
cairn test examples/hello     # "status": "passed-finite-tests", "cases": 81
```

`src/math.cairn` is the whole point: `average` computes the floor of the mean without overflowing the intermediate sum. `tests/average.json` pins it at 81 boundary pairs, including `u64` maxima, and `cairn test` builds a shared library and calls the symbol for each one.
