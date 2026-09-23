"""The ten tasks of the equal-budget benchmark: what each program must print, and the hidden cases.

Every task is a whole program that reads standard input and writes standard output, so one oracle and one set of
cases judge all three languages. The oracle is written from the task's SPEC.md in Python, independently of the six
reference programs, and `harness.py verify` holds the references, the starters and each SPEC.md example to it.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
I64 = (-(2**63), 2**63 - 1)
U64 = 2**64
SEED = 20260923


@dataclass(frozen=True)
class Task:
    name: str
    kind: (
        str  # "implement": the starter is scaffolding; "repair": the starter is a whole program with one planted defect
    )
    threads: bool  # the program must run work on several threads, and ThreadSanitizer judges it too
    oracle: Callable[[bytes], bytes]
    cases: Callable[[random.Random], list[bytes]]

    @property
    def spec(self) -> str:
        return (HERE / "tasks" / self.name / "SPEC.md").read_text()


def ints(data: bytes) -> list[int]:
    return [int(t) for t in data.split()]


# histogram ------------------------------------------------------------------------------------------------------


def histogram(data: bytes) -> bytes:
    n, shift, *values = ints(data)
    counts = Counter((v >> shift) & 255 for v in values[:n])
    return "".join(f"{b} {counts[b]}\n" for b in range(256) if counts[b]).encode()


def histogram_cases(rng: random.Random) -> list[bytes]:
    shapes = [(0, 0), (1, 0), (1, 24), (7, 3), (256, 0), (1000, 8), (5000, 24), (20000, 5), (150000, 0), (300000, 13)]
    out = []
    for n, shift in shapes:
        pick = rng.choice(
            [lambda: rng.randrange(2**32), lambda: rng.choice([0, 255, 256, 2**32 - 1]), lambda: rng.randrange(4096)]
        )
        out.append(f"{n} {shift}\n" + " ".join(str(pick()) for _ in range(n)) + "\n")
    out.append("3 24\n4294967295 16777215 16777216\n")
    return [c.encode() for c in out]


# chunk_sums -----------------------------------------------------------------------------------------------------


def chunk_sums(data: bytes) -> bytes:
    k, n, *values = ints(data)
    lines, sums, broken = [], [], False
    for j in range(k):
        total, over = 0, False
        for v in values[j * n // k : (j + 1) * n // k]:
            total += v
            over = over or not I64[0] <= total <= I64[1]
        broken = broken or over
        sums.append(total)
        lines.append(f"chunk {j} overflow" if over else f"chunk {j} {total}")
    grand = 0
    for s in sums:
        grand += s
        broken = broken or not I64[0] <= grand <= I64[1]
    lines.append("total overflow" if broken else f"total {grand}")
    return "".join(line + "\n" for line in lines).encode()


def chunk_sums_cases(rng: random.Random) -> list[bytes]:
    big = [I64[0], I64[1], I64[1] - 1, I64[0] + 1, 2**62, -(2**62)]
    out = []
    for k, n, mode in [(1, 0, "small"), (16, 0, "small"), (16, 5, "small"), (3, 7, "big"), (2, 4, "edge"),
                       (4, 1000, "small"), (8, 1000, "big"), (5, 12, "edge"), (16, 40000, "small"), (7, 200000, "small"),
                       (6, 60, "big"), (2, 2, "back"), (3, 9, "total"), (1, 3, "edge"), (13, 13, "big")]:  # fmt: skip
        if mode == "small":
            values = [rng.randrange(-(10**6), 10**6) for _ in range(n)]
        elif mode == "big":
            values = [rng.choice(big) if rng.random() < 0.3 else rng.randrange(-(10**12), 10**12) for _ in range(n)]
        elif mode == "back":  # overflows part way, then a later value brings the sum back in range
            values = [I64[1], 1, -2, 0][:n] if n >= 3 else [I64[1], 1]
        elif mode == "total":  # every chunk fits, the total does not
            values = [2**62, 2**62, 1] * 3
        else:
            values = [rng.choice(big) for _ in range(n)]
        out.append(f"{k} {len(values)}\n" + " ".join(map(str, values)) + "\n")
    out.append(f"2 4\n{I64[1]} 1 -1 {I64[0]}\n")
    out.append(f"3 3\n{I64[1]} {I64[1]} {I64[1]}\n")
    out.append(f"1 3\n{I64[0]} -1 5\n")
    return [c.encode() for c in out]


# records --------------------------------------------------------------------------------------------------------

LIMITS = {"id": 2**32 - 1, "qty": 2**16 - 1, "price": 2**63 - 1}


def lines_of(data: bytes) -> list[bytes]:
    if not data:
        return []
    lines = data.split(b"\n")
    return lines[:-1] if data.endswith(b"\n") else lines


def record(number: int, line: bytes) -> str:
    fields = line.split(b",")
    if len(fields) < 3:
        return f"err {number} {len(line) + 1} missing"
    if len(fields) > 3:
        third = len(fields[0]) + len(fields[1]) + len(fields[2]) + 3
        return f"err {number} {third} extra"
    values = []
    start = 1
    for name, field in zip(("id", "qty", "price"), fields, strict=True):
        allowed = b"0123456789." if name == "price" else b"0123456789"
        if not field:
            return f"err {number} {start} empty"
        for i, byte in enumerate(field):
            if byte not in allowed:
                return f"err {number} {start + i} digit"
        if name == "price":
            whole, dot, cents = field.partition(b".")
            if not whole or not dot or len(cents) != 2 or b"." in cents:
                return f"err {number} {start} format"
            value = int(whole) * 100 + int(cents)
        else:
            value = int(field)
        if value > LIMITS[name]:
            return f"err {number} {start} range"
        values.append(value)
        start += len(field) + 1
    return "ok {} {} {}".format(*values)


def records(data: bytes) -> bytes:
    return "".join(record(i + 1, line) + "\n" for i, line in enumerate(lines_of(data))).encode()


def records_cases(rng: random.Random) -> list[bytes]:
    fixed = [
        b"", b"\n", b"\n\n", b"1,2,3.00", b"1,2,3.00\n", b"4294967295,65535,92233720368547758.07\n",
        b"4294967296,1,1.00\n1,65536,1.00\n1,1,92233720368547758.08\n", b"0000000000000000000000000001,00065535,0.00\n",
        b",,\n,1,1.00\n1,,1.00\n1,1,\n", b"1,2\n1\n1,2,3,\n1,2,3.00,\n,,,\n", b"+1,2,3.00\n1,-2,3.00\n1,2,-3.00\n",
        b"1,2,3.00\r\n1 ,2,3.00\n1,2, 3.00\n", b"1,2,3.0\n1,2,3.000\n1,2,.00\n1,2,3.\n1,2,3..00\n1,2,3.0.0\n1,2,300\n",
        b"99999999999999999999999999,1,1.00\n1,1,999999999999999999999999.99\n1,1,x.00\n1,1,1.0x\n",
        b"\xff,1,1.00\n1,\xc3\xa9,1.00\n", b"7,3,12.50\n7,,1.00\n8,70000,1.00\n9,1,1.5\n10,2,3.00,4\nx\n",
    ]  # fmt: skip
    pieces = [b"", b"0", b"7", b"42", b"4294967295", b"4294967296", b"65535", b"65536", b"00012", b"1.00", b"0.07",
              b"12.5", b"12.345", b".50", b"9.99", b"1a", b" 1", b"x", b"99999999999999999999", b"3.00"]  # fmt: skip
    out = list(fixed)
    for _ in range(12):
        lines = []
        for _ in range(rng.randrange(1, 40)):
            count = rng.choice([1, 2, 3, 3, 3, 3, 4, 5])
            lines.append(b",".join(rng.choice(pieces) for _ in range(count)))
        out.append(b"\n".join(lines) + rng.choice([b"", b"\n"]))
    return out


# pool -----------------------------------------------------------------------------------------------------------


def pool(data: bytes) -> bytes:
    slots: list[list] = []  # [generation, buffer or None]
    out = []

    def live(i: int, g: int) -> bool:
        return i < len(slots) and slots[i][1] is not None and slots[i][0] == g

    for line in lines_of(data):
        op, *a = line.decode().split(" ")
        args = [int(x) for x in a]
        if op == "new":
            free = next((i for i, s in enumerate(slots) if s[1] is None), len(slots))
            if free == len(slots):
                slots.append([0, None])
            slots[free][0] += 1
            slots[free][1] = bytearray(args[0])
            out.append(f"h {free} {slots[free][0]}")
        elif op == "set":
            i, g, off, v = args
            if not live(i, g):
                out.append("stale")
            elif off >= len(slots[i][1]):
                out.append("bounds")
            else:
                slots[i][1][off] = v
                out.append("ok")
        elif op == "sum":
            out.append(str(sum(slots[args[0]][1])) if live(*args) else "stale")
        elif op == "move":
            i, g, j, h = args
            if not live(i, g) or not live(j, h):
                out.append("stale")
            elif (i, g) == (j, h):
                out.append("same")
            else:
                slots[j][1] += slots[i][1]
                slots[i][1] = None
                out.append(f"ok {len(slots[j][1])}")
        elif op == "free":
            if live(*args):
                slots[args[0]][1] = None
                out.append("ok")
            else:
                out.append("stale")
    held = [s[1] for s in slots if s[1] is not None]
    out.append(f"live {len(held)} {sum(map(len, held))}")
    return "".join(line + "\n" for line in out).encode()


def pool_cases(rng: random.Random) -> list[bytes]:
    out = [b"", b"new 0\n", b"sum 0 1\nfree 0 0\nmove 0 1 0 1\n", b"new 1\nmove 0 1 0 1\nfree 0 1\nfree 0 1\n",
           b"new 5\nset 0 1 5 1\nset 0 1 4 255\nset 18446744073709551615 1 0 0\nsum 0 18446744073709551615\n"]  # fmt: skip
    for length in [20, 60, 200, 1000, 3000, 6000]:
        handles: list[tuple[int, int]] = []
        slots: list[int] = []
        lines = []
        for _ in range(length):
            r = rng.random()
            known = handles or [(0, 1)]
            if r < 0.3 or not handles:
                size = rng.choice([0, 1, 2, 3, 64, 4096, rng.randrange(300)])
                lines.append(f"new {size}")
                # Mirror the pool only to aim later commands at plausible handles; the oracle decides the output.
                free = next((i for i, g in enumerate(slots) if g < 0), len(slots))
                if free == len(slots):
                    slots.append(0)
                slots[free] = abs(slots[free]) + 1
                handles.append((free, slots[free]))
            elif r < 0.5:
                i, g = rng.choice(known)
                lines.append(f"set {i} {g + rng.choice([0, 0, 0, 1])} {rng.randrange(80)} {rng.randrange(256)}")
            elif r < 0.62:
                i, g = rng.choice(known)
                lines.append(f"sum {i} {g}")
            elif r < 0.8:
                (i, g), (j, h) = rng.choice(known), rng.choice(known)
                lines.append(f"move {i} {g} {j} {h}")
                if (i, g) != (j, h) and (i, g) in handles and (j, h) in handles and slots[i] == g and slots[j] == h:
                    slots[i] = -g
                    handles.remove((i, g))
            else:
                i, g = rng.choice(known)
                lines.append(f"free {i} {g}")
                if (i, g) in handles and slots[i] == g:
                    slots[i] = -g
                    handles.remove((i, g))
        out.append(("\n".join(lines) + "\n").encode())
    return out


# rle ------------------------------------------------------------------------------------------------------------

FNV_START, FNV_PRIME = 14695981039346656037, 1099511628211


def fnv1a(data: bytes) -> int:
    h = FNV_START
    for b in data:
        h = ((h ^ b) * FNV_PRIME) % U64
    return h


def rle(data: bytes) -> bytes:
    first, _, rest = data.partition(b"\n")
    cap = int(first)
    packed = bytes.fromhex(rest.strip().decode())
    output = bytearray()
    pos = 0
    while pos < len(packed):
        c = packed[pos]
        size = c + 1 if c < 128 else 1
        if pos + 1 + size > len(packed):
            return f"err truncated {pos}\n".encode()
        run = packed[pos + 1 : pos + 1 + size] if c < 128 else packed[pos + 1 : pos + 2] * (c - 126)
        if len(output) + len(run) > cap:
            return f"err full {pos}\n".encode()
        output += run
        pos += 1 + size
    return f"ok {len(output)} {fnv1a(bytes(output)):016x}\n".encode()


def rle_cases(rng: random.Random) -> list[bytes]:
    def packets(count: int) -> bytes:
        out = bytearray()
        for _ in range(count):
            if rng.random() < 0.5:
                c = rng.randrange(128)
                out += bytes([c]) + bytes(rng.randrange(256) for _ in range(c + 1))
            else:
                out += bytes([rng.randrange(128, 256), rng.randrange(256)])
        return bytes(out)

    fixed = [(0, b""), (0, b"\x80\x05"), (2, b"\x80\x05"), (1, b"\x00\x07"), (1, b"\x01\x07\x08"), (5, b"\x00"),
             (5, b"\x80"), (1000, b"\x05\x01\x02"), (0, b"\x7f"), (129, b"\xff\x00"), (128, b"\xff\x00"),
             (1000000, b"\xff\x61" * 7751), (1000000, b"\xff\x61" * 7752)]  # fmt: skip
    cases = [(cap, body) for cap, body in fixed]
    for count in [1, 3, 10, 50, 200, 2000]:
        body = packets(count)
        cases.append((1000000, body))
        cases.append((rng.randrange(0, 200 * count), body))
        cut = body[: rng.randrange(1, len(body) + 1)]
        cases.append((1000000, cut))
    return [f"{cap}\n{body.hex()}\n".encode() for cap, body in cases]


# varint (repair) ------------------------------------------------------------------------------------------------


def varint(data: bytes) -> bytes:
    raw = bytes.fromhex(data.strip().decode())
    out, pos = [], 0
    while pos < len(raw):
        start, value, i = pos, 0, 0
        while True:
            if pos >= len(raw):
                return "".join(out).encode() + f"err truncated {start}\n".encode()
            b = raw[pos]
            if i == 9 and b > 1:
                return "".join(out).encode() + f"err overflow {start}\n".encode()
            value |= (b & 127) << (7 * i)
            pos += 1
            if b < 128:
                break
            i += 1
        out.append(f"{value}\n")
    return "".join(out).encode()


def leb(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 127
        value >>= 7
        out.append(b | (128 if value else 0))
        if not value:
            return bytes(out)


def varint_cases(rng: random.Random) -> list[bytes]:
    edges = [0, 1, 127, 128, 16383, 16384, 2**32, 2**63, 2**64 - 1]
    fixed = [b"", b"ff", b"80", b"0080", b"7f81", b"ffffffffffffffffff01", b"ffffffffffffffffff02", b"ffffffffffffffffff81",
             b"808080808080808080", b"80808080808080808001", b"8080808080808080808001", b"00e5018e0280808080808080808001",
             b"ffffffffffffffffff7f", b"05ff"]  # fmt: skip
    out = list(fixed)
    for count in [1, 5, 40, 300]:
        body = b"".join(
            leb(rng.choice(edges) if rng.random() < 0.4 else rng.randrange(2 ** rng.randrange(1, 65)))
            for _ in range(count)
        )
        out.append(body.hex().encode())
        out.append(body[:-1].hex().encode())
        out.append((body + b"\xff" * rng.randrange(1, 9)).hex().encode())
    return [c + b"\n" for c in out]


# split_sum (repair) ---------------------------------------------------------------------------------------------


def split_sum(data: bytes) -> bytes:
    _, n, *values = ints(data)
    return f"sum {sum(values[:n]) % U64}\n".encode()


def split_sum_cases(rng: random.Random) -> list[bytes]:
    shapes = [
        (1, 0),
        (16, 0),
        (3, 10),
        (2, 10),
        (16, 15),
        (16, 17),
        (5, 3),
        (7, 1000),
        (9, 99999),
        (16, 200000),
        (4, 4),
    ]
    out = []
    for k, n in shapes:
        values = [rng.choice([rng.randrange(1000), U64 - 1, rng.randrange(U64)]) for _ in range(n)]
        if (k, n) == (3, 10):
            values = list(range(1, 11))
        out.append(f"{k} {n}\n" + " ".join(map(str, values)) + "\n")
    return [c.encode() for c in out]


# dedupe (repair) ------------------------------------------------------------------------------------------------


def dedupe(data: bytes) -> bytes:
    n, *values = ints(data)
    runs: list[list[int]] = []
    for v in values[:n]:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    return "".join(f"{v} x{c}\n" for v, c in runs).encode() + f"distinct {len(runs)}\n".encode()


def dedupe_cases(rng: random.Random) -> list[bytes]:
    out = ["0\n", "1\n5\n", "2\n-3 -3\n", f"3\n{I64[0]} {I64[0]} {I64[1]}\n", "6\n-4 -4 0 7 7 7\n", "4\n1 2 3 4\n"]
    for n in [10, 100, 5000, 200000]:
        values = sorted(rng.choice([rng.randrange(-5, 5), rng.randrange(I64[0], I64[1])]) for _ in range(n))
        out.append(f"{n}\n" + " ".join(map(str, values)) + "\n")
    return [c.encode() for c in out]


# basis_points (repair) ------------------------------------------------------------------------------------------


def basis_points(data: bytes) -> bytes:
    m, *nums = ints(data)
    pairs = zip(nums[0 : 2 * m : 2], nums[1 : 2 * m : 2], strict=True)
    return "".join("undefined\n" if t == 0 else f"{u * 10000 // t} bp\n" for u, t in pairs).encode()


def basis_points_cases(rng: random.Random) -> list[bytes]:
    top = 10**15
    fixed = [(1, 3), (2500, 10000), (7, 0), (2 * 10**15, 10**15), (0, 0), (0, 1), (10**18, top), (top * 1000, top),
             (top - 1, top), (1844674407370956, top), (1844674407370955, top), (1844674407370956, 1844674407371), (1000, 1)]  # fmt: skip
    pairs = list(fixed)
    for _ in range(300):
        total = rng.choice([0, 1, rng.randrange(1, 1000), rng.randrange(1, top + 1), top])
        used = rng.randrange(0, min(10**18, 1000 * total) + 1) if total else rng.randrange(10**18)
        pairs.append((used, total))
    chunks = [fixed[:4], [(0, 0)], fixed, pairs]
    return [(f"{len(c)}\n" + "".join(f"{u} {t}\n" for u, t in c)).encode() for c in chunks] + [b"0\n"]


# csv_field (repair) ---------------------------------------------------------------------------------------------


def csv_field(data: bytes) -> bytes:
    first, newline, rest = data.partition(b"\n")
    k = int(first)
    out = []
    for row in lines_of(rest) if newline else []:
        fields = row.split(b",")
        out.append(fields[k] if k < len(fields) else b"none")
    return b"".join(f + b"\n" for f in out)


def csv_field_cases(rng: random.Random) -> list[bytes]:
    fixed = [b"0\n", b"0\na", b"0\na\n", b"1\na,b", b"2\na,b,c", b"2\na,b,", b"1\n,\n,", b"3\na,b,c", b"0\n\n\n",
             b"1\na,b,c\nd\n,e,\nx,yz\n", b"1\na,b,c\nd\n,e,\nx,yz", b"5\n,,,,,\n,,,,,x", b"0\nlast"]  # fmt: skip
    out = list(fixed)
    words = [b"", b"a", b"bc", b"def", b"12", b"x y"]
    for rows in [3, 20, 200]:
        k = rng.randrange(4)
        body = b"\n".join(b",".join(rng.choice(words) for _ in range(rng.randrange(1, 6))) for _ in range(rows))
        out.append(f"{k}\n".encode() + body + b"\n")
        out.append(f"{k}\n".encode() + body + b"," + rng.choice(words[1:]))
    return out


# What each SPEC.md promises about its input, so every hidden case can be held to the rules the subject was given.


def counted(data: bytes, head: int, low: int, high: int, limits: list[tuple[int, int]], count: int = -1) -> bool:
    """`head` leading numbers within `limits`, one of them (the last unless `count` says) the number of values after
    them, each value in [low, high]."""
    t = ints(data)
    if len(t) < head or any(not a <= v <= b for v, (a, b) in zip(t, limits, strict=False)):
        return False
    return len(t) == head + t[:head][count] and all(low <= v <= high for v in t[head:])


def valid_pool(data: bytes) -> bool:
    arity = {"new": 1, "set": 4, "sum": 2, "move": 4, "free": 2}
    for line in lines_of(data):
        op, *args = line.decode().split(" ")
        if arity.get(op) != len(args) or not all(a.isdigit() and int(a) < U64 for a in args):
            return False
        if (op == "new" and int(args[0]) > 4096) or (op == "set" and int(args[3]) > 255):
            return False
    return True


def valid_hex(line: bytes) -> bool:
    return len(line) % 2 == 0 and all(c in b"0123456789abcdef" for c in line)


def valid_basis_points(data: bytes) -> bool:
    m, *nums = ints(data)
    pairs = list(zip(nums[0::2], nums[1::2], strict=False))
    ok = all(0 <= t <= 10**15 and 0 <= u <= 10**18 and (t == 0 or u <= 1000 * t) for u, t in pairs)
    return ok and 0 <= m <= 100000 and len(nums) == 2 * m


def valid_rle(data: bytes) -> bool:
    lines = data.split(b"\n")
    return len(lines) == 3 and lines[2] == b"" and 0 <= int(lines[0]) <= 10**6 and valid_hex(lines[1])


def valid_dedupe(data: bytes) -> bool:
    t = ints(data)
    return counted(data, 1, *I64, [(0, 200000)]) and t[1:] == sorted(t[1:])


VALID: dict[str, Callable[[bytes], bool]] = {
    "histogram": lambda d: counted(d, 2, 0, 2**32 - 1, [(0, 2000000), (0, 24)], count=0),
    "chunk_sums": lambda d: counted(d, 2, *I64, [(1, 16), (0, 200000)]),
    "records": lambda d: True,
    "pool": valid_pool,
    "rle": valid_rle,
    "varint": lambda d: d.endswith(b"\n") and valid_hex(d[:-1]),
    "split_sum": lambda d: counted(d, 2, 0, U64 - 1, [(1, 16), (0, 200000)]),
    "dedupe": valid_dedupe,
    "basis_points": valid_basis_points,
    "csv_field": lambda d: 0 <= int(d.partition(b"\n")[0]) <= 100,
}

TASKS = [
    Task("histogram", "implement", True, histogram, histogram_cases),
    Task("chunk_sums", "implement", True, chunk_sums, chunk_sums_cases),
    Task("records", "implement", False, records, records_cases),
    Task("pool", "implement", False, pool, pool_cases),
    Task("rle", "implement", False, rle, rle_cases),
    Task("varint", "repair", False, varint, varint_cases),
    Task("split_sum", "repair", True, split_sum, split_sum_cases),
    Task("dedupe", "repair", False, dedupe, dedupe_cases),
    Task("basis_points", "repair", False, basis_points, basis_points_cases),
    Task("csv_field", "repair", False, csv_field, csv_field_cases),
]
BY_NAME = {t.name: t for t in TASKS}


def hidden_cases(task: Task) -> list[bytes]:
    """The hidden cases of `task`, the same on every run: its own generator under a fixed seed, plus its example."""
    example_in, _ = example(task)
    return [example_in, *task.cases(random.Random(f"{SEED}:{task.name}"))]


def example(task: Task) -> tuple[bytes, bytes]:
    """The input and output of the example in SPEC.md, as the fenced blocks after `## Example` give them."""
    section = task.spec.split("## Example", 1)[1]
    blocks = section.split("```")
    return blocks[1].lstrip("\n").encode(), blocks[3].lstrip("\n").encode()
