"""The table in docs/devices.md, "What fast kernels use": how a CAIRN program writes each feature fast CUDA kernels
use. Every row names spellings, and this file compiles each one and requires what the row's last column says,
accepted or refused with that code. A row changes here and in the table together, or neither test passes.
"""

import re
from pathlib import Path

import pytest

from cairn.compiler.cairnc import Diagnostic, compile_source

ROOT = Path(__file__).resolve().parents[2]

# Each row of the table, by its first cell: the spellings it names, each with what compiling it gives.
ROWS: dict[str, list[tuple[str, str]]] = {
    "16-byte loads and stores with a cache hint": [
        (
            """fn f(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = 2.0 * x[i]; } }
plan f { vector 4; }""",
            "accepted",
        ),
        (
            """fn f(g:usize, n:usize, x:ro<f32>[n]@device, out:rw<f32>[g]@device) {
  blocks b in g threads t in 32 {
    let mut s:f32 = 0.0;
    unsafe {
      asm ptx sm_70 "ld.global.cs.v4.f32 {%0, %1, %2, %3}, [%4];" (out a:f32, out c:f32, out d:f32, out e:f32, x)
        effects(read:x);
      s = a + c + d + e;
    }
    let total = reduce + warp yield s;
    if t == 0 { out[b] = total; }
  }
}""",
            "accepted",
        ),
        (
            """fn f(g:usize, n:usize, x:ro<f32>[n]@device, out:rw<f32>[g]@device) {
  blocks b in g threads t in 32 {
    let v = load_wide[4](x, 4 * (b * 32 + t), Cache.streaming);
    let total = reduce + warp yield v[0] + v[1] + v[2] + v[3];
    if t == 0 { out[b] = total; }
  }
}""",
            "accepted",
        ),
        ("""fn f(n:usize, x:ro<f64>[n]@device) -> f64 { let v = load_wide[4](x, 0); return v[0]; }""", "E-WIDE"),
    ],
    "Atomics on device memory": [
        (
            """fn f(n:usize, out:rw<u64>[n]@device, total:ro<Atomic[u64]>) {
  parallel i in n { out[i] = 1; let v = total.fetch_add(1, Order.relaxed); }
}""",
            "E-PLACEMENT",
        ),
        (
            """fn f(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) {
  parallel i in n { let v = x[i]; unsafe { asm ptx sm_70 "red.global.add.u32 [%0], %1;" (out, v) effects(write:out); } }
}""",
            "E-PARALLEL-RACE",
        ),
    ],
    "Atomics on shared memory": [
        (
            """fn f(g:usize, n:usize, x:ro<u32>[n]@device) {
  blocks b in g threads t in 32 {
    shared h:u32[32] = zeroed;
    let v = x[0];
    unsafe { asm ptx sm_70 "red.shared.add.u32 [%0], %1;" (h, v) effects(write:h); }
  }
}""",
            "E-COOP-CONFLICT",
        )
    ],
    "A last block that finishes, grid-wide sync": [
        (
            """fn f(g:usize, partial:rw<u64>[g]@device, out:rw<u64>[1]@device) {
  blocks b in g threads t in 32 { if t == 0 { partial[b] = u64(b); } }
  blocks b in 1 threads t in 32 {
    if t == 0 {
      let mut sum:u64 = 0;
      for k in 0..g { sum = add_wrap(sum, partial[k]); }
      out[0] = sum;
    }
  }
}""",
            "accepted",
        )
    ],
    "Shared memory nobody zeroes": [
        ("""fn f(g:usize) { blocks b in g threads t in 32 { shared s:u32[32]; s[t] = 1; } }""", "E-PARSE")
    ],
    "Warp vote and ballot": [
        (
            """fn f(g:usize, n:usize, x:ro<u32>[n], out:rw<u32>[n]) {
  blocks b in g threads t in 32 {
    let i = b * 32 + t;
    let mut bit:u32 = 0;
    if i < n && x[i] > 0 { bit = shl_wrap(1, t); }
    let ballot = reduce | warp yield bit;
    if i < n { out[i] = ballot; }
  }
}""",
            "accepted",
        )
    ],
    "Warp match": [("""fn f(g:usize) { blocks b in g threads t in 32 { let m = match_any(t); } }""", "E-CALLEE")],
    "Shuffles": [
        (
            """fn f(g:usize) {
  blocks b in g threads t in 32 { let a = shuffle(t, 0); let c = shuffle_xor(t, 1); let d = shuffle_down(t, 1); }
}""",
            "accepted",
        ),
        ("""fn f(g:usize) { blocks b in g threads t in 32 { let v = shuffle_up(t, 1); } }""", "E-CALLEE"),
    ],
    "Grid-stride loops": [
        (
            """fn f(g:usize, n:usize, x:ro<u64>[n]@device, out:rw<u64>[g]@device) {
  blocks b in g threads t in 32 {
    let mut sum:u64 = 0;
    let mut i = b * 32 + t;
    while i < n {
      sum = add_wrap(sum, x[i]);
      i += g * 32;
    }
    let total = reduce add_wrap warp yield sum;
    if t == 0 { out[b] = total; }
  }
}""",
            "accepted",
        )
    ],
    "Dynamic shared memory, more than 48 KiB": [
        ("""fn f(g:usize, k:usize) { blocks b in g threads t in 32 { shared s:u32[k] = zeroed; } }""", "E-COOP-SHARED"),
        ("""fn f(g:usize) { blocks b in g threads t in 32 { shared s:u32[20000] = zeroed; } }""", "E-COOP-SHARED"),
    ],
    "Launch bounds": [
        (
            """fn f(g:usize, out:rw<u32>[g]@device) { blocks b in g threads t in 128 { if t == 0 { out[b] = 1; } } }""",
            "accepted",
        )
    ],
    "Unrolling": [
        (
            """fn f(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = 2.0 * x[i]; } }
plan f { unroll 4; block 128; }""",
            "accepted",
        )
    ],
    "Packed half and bf16 math": [
        ("""fn f(a:f16, b:f16) -> f16 = a * b;""", "E-OPERATOR"),
        (
            """fn f(n:usize, a:ro<u32>[n]@device, out:rw<u32>[n]@device) {
  parallel i in n { let x = a[i]; unsafe { asm ptx sm_53 "fma.rn.f16x2 %0, %1, %2, %3;" (out r:u32, x, x, x); out[i] = r; } }
}""",
            "accepted",
        ),
    ],
    "Fast approximate math": [
        (
            """import std.math as math;
fn f(n:usize, a:ro<f64>[n]@device, out:rw<f64>[n]@device) { parallel i in n { out[i] = math.exp(a[i]); } }""",
            "E-PARALLEL-CALL",
        ),
        (
            """fn f(n:usize, a:ro<f32>[n]@device, out:rw<f32>[n]@device) {
  parallel i in n { let x = a[i]; unsafe { asm ptx sm_70 "ex2.approx.ftz.f32 %0, %1;" (out r:f32, x); out[i] = r; } }
}""",
            "accepted",
        ),
    ],
    "No-alias knowledge": [
        (
            """fn f(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = 2.0 * x[i]; } }""",
            "accepted",
        )
    ],
    "Asynchronous copies": [
        (
            """fn f(g:usize, n:usize, x:ro<u32>[n]@device, out:rw<u32>[g]@device) {
  blocks b in g threads t in 32 {
    pipeline p:u32[32] depth 2;
    p.fill(x, 0, min(32, n));
    p.wait();
    let v = p[t];
    p.release();
    if t == 0 { out[b] = v; }
  }
}""",
            "accepted",
        ),
        ("""fn f(g:usize) { blocks b in g threads t in 32 { pipeline p:f16[32] depth 2; } }""", "E-COOP-SHARED"),
    ],
    "`ldmatrix`, `mma.sync`": [
        (
            """layout TILE = rows(16, 16);
fn tile(a:ro<f16>[256]@device) {
  blocks g in 1 threads t in 32 {
    shared s:f16[256] = zeroed;
    let x = mma_load[MmaA[f16, 16, 8, 16]](s, TILE, 0, 0);
  }
}""",
            "accepted",
        )
    ],
    "`wgmma`, TMA, clusters, distributed shared memory": [
        ("""fn f() { let acc = TmemAcc[f32, 128, 256, 16](0.0); }""", "E-TARGET-FEATURE")
    ],
    "Block-wide cooperative groups": [
        (
            """fn f(g:usize, out:rw<u32>[g]) {
  blocks b in g threads t in 64 {
    shared s:u32[64] = zeroed;
    s[t] = u32(t);
    barrier;
    let w = reduce add_wrap warp yield s[63 - t];
    if t == 0 { out[b] = w; }
  }
}""",
            "accepted",
        )
    ],
    "Memory fences, `__nanosleep`": [
        (
            """fn f(n:usize, out:rw<u32>[n]@device) {
  parallel i in n {
    let d:u32 = 100;
    unsafe {
      asm ptx sm_70 "fence.acq_rel.gpu;" () effects(fence);
      asm ptx sm_70 "nanosleep.u32 %0;" (d);
    }
    out[i] = 1;
  }
}""",
            "accepted",
        )
    ],
    "Persistent kernels": [
        (
            """fn f(sms:usize, rows:usize, cols:usize, n:usize, x:ro<u64>[n]@device, out:rw<u64>[rows]@device) {
  blocks b in sms threads t in 32 {
    let mut r = b;
    while r < rows {
      let mut sum:u64 = 0;
      for c in 0..cols { if r * cols + c < n && c % 32 == t { sum = add_wrap(sum, x[r * cols + c]); } }
      let total = reduce add_wrap warp yield sum;
      r += sms;
    }
  }
}""",
            "accepted",
        ),
        (
            """fn f(g:usize, n:usize, out:rw<u32>[n]@device, next:ro<Atomic[u64]>) {
  blocks b in g threads t in 32 { let k = next.fetch_add(1, Order.relaxed); if t == 0 { out[b] = 1; } }
}""",
            "E-PLACEMENT",
        ),
    ],
    "Streams": [
        (
            """fn f(n:usize, host:ro<f32>[n], x:rw<f32>[n]@device, out:rw<f32>[n]@device) {
  let up = spawn transfer(x, host);
  let work = spawn parallel i in n after up { out[i] = 2.0 * x[i]; };
  wait(up);
  wait(work);
}""",
            "accepted",
        ),
        (
            """fn f(g:usize, out:rw<u32>[g]@device) {
  let t = spawn blocks b in g threads u in 32 { if u == 0 { out[b] = 1; } };
  wait(t);
}""",
            "E-PARSE",
        ),
    ],
    "CUDA graphs": [],
}


