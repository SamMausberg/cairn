| language | subjects | solved | median tokens | sum tokens | median turns | median seconds | sum cost (USD) |
|---|---|---|---|---|---|---|---|
| cairn | 20 | 20 | 1169110.5 | 34667459 | 30.5 | 203.65 | 15.4841 |
| cpp | 20 | 20 | 135842.0 | 2977636 | 8.0 | 34.35 | 1.8819 |
| rust | 20 | 20 | 128332.5 | 2816890 | 8.0 | 29.3 | 1.8556 |

| pair | cells | solved | only first | only second | McNemar p | tokens, all | tokens, both solved | per-cell median (range) |
|---|---|---|---|---|---|---|---|---|
| cairn / cpp | 20 | 20 / 20 | 0 | 0 | 1.0 | 11.643 | 11.643 | 6.96 ([1.457, 50.333]) |
| cairn / rust | 20 | 20 / 20 | 0 | 0 | 1.0 | 12.307 | 12.307 | 8.219 ([1.711, 50.879]) |
| cpp / rust | 20 | 20 / 20 | 0 | 0 | 1.0 | 1.057 | 1.057 | 1.171 ([0.556, 1.975]) |

| replicate | task | language | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | basis_points | cairn | yes |  | success | 16 | 387217 | 0.1956 | 59.1 | 7 (1) |
| 1 | basis_points | cpp | yes |  | success | 10 | 163830 | 0.0823 | 32.1 | 1 (0) |
| 1 | basis_points | rust | yes |  | success | 8 | 125232 | 0.0594 | 23.0 | 1 (0) |
| 1 | chunk_sums | cairn | yes |  | success | 63 | 6799837 | 2.3256 | 566.5 | 20 (9) |
| 1 | chunk_sums | cpp | yes |  | success | 8 | 135098 | 0.0827 | 35.6 | 2 (0) |
| 1 | chunk_sums | rust | yes |  | success | 8 | 133648 | 0.0808 | 31.7 | 1 (0) |
| 1 | csv_field | cairn | yes |  | success | 9 | 159362 | 0.094 | 37.3 | 4 (0) |
| 1 | csv_field | cpp | yes |  | success | 7 | 109370 | 0.057 | 18.4 | 1 (0) |
| 1 | csv_field | rust | yes |  | success | 6 | 93150 | 0.0489 | 17.8 | 1 (0) |
| 1 | dedupe | cairn | yes |  | success | 25 | 668222 | 0.2999 | 87.5 | 10 (0) |
| 1 | dedupe | cpp | yes |  | success | 8 | 125414 | 0.0581 | 18.8 | 1 (0) |
| 1 | dedupe | rust | yes |  | success | 7 | 107023 | 0.0493 | 25.2 | 1 (0) |
| 1 | histogram | cairn | yes |  | success | 42 | 2247356 | 0.8452 | 204.8 | 9 (2) |
| 1 | histogram | cpp | yes |  | success | 10 | 176212 | 0.1065 | 42.3 | 1 (0) |
| 1 | histogram | rust | yes |  | success | 8 | 127422 | 0.0684 | 26.9 | 1 (0) |
| 1 | pool | cairn | yes |  | success | 39 | 2646994 | 1.2705 | 403.3 | 6 (0) |
| 1 | pool | cpp | yes |  | success | 10 | 202413 | 0.1608 | 80.5 | 2 (0) |
| 1 | pool | rust | yes |  | success | 7 | 129243 | 0.109 | 59.0 | 1 (0) |
| 1 | records | cairn | yes |  | success | 30 | 2138305 | 1.3504 | 501.2 | 5 (0) |
| 1 | records | cpp | yes |  | success | 9 | 210019 | 0.1958 | 101.6 | 1 (0) |
| 1 | records | rust | yes |  | success | 10 | 218463 | 0.2108 | 114.2 | 1 (0) |
| 1 | rle | cairn | yes |  | success | 31 | 1434866 | 0.6755 | 202.5 | 6 (1) |
| 1 | rle | cpp | yes |  | success | 11 | 207388 | 0.1343 | 59.4 | 1 (0) |
| 1 | rle | rust | yes |  | success | 9 | 154076 | 0.1005 | 42.8 | 1 (0) |
| 1 | split_sum | cairn | yes |  | success | 29 | 739810 | 0.3251 | 158.1 | 14 (1) |
| 1 | split_sum | cpp | yes |  | success | 11 | 183687 | 0.0909 | 33.1 | 2 (0) |
| 1 | split_sum | rust | yes |  | success | 6 | 92991 | 0.049 | 15.2 | 1 (0) |
| 1 | varint | cairn | yes |  | success | 12 | 308034 | 0.1894 | 52.6 | 5 (0) |
| 1 | varint | cpp | yes |  | success | 8 | 129169 | 0.0684 | 27.1 | 1 (0) |
| 1 | varint | rust | yes |  | success | 8 | 126889 | 0.0613 | 19.5 | 1 (0) |
| 2 | basis_points | cairn | yes |  | success | 19 | 489887 | 0.2499 | 75.1 | 6 (1) |
| 2 | basis_points | cpp | yes |  | success | 8 | 125634 | 0.065 | 22.6 | 2 (0) |
| 2 | basis_points | rust | yes |  | success | 6 | 91369 | 0.0443 | 14.2 | 1 (0) |
| 2 | chunk_sums | cairn | yes |  | success | 37 | 2878782 | 1.2772 | 370.2 | 10 (3) |
| 2 | chunk_sums | cpp | yes |  | success | 8 | 137938 | 0.09 | 39.0 | 2 (0) |
| 2 | chunk_sums | rust | yes |  | success | 9 | 152849 | 0.0948 | 36.3 | 2 (0) |
| 2 | csv_field | cairn | yes |  | success | 14 | 292699 | 0.1564 | 50.1 | 5 (1) |
| 2 | csv_field | cpp | yes |  | success | 7 | 109656 | 0.0566 | 18.4 | 1 (0) |
| 2 | csv_field | rust | yes |  | success | 6 | 93442 | 0.0513 | 19.3 | 1 (0) |
| 2 | dedupe | cairn | yes |  | success | 16 | 351083 | 0.1754 | 53.4 | 6 (1) |
| 2 | dedupe | cpp | yes |  | success | 8 | 125608 | 0.0602 | 20.9 | 1 (0) |
| 2 | dedupe | rust | yes |  | success | 7 | 107291 | 0.0497 | 15.6 | 1 (0) |
| 2 | histogram | cairn | yes |  | success | 40 | 3453950 | 1.4178 | 364.5 | 10 (0) |
| 2 | histogram | cpp | yes |  | success | 8 | 133480 | 0.0832 | 40.3 | 1 (0) |
| 2 | histogram | rust | yes |  | success | 9 | 164746 | 0.1172 | 54.7 | 1 (0) |
| 2 | pool | cairn | yes |  | success | 42 | 4331121 | 1.8929 | 677.8 | 6 (0) |
| 2 | pool | cpp | yes |  | success | 8 | 146859 | 0.1076 | 50.1 | 1 (0) |
| 2 | pool | rust | yes |  | success | 9 | 181105 | 0.1464 | 81.0 | 1 (0) |
| 2 | records | cairn | yes |  | success | 37 | 2743951 | 1.4648 | 547.2 | 9 (0) |
| 2 | records | cpp | yes |  | success | 8 | 179819 | 0.1741 | 96.6 | 1 (0) |
| 2 | records | rust | yes |  | success | 12 | 323517 | 0.2941 | 160.0 | 6 (0) |
| 2 | rle | cairn | yes |  | success | 33 | 1437836 | 0.706 | 288.8 | 6 (0) |
| 2 | rle | cpp | yes |  | success | 8 | 136586 | 0.0852 | 46.8 | 1 (0) |
| 2 | rle | rust | yes |  | success | 9 | 155206 | 0.097 | 41.2 | 1 (0) |
| 2 | split_sum | cairn | yes |  | success | 31 | 903355 | 0.4391 | 213.0 | 14 (1) |
| 2 | split_sum | cpp | yes |  | success | 8 | 129021 | 0.0677 | 24.6 | 1 (0) |
| 2 | split_sum | rust | yes |  | success | 9 | 145413 | 0.0745 | 33.3 | 1 (0) |
| 2 | varint | cairn | yes |  | success | 13 | 254792 | 0.1334 | 42.8 | 4 (0) |
| 2 | varint | cpp | yes |  | success | 7 | 110435 | 0.0555 | 23.8 | 1 (0) |
| 2 | varint | rust | yes |  | success | 6 | 93815 | 0.0489 | 16.4 | 1 (0) |

