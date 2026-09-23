## What changed from `v1.3.0` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/1.3.0): 7 identical-code, 2 unknown. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `main` | unknown | Statement 'unsafe' is not modeled. | effects -diverge; guards written 21 -> 22; guards discharged 3 -> 4 |
| `report` | unknown | Statement 'unsafe' is not modeled. | effects -diverge -local_read -local_write -stack_storage -zero_init |

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): main x1.0.

Semantic version: **unknown (at least patch)**.

It is unknown, because these public functions are unproven: `main`, `report`.
