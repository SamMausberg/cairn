# basics

Three single-file sources with no `main`, read by `cairn check`, by the density harness (`tools/checks/density.py`) and by the codegen benchmarks.

```sh
cairn check examples/basics/native.cairn   # "functions": 23
cairn check examples/basics/family.cairn   # "functions": 256
cairn check examples/basics/wire.cairn     # "functions": 3
```

`native.cairn` is the breadth sample: records, sums, `each` loops, `compact`, checked arithmetic, explicit conversions and every guard site. `family.cairn` is five lines that produce 256 typed specializations, which is what `family gain = scale[1..257];` means. `wire.cairn` is one record and one `derive wire`; read what it generated with `cairn expand examples/basics/wire.cairn`.
