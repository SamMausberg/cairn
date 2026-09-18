#!/usr/bin/env python3
"""Compare the compact C++ family with independent finite value oracles."""

import ctypes as C
import json
import random
from pathlib import Path

R = Path(__file__).resolve().parents[1]
f = C.CDLL(str(R / "results/libtemplate.so")).cc_gain
f.argtypes = [C.c_size_t, C.c_size_t, C.POINTER(C.c_float), C.POINTER(C.c_float)]
f.restype = None
rng = random.Random(20260917)
ncases = 0
for k in range(1, 257):
    for n in [0, 1, 7, 31, 257]:
        vals = [rng.randrange(-256, 257) / 8 for _ in range(n)]
        a = (C.c_float * n)(*vals)
        b = (C.c_float * n)()
        f(k, n, b, a)
        assert list(b) == [C.c_float(v * k).value for v in vals]
        ncases += 1
print(
    json.dumps(
        {
            "cpp_template_family_cases": ncases,
            "all_passed": True,
            "scope": "Same family values; indexed C++ entry versus named CAIRN entries. Not an ABI or performance equivalence test.",
        },
        indent=2,
    )
)
