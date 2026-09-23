| language | subjects | solved | median tokens | sum tokens | median turns | median seconds | sum cost (USD) |
|---|---|---|---|---|---|---|---|
| cairn | 1 | 1 | 224137 | 224137 | 12 | 55.9 | 0.3167 |
| cpp | 0 | 0 |  |  |  |  |  |
| rust | 0 | 0 |  |  |  |  |  |

| pair | cells | solved | only first | only second | McNemar p | tokens, all | tokens, both solved | per-cell median (range) |
|---|---|---|---|---|---|---|---|---|
| cairn / cpp | 0 | 0 / 0 | 0 | 0 | 1.0 | None | None | None (None) |
| cairn / rust | 0 | 0 / 0 | 0 | 0 | 1.0 | None | None | None (None) |
| cpp / rust | 0 | 0 / 0 | 0 | 0 | 1.0 | None | None | None (None) |

| replicate | task | language | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | histogram | cairn | yes |  | success | 12 | 224137 | 0.3167 | 55.9 | 3 (1) |

Not preregistered, a description of where the calls went: calls that read the documentation and the characters they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.

| replicate | task | language | docs calls | docs characters | other calls | other characters | diagnostics |
|---|---|---|---|---|---|---|---|
| 1 | histogram | cairn | 5 | 34830 | 6 | 5154 | E-EFFECT-ORDER 2 |
