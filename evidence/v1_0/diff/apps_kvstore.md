## What changed from `v0.8.3` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/0.8.3): 12 identical-code, 6 unknown. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `apply` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 1 -> 2; guards discharged 0 -> 1 |
| `compact_log` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 9 -> 11; guards discharged 0 -> 2 |
| `main` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 4 -> 2; guards discharged 1 -> 0; guards emitted 2 -> 1 |
| `put` | unknown | An owner inside a record, a sum or an array is not modeled. Its own code is identical; something it calls changed. |  |
| `replay` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 19 -> 21; guards discharged 8 -> 10 |
| `scenario` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 13 -> 22; guards discharged 0 -> 9 |

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): apply x1.0, main x1.0, scenario x1.0.

Semantic version: **unknown (at least patch)**.

It is unknown, because these public functions are unproven: `apply`, `compact_log`, `main`, `put`, `replay`, `scenario`.
