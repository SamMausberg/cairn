## What changed from `v1.3.0` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/1.3.0): 3 identical-code, 4 unknown. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `ingest` | unknown | Expression form 'str' is not modeled. Its own code is identical; something it calls changed. |  |
| `main` | unknown | Expression form 'str' is not modeled. Its own code is identical; something it calls changed. |  |
| `puts` | unknown | Statement 'unsafe' is not modeled. |  |
| `sort_small` | unknown | The comparison ran past its 60 s limit and was stopped. | guards emitted 8 -> 7 |

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): main x2.582, puts x1.0.

Semantic version: **unknown (at least patch)**.

It is unknown, because these public functions are unproven: `ingest`, `main`, `puts`, `sort_small`.