Not preregistered, a description of where the calls went: calls that read the documentation and the characters they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.

| replicate | task | language | docs calls | docs characters | other calls | other characters | diagnostics |
|---|---|---|---|---|---|---|---|
| 1 | basis_points | cairn | 1 | 4605 | 14 | 14381 |  |
| 1 | basis_points | cpp | 0 | 0 | 9 | 3155 |  |
| 1 | basis_points | rust | 0 | 0 | 7 | 3461 |  |
| 1 | chunk_sums | cairn | 23 | 177833 | 39 | 22833 | E-LITERAL-RANGE 7, E-WRAP-TYPE 1, E-EFFECT-ORDER 1 |
| 1 | chunk_sums | cpp | 0 | 0 | 7 | 3544 |  |
| 1 | chunk_sums | rust | 0 | 0 | 7 | 3736 |  |
| 1 | csv_field | cairn | 0 | 0 | 8 | 11109 |  |
| 1 | csv_field | cpp | 0 | 0 | 6 | 4095 |  |
| 1 | csv_field | rust | 0 | 0 | 5 | 4075 |  |
| 1 | dedupe | cairn | 6 | 12327 | 18 | 14718 |  |
| 1 | dedupe | cpp | 0 | 0 | 7 | 3418 |  |
| 1 | dedupe | rust | 0 | 0 | 6 | 3548 |  |
| 1 | histogram | cairn | 2 | 22455 | 39 | 71731 | E-OWNER-EXTENT 1, E-EFFECT-ORDER 1 |
| 1 | histogram | cpp | 0 | 0 | 9 | 2980 |  |
| 1 | histogram | rust | 0 | 0 | 7 | 3190 |  |
| 1 | pool | cairn | 12 | 88576 | 26 | 12227 | E-TYPE-MISMATCH 1, E-EFFECT-ORDER 1 |
| 1 | pool | cpp | 0 | 0 | 9 | 3698 |  |
| 1 | pool | rust | 0 | 0 | 6 | 3869 |  |
| 1 | records | cairn | 10 | 142757 | 19 | 13792 |  |
| 1 | records | cpp | 0 | 0 | 8 | 4468 |  |
| 1 | records | rust | 0 | 0 | 9 | 5302 |  |
| 1 | rle | cairn | 14 | 61696 | 16 | 12862 | E-EFFECT-ORDER 1 |
| 1 | rle | cpp | 0 | 0 | 10 | 4190 |  |
| 1 | rle | rust | 0 | 0 | 8 | 4223 |  |
| 1 | split_sum | cairn | 0 | 0 | 28 | 19643 |  |
| 1 | split_sum | cpp | 0 | 0 | 10 | 4417 |  |
| 1 | split_sum | rust | 0 | 0 | 5 | 3687 |  |
| 1 | varint | cairn | 1 | 20566 | 10 | 20158 |  |
| 1 | varint | cpp | 0 | 0 | 7 | 4840 |  |
| 1 | varint | rust | 0 | 0 | 7 | 4680 |  |
| 2 | basis_points | cairn | 4 | 18605 | 14 | 9503 |  |
| 2 | basis_points | cpp | 0 | 0 | 7 | 3206 |  |
| 2 | basis_points | rust | 0 | 0 | 5 | 3421 |  |
| 2 | chunk_sums | cairn | 12 | 119062 | 24 | 16380 | E-LITERAL-RANGE 2, E-VIEW-ALIAS 1 |
| 2 | chunk_sums | cpp | 0 | 0 | 7 | 3767 |  |
| 2 | chunk_sums | rust | 0 | 0 | 8 | 3979 |  |
| 2 | csv_field | cairn | 3 | 3209 | 10 | 16060 |  |
| 2 | csv_field | cpp | 0 | 0 | 6 | 4320 |  |
| 2 | csv_field | rust | 0 | 0 | 5 | 4208 |  |
| 2 | dedupe | cairn | 2 | 7454 | 13 | 10297 |  |
| 2 | dedupe | cpp | 0 | 0 | 7 | 3605 |  |
| 2 | dedupe | rust | 0 | 0 | 6 | 3717 |  |
| 2 | histogram | cairn | 16 | 150010 | 23 | 16302 | E-TYPE-MISMATCH 1, E-EFFECT-ORDER 1 |
| 2 | histogram | cpp | 0 | 0 | 7 | 2724 |  |
| 2 | histogram | rust | 0 | 0 | 8 | 2867 |  |
| 2 | pool | cairn | 12 | 153076 | 29 | 13387 | E-TYPE-MISMATCH 1 |
| 2 | pool | cpp | 0 | 0 | 7 | 3574 |  |
| 2 | pool | rust | 0 | 0 | 8 | 4354 |  |
| 2 | records | cairn | 16 | 96203 | 20 | 18476 |  |
| 2 | records | cpp | 0 | 0 | 7 | 4403 |  |
| 2 | records | rust | 0 | 0 | 11 | 5193 |  |
| 2 | rle | cairn | 18 | 64654 | 14 | 10743 |  |
| 2 | rle | cpp | 0 | 0 | 7 | 3432 |  |
| 2 | rle | rust | 0 | 0 | 8 | 4085 |  |
| 2 | split_sum | cairn | 4 | 10031 | 26 | 18964 |  |
| 2 | split_sum | cpp | 0 | 0 | 7 | 3999 |  |
| 2 | split_sum | rust | 0 | 0 | 8 | 3893 |  |
| 2 | varint | cairn | 1 | 930 | 11 | 13808 |  |
| 2 | varint | cpp | 0 | 0 | 6 | 4740 |  |
| 2 | varint | rust | 0 | 0 | 5 | 4627 |  |
