| language | subjects | solved | median tokens | sum tokens | median turns | median seconds | sum cost (USD) |
|---|---|---|---|---|---|---|---|
| cairn | 2 | 2 | 1354461.0 | 2708922 | 25.0 | 289.3 | 1.547 |
| cpp | 2 | 2 | 185450.0 | 370900 | 9.0 | 76.3 | 0.2947 |
| rust | 2 | 2 | 142154.0 | 284308 | 7.0 | 79.35 | 0.3002 |

| pair | cells | solved | only first | only second | McNemar p | tokens, all | tokens, both solved | per-cell median (range) |
|---|---|---|---|---|---|---|---|---|
| cairn / cpp | 2 | 2 / 2 | 0 | 0 | 1.0 | 7.304 | 7.304 | 6.482 ([3.981, 8.983]) |
| cairn / rust | 2 | 2 / 2 | 0 | 0 | 1.0 | 9.528 | 9.528 | 8.559 ([4.629, 12.489]) |
| cpp / rust | 2 | 2 / 2 | 0 | 0 | 1.0 | 1.305 | 1.305 | 1.276 ([1.163, 1.39]) |

| replicate | task | language | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | dedupe | cairn | yes |  | success | 18 | 495765 | 0.2506 | 65.3 | 6 (0) |
| 1 | dedupe | cpp | yes |  | success | 8 | 124522 | 0.0627 | 26.5 | 1 (0) |
| 1 | dedupe | rust | yes |  | success | 7 | 107101 | 0.0495 | 16.4 | 1 (0) |
| 1 | records | cairn | yes |  | success | 32 | 2213157 | 1.2964 | 513.3 | 5 (0) |
| 1 | records | cpp | yes |  | success | 10 | 246378 | 0.232 | 126.1 | 1 (0) |
| 1 | records | rust | yes |  | success | 7 | 177207 | 0.2507 | 142.3 | 1 (0) |

Not preregistered, a description of where the calls went: calls that read the documentation and the characters they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.

| replicate | task | language | docs calls | docs characters | other calls | other characters | diagnostics |
|---|---|---|---|---|---|---|---|
| 1 | dedupe | cairn | 3 | 25850 | 14 | 13520 |  |
| 1 | dedupe | cpp | 0 | 0 | 7 | 3446 |  |
| 1 | dedupe | rust | 0 | 0 | 6 | 3538 |  |
| 1 | records | cairn | 10 | 88299 | 21 | 18614 |  |
| 1 | records | cpp | 0 | 0 | 9 | 5035 |  |
| 1 | records | rust | 0 | 0 | 6 | 4729 |  |
