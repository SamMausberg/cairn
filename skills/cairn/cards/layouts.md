# The layouts card

Sent to an agent when the program uses `layout`. Codes: `E-LAYOUT`, `E-LAYOUT-CONSUMER`, `E-LAYOUT-GAP`, `E-LAYOUT-OVERLAP`.

```text
layout T = pad(rows(32, 32), 1); declares a compile-time storage layout: rows(R, C), cols(R, C), strided(R, C, SR, SC), pad(L, P) (the larger stride grows by P), swizzle(L, B, M, S) (CuTe's Swizzle<B, M, S>), transpose(L), tile(L, TR, TC) (coordinates i, j, r, c), inverse(D) (INV.row(r, c) is the participant holding element (r, c), INV.col(r, c) its value). layout D = spread(T, TR, TC, VR, VC); gives TR x TC participants, row by row, a VR x VC block of T each, repeated down and across T. In code T.at(r, c) is an element's offset, D.row(t, v) and D.col(t, v) participant t's value v, D.at(t, v) its offset in D's tile, each argument trapping outside its extent; T.cosize(), T.size(), T.extent(k), D.participants() and D.values() are constants. Every storage layout gives each element its own offset and every spread gives each element exactly one holder (E-LAYOUT-OVERLAP, E-LAYOUT-GAP); L.at(D.row(t, v), D.col(t, v)) needs L and D's tile of one shape (E-LAYOUT-CONSUMER); every offset stays below 2^63 - 1 and a swizzle's B + M + S at most 63; anything else malformed is E-LAYOUT. The receipt's layouts and cairn explain give coverage, runs (adjacent values one access moves) and bank_ways (shared-memory conflicts).
```
