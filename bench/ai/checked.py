"""The oracles, hidden cases and input rules of the three tasks the 1.1 evaluation adds, where CAIRN's checks are the
point: a threaded sieve whose natural shared design races, a scan written as GPU blocks of threads that meet at
barriers, and a repair whose starter shares counters between threads without synchronization.

`tasks.py` makes them tasks beside the ten of 1.0. Like those oracles, these are written from each task's SPEC.md in
Python, independently of the reference programs, and `harness.py verify` holds the references and starters to them.
"""

from __future__ import annotations

import functools
import itertools
import random

U64 = 2**64
SIEVE_MAX = 20_000_000
SCAN_MAX = 100_000


def ints(data: bytes) -> list[int]:
    return [int(t) for t in data.split()]


# sieve ----------------------------------------------------------------------------------------------------------


def primes_upto(n: int) -> list[int]:
    if n < 2:
        return []
    marks = bytearray([1]) * (n + 1)
    marks[0] = marks[1] = 0
    for p in range(2, int(n**0.5) + 1):
        if marks[p]:
            marks[p * p :: p] = bytes(len(range(p * p, n + 1, p)))
    return [i for i, m in enumerate(marks) if m]


@functools.cache  # several judges of a run ask for the same large cases
def sieve(data: bytes) -> bytes:
    (n,) = ints(data)
    found = primes_upto(n)
    lines = [f"count {len(found)}", f"sum {sum(found)}", f"last {found[-1]}" if found else "last none"]
    if len(found) < 2:
        lines.append("gap none")
    else:
        gap, at = max((b - a, -a) for a, b in itertools.pairwise(found))
        lines.append(f"gap {gap} {-at}")
    return "".join(line + "\n" for line in lines).encode()


def sieve_cases(rng: random.Random) -> list[bytes]:
    fixed = [0, 1, 2, 3, 4, 5, 10, 11, 100, 1000, 7919, 7920, 65536, 1_000_000, 999_983, 4_652_506, 4_652_507, 9_999_991,
             10_000_000, 17_051_887, 19_999_999, SIEVE_MAX]  # fmt: skip
    drawn = [rng.randrange(2, 5000) for _ in range(3)] + [rng.randrange(10**6, SIEVE_MAX) for _ in range(2)]
    return [f"{n}\n".encode() for n in fixed + drawn]


# block_scan -----------------------------------------------------------------------------------------------------


def block_scan(data: bytes) -> bytes:
    n, *values = ints(data)
    out, total = [], 0
    for v in values[:n]:
        total += v
        out.append(f"{total}\n")
    return "".join(out).encode()


def block_scan_cases(rng: random.Random) -> list[bytes]:
    shapes = [0, 1, 2, 31, 255, 256, 257, 511, 512, 1000, 4096, 12345, 65536, 65537, SCAN_MAX]
    out = []
    for n in shapes:
        pick = rng.choice(
            [lambda: rng.randrange(2**32), lambda: rng.choice([0, 1, 2**32 - 1]), lambda: rng.randrange(10)]
        )
        out.append(f"{n}\n" + " ".join(str(pick()) for _ in range(n)) + "\n")
    out.append("300\n" + " ".join(["4294967295"] * 300) + "\n")
    return [c.encode() for c in out]


# tally (repair) -------------------------------------------------------------------------------------------------


def tally(data: bytes) -> bytes:
    _, n, t, *values = ints(data)
    above = [v for v in values[:n] if v > t]
    top = f"max {max(values[:n])}" if n else "max none"
    return f"above {len(above)}\nsum {sum(above) % U64}\n{top}\n".encode()


def tally_cases(rng: random.Random) -> list[bytes]:
    shapes = [(1, 0), (16, 0), (3, 10), (4, 3), (16, 17), (2, 1000), (7, 5000), (8, 99999), (16, 200000), (5, 200000)]
    out = []
    for k, n in shapes:
        values = [rng.choice([rng.randrange(1000), U64 - 1, rng.randrange(U64)]) for _ in range(n)]
        t = rng.choice([0, 500, U64 // 2, U64 - 1, rng.randrange(U64)])
        if (k, n) == (3, 10):
            values, t = list(range(1, 11)), 4
        out.append(f"{k} {n} {t}\n" + " ".join(map(str, values)) + "\n")
    return [c.encode() for c in out]


# What each SPEC.md promises about its input.


def valid_sieve(data: bytes) -> bool:
    t = ints(data)
    return len(t) == 1 and 0 <= t[0] <= SIEVE_MAX


def valid_block_scan(data: bytes) -> bool:
    n, *values = ints(data)
    return 0 <= n <= SCAN_MAX and len(values) == n and all(0 <= v < 2**32 for v in values)


def valid_tally(data: bytes) -> bool:
    k, n, t, *values = ints(data)
    return 1 <= k <= 16 and 0 <= n <= 200000 and 0 <= t < U64 and len(values) == n and all(0 <= v < U64 for v in values)
