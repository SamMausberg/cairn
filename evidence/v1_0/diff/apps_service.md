## What changed from `v1.3.0` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/1.3.0): 6 identical-code, 2 unknown, 2 signature-changed, 3 added, 1 removed. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `main` | unknown | Expression form 'str' is not modeled. | effects -ffi:accept -ffi:recv -stack_storage; guards written 3 -> 2; guards emitted 2 -> 1 |
| `run` | unknown | Expression form 'str' is not modeled. | effects -ffi:accept -ffi:recv -stack_storage; guards written 1 -> 20; guards discharged 1 -> 3; guards emitted 0 -> 10 |
| `respond` | signature-changed |  | signature (ro<std.net.Socket>, rw<Table>, usize, ro<u8>[n]@host) -> std.core.Result[bool, std.io.IoError] became (i32, rw<Table>, usize, ro<u8>[n]@host) -> std.core.Result[bool, std.io.IoError]; effects -read:c; guards written 18 -> 23; guards discharged 3 -> 11; guards emitted 15 -> 12 |
| `say` | signature-changed |  | signature (ro<std.net.Socket>, usize, ro<u8>[n]@host) -> std.core.Result[usize, std.io.IoError] became (i32, usize, ro<u8>[n]@host) -> std.core.Result[usize, std.io.IoError]; effects -read:c |
| `admit` | added | (rw<IoRing>, rw<Buf[Client]>, i32) -> void |  |
| `hang_up` | added | (rw<Buf[Client]>, usize) -> void |  |
| `take_in` | added | (rw<Client>, rw<Table>, usize, ro<u8>[n]@host) -> std.core.Result[bool, std.io.IoError] |  |
| `serve` | removed | (ro<std.net.Socket>, rw<Table>) -> std.core.Result[bool, std.io.IoError] |  |

Types: `Client` added.

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): main x1.0, run x1.0.

Semantic version: **major**, because respond's signature changed; say's signature changed; serve was removed; admit was added; hang_up was added; take_in was added; type Client was added.

It is these public functions are unproven, and cannot raise it further: `main`, `run`.