def outcome(source: str) -> str:
    try:
        compile_source(source)
    except Diagnostic as refused:
        return refused.data["code"]
    return "accepted"


def table() -> dict[str, list[str]]:
    """The rows of the table in docs/devices.md: each first cell, and what its last cell says a check gives."""
    text = (ROOT / "docs/devices.md").read_text(encoding="utf-8")
    section = text.split("## What fast kernels use", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) == 4 and cells[0] not in {"Feature", "---"}:
            rows[cells[0]] = re.findall(r"accepted|E-[A-Z0-9-]+", cells[3])
    return rows


def test_the_table_and_this_file_name_the_same_rows_and_results():
    written = table()
    assert list(written) == list(ROWS)
    for row, checks in ROWS.items():
        assert sorted(set(written[row])) == sorted({want for _, want in checks}), row


@pytest.mark.parametrize(("row", "source", "want"), [(r, s, w) for r, checks in ROWS.items() for s, w in checks])
def test_each_spelling_compiles_as_its_row_says(row, source, want):
    assert outcome(source) == want, row


def test_the_lowering_tells_nvcc_nothing_of_what_an_ro_view_promises():
    """The No-alias row: the checker knows x is not written while f runs, and the emitted lane says nothing of it."""
    cpp = compile_source(ROWS["No-alias knowledge"][0][0])[0]
    assert "__restrict__" not in cpp and "__ldg" not in cpp


def test_a_cooperative_kernel_states_its_block_as_its_launch_bounds():
    from cairn.compiler.cairnc import RUNTIME_FILES

    assert "__launch_bounds__(THREADS)" in RUNTIME_FILES["cairn_coop.hpp"]
