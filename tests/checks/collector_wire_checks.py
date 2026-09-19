#!/usr/bin/env python3
import ctypes as C
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_checks import SZ, U8, U64, array, load

ROOT = Path(__file__).resolve().parents[2]
l = load()
cases = 0
# Every selection mask up to length 12. Unwritten output tail must remain intact.
for n in range(13):
    for mask in range(1 << n):
        vals = [2 * i + (0 if mask & (1 << i) else 1) for i in range(n)]
        a = array(U64, vals)
        b = array(U64, [0xDEADBEEF] * n)
        k = l.cf_compact_even(n, b, a)
        exp = [v for v in vals if v % 2 == 0]
        assert k == len(exp) and list(b) == exp + [0xDEADBEEF] * (n - k)
        cases += 1


class Packet(C.Structure):
    _fields_ = [
        ("sequence", C.c_uint64),
        ("source", C.c_uint32),
        ("destination", C.c_uint32),
        ("flags", C.c_uint16),
        ("kind", C.c_uint8),
        ("version", C.c_uint8),
        ("payload_bytes", C.c_uint64),
        ("timestamp", C.c_uint64),
    ]


wire = C.CDLL(str(ROOT / "results/libwire.so"))
wire.cf_wire_size_Packet.restype = SZ
wire.cf_encode_Packet.argtypes = [C.POINTER(U8), Packet]
wire.cf_encode_Packet.restype = None
wire.cf_decode_Packet.argtypes = [C.POINTER(U8)]
wire.cf_decode_Packet.restype = Packet
size = struct.calcsize("<QIIHBBQQ")
assert wire.cf_wire_size_Packet() == size
r = random.Random(17092026)
fields = [(n, C.sizeof(t) * 8) for n, t in Packet._fields_]
for i in range(1000):
    vals = [r.getrandbits(w) for _, w in fields]
    out = array(U8, [0] * size)
    p = Packet(*vals)
    wire.cf_encode_Packet(out, p)
    expected = struct.pack("<QIIHBBQQ", *vals)
    assert bytes(out) == expected
    q = wire.cf_decode_Packet(out)
    assert [getattr(q, n) for n, _ in fields] == vals
    independent = bytes(r.randrange(256) for _ in range(size))
    q = wire.cf_decode_Packet(array(U8, independent))
    assert tuple(getattr(q, n) for n, _ in fields) == struct.unpack("<QIIHBBQQ", independent)
print(
    json.dumps(
        {
            "exhaustive_collector_masks": cases,
            "collector_max_length": 12,
            "wire_roundtrip_and_independent_decode_cases": 1000,
            "wire_bytes": size,
            "native_record_bytes": C.sizeof(Packet),
            "all_passed": True,
            "formal_status": "finite-tests-only",
        },
        indent=2,
    )
)
