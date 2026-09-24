Cells: 1. Resamples of the cells: 10000, seed 20260924.

| arm | subjects | solved | solve rate [95%] | USD per solved task [95%] | tokens per solved task [95%] |
|---|---|---|---|---|---|
| plugin | 1 | 1 | 1.0 [1.0, 1.0] | 1.3893 [1.3893, 1.3893] | 2950002 [2950002, 2950002] |
| cairn | 1 | 1 | 1.0 [1.0, 1.0] | 1.4205 [1.4205, 1.4205] | 3327198 [3327198, 3327198] |
| cpp | 1 | 1 | 1.0 [1.0, 1.0] | 0.9382 [0.9382, 0.9382] | 1675857 [1675857, 1675857] |
| rust | 1 | 1 | 1.0 [1.0, 1.0] | 0.5038 [0.5038, 0.5038] | 667878 [667878, 667878] |

| ratio | USD per solved task [95%] | tokens per solved task [95%] | McNemar p (only first, only second) |
|---|---|---|---|
| plugin/cairn | 0.978 [0.978, 0.978] | 1 [1, 1] | 1.0 (0, 0) |
| plugin/cpp | 1.4808 [1.4808, 1.4808] | 2 [2, 2] | 1.0 (0, 0) |
| plugin/rust | 2.7576 [2.7576, 2.7576] | 4 [4, 4] | 1.0 (0, 0) |
| cairn/cpp | 1.5141 [1.5141, 1.5141] | 2 [2, 2] | 1.0 (0, 0) |
| cairn/rust | 2.8196 [2.8196, 2.8196] | 5 [5, 5] | 1.0 (0, 0) |
| cpp/rust | 1.8622 [1.8622, 1.8622] | 3 [3, 3] | 1.0 (0, 0) |

| arm | median turns [quartiles] | median seconds [quartiles] | sum USD | sum tokens | sanitizer | abort | panic | unsafe |
|---|---|---|---|---|---|---|---|---|
| plugin | 48 [48, 48] | 527.5 [527.5, 527.5] | 1.3893 | 2950002 | 0 | 0 | 0 | 0 |
| cairn | 38 [38, 38] | 631.6 [631.6, 631.6] | 1.4205 | 3327198 | 0 | 0 | 0 | 0 |
| cpp | 39 [39, 39] | 598.2 [598.2, 598.2] | 0.9382 | 1675857 | 0 | 0 | 0 | 0 |
| rust | 20 [20, 20] | 453.0 [453.0, 453.0] | 0.5038 | 667878 | 0 | 0 | 0 | 0 |

| replicate | task | arm | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | block_scan | plugin | yes |  | success | 48 | 2950002 | 1.3893 | 527.5 | 8 (3) |
| 1 | block_scan | cairn | yes |  | success | 38 | 3327198 | 1.4205 | 631.6 | 10 (0) |
| 1 | block_scan | cpp | yes |  | success | 39 | 1675857 | 0.9382 | 598.2 | 9 (0) |
| 1 | block_scan | rust | yes |  | success | 20 | 667878 | 0.5038 | 453.0 | 4 (0) |

Not preregistered, a description of where the calls went: calls that read documentation (the `docs/` of the sandbox or the plugin, the skill and its cards) and the characters they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.

| replicate | task | arm | docs calls | docs characters | other calls | other characters | diagnostics |
|---|---|---|---|---|---|---|---|
| 1 | block_scan | cairn | 13 | 139914 | 24 | 19109 | E-COOP-GLOBAL 1, E-EFFECT-ORDER 1 |
| 1 | block_scan | cpp | 0 | 0 | 38 | 26510 |  |
| 1 | block_scan | plugin | 20 | 72002 | 26 | 19492 | E-TYPE-MISMATCH 1, E-EFFECT-ORDER 1 |
| 1 | block_scan | rust | 0 | 0 | 19 | 5136 |  |
