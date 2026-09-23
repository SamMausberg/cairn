"""`layout NAME = ...;`: storage layouts and spreads the checker evaluates, and what it answers from them.

The algebra is held element by element to an oracle written here from the definitions in docs/memory.md. The
transpose through a tile is built with row-major, padded and swizzled tiles, and run under both compilers with the
address and undefined sanitizers, where each must equal the transpose the program also computes the plain way.
"""

import json
import signal

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler import layouts as L
from cairn.compiler.cairnc import compile_program, compile_source
from emitted import refused, run, sanitized, watched

TILES = {"rows": "rows(32, 32)", "padded": "pad(rows(32, 32), 1)", "swizzled": "swizzle(rows(32, 32), 5, 0, 5)"}


def transpose(tile: str, threads: str = "8, 32, 1, 1") -> str:
    """One logical operation, a 32 x 32 transpose through a tile, whatever the tile's storage."""
    return f"""layout TILE = {tile};
layout LOAD = spread(TILE, {threads});
layout STORE = spread(transpose(TILE), {threads});
const CELLS:usize = TILE.cosize();

fn through(out:rw<f32>[1024], x:ro<f32>[1024]) {{
  buffer s:f32[CELLS] = zeroed;
  for t in 0..LOAD.participants() {{
    for v in 0..LOAD.values() {{ s[LOAD.at(t, v)] = x[LOAD.row(t, v) * 32 + LOAD.col(t, v)]; }}
  }}
  for t in 0..STORE.participants() {{
    for v in 0..STORE.values() {{ out[STORE.row(t, v) * 32 + STORE.col(t, v)] = s[STORE.at(t, v)]; }}
  }}
}}

fn main() -> i32 {{
  buffer x:f32[1024] = zeroed;
  buffer y:f32[1024] = zeroed;
  for i in 0..1024 {{ x[i] = f32(i) * 0.5; }}
  through(y, x);
  for r in 0..32 {{ for c in 0..32 {{ if y[r * 32 + c] != x[c * 32 + r] {{ return 1; }} }} }}
  return 0;
}}
"""


def value(source: str, name: str):
    _, checker, _ = compile_program(source)
    return L.value(checker, name)


# The oracle: each layout's offset and each spread's coordinates, straight from the definitions.


def swizzle(o: int, bits: int, base: int, shift: int) -> int:
    flipped = (o >> (base + shift)) & ((1 << bits) - 1)
    return o ^ (flipped << base)


ORACLES = {
    "rows(8, 16)": lambda r, c: r * 16 + c,
    "cols(8, 16)": lambda r, c: r + c * 8,
    "strided(8, 16, 20, 1)": lambda r, c: r * 20 + c,
    "pad(rows(8, 16), 4)": lambda r, c: r * 20 + c,
    "pad(cols(8, 16), 3)": lambda r, c: r + c * 11,
    "transpose(rows(16, 8))": lambda r, c: c * 8 + r,
    "swizzle(rows(8, 64), 3, 3, 3)": lambda r, c: swizzle(r * 64 + c, 3, 3, 3),
    "swizzle(pad(rows(8, 16), 16), 2, 2, 4)": lambda r, c: swizzle(r * 32 + c, 2, 2, 4),
}


@pytest.mark.parametrize("written", ORACLES)
def test_every_element_of_a_storage_layout_is_where_its_definition_puts_it(written):
    v = value(f"layout T = {written};", "T")
    for r in range(8):
        for c in range(16):
            assert v.offset((r, c)) == ORACLES[written](r, c), (written, r, c)


def test_a_tiled_layout_names_each_tile_and_each_element_inside_it():
    v = value("layout T = tile(pad(rows(64, 64), 8), 16, 32);", "T")
    assert v.shape == (4, 2, 16, 32)
    for i, j, r, c in [(0, 0, 0, 0), (3, 1, 15, 31), (2, 0, 5, 7), (1, 1, 0, 9)]:
        assert v.offset((i, j, r, c)) == (i * 16 + r) * 72 + j * 32 + c


def spread_oracle(rows: int, cols: int, tr: int, tc: int, vr: int, vc: int) -> dict:
    """(participant, value) -> (row, column): participant tr*TC + tc holds a VR x VC block; the block of blocks
    repeats down and across, and a participant's values run across its block, then down, then over repeats."""
    reps_r, reps_c = max(1, rows // (tr * vr)), max(1, cols // (tc * vc))
    out = {}
    for t in range(tr * tc):
        for pr in range(reps_r):
            for pc in range(reps_c):
                for i in range(vr):
                    for j in range(vc):
                        v = ((pr * reps_c + pc) * vr + i) * vc + j
                        r, c = pr * tr * vr + (t // tc) * vr + i, pc * tc * vc + (t % tc) * vc + j
                        out[t, v] = (r % rows, c % cols)
    return out


@pytest.mark.parametrize("shape", [(32, 32, 8, 32, 1, 1), (64, 32, 32, 4, 1, 8), (64, 64, 2, 2, 2, 2),
                                   (16, 8, 4, 2, 2, 2), (32, 32, 32, 8, 1, 4)])  # fmt: skip
def test_a_spread_gives_each_participant_the_elements_its_definition_does(shape):
    rows, cols, *rest = shape
    v = value(f"layout D = spread(rows({rows}, {cols}), {', '.join(map(str, rest))});", "D")
    expected = spread_oracle(rows, cols, *rest)
    assert {(t, x): v.coords(t, x) for t in range(v.count) for x in range(v.each)} == expected
    for (t, x), at in list(expected.items())[:: max(1, len(expected) // 64)]:
        assert L.owner(v, at) == [(t, x)]


def test_the_tiles_differ_only_in_how_a_warp_meets_the_banks():
    """Reading a column of a 32 x 32 f32 tile: all 32 lanes on one bank row-major, one each padded or swizzled."""
    ways = {}
    for name, tile in TILES.items():
        store = value(transpose(tile), "STORE")
        table = [[store.tile.offset(store.coords(t, x)) for x in range(store.each)] for t in range(32)]
        banks = [len({o % 32 for o in column}) for column in zip(*table, strict=True)]
        ways[name] = 32 // min(banks)
        assert L.conflicts(store, 4) == ways[name]
        assert L.conflicts(value(transpose(tile), "LOAD"), 4) == 1
    assert ways == {"rows": 32, "padded": 1, "swizzled": 1}


def test_a_copy_in_sixteen_byte_runs_is_seen_where_the_tile_keeps_rows_whole():
    runs = {}
    for tile in ["rows(64, 32)", "pad(rows(64, 32), 8)", "swizzle(rows(64, 32), 2, 3, 3)", "cols(64, 32)"]:
        runs[tile] = L.runs(value(f"layout D = spread({tile}, 32, 4, 1, 8);", "D"), 2)
    assert runs == {"rows(64, 32)": 8, "pad(rows(64, 32), 8)": 8, "swizzle(rows(64, 32), 2, 3, 3)": 8,
                    "cols(64, 32)": 1}  # fmt: skip


def test_what_a_change_of_spread_needs():
    source = """layout T = rows(32, 32);
layout A = spread(T, 8, 32, 1, 1);
layout B = spread(T, 8, 32, 1, 1);
layout BLOCKED = spread(T, 8, 32, 4, 1);
layout WIDE = spread(T, 32, 8, 1, 4);
"""
    held = {n: value(source, n) for n in ["A", "B", "BLOCKED", "WIDE"]}
    assert L.conversion(held["A"], held["B"]) == "none"
    assert L.conversion(held["A"], held["BLOCKED"]) == "shared"  # rows move between warps
    assert L.conversion(held["A"], held["WIDE"]) == "shared"
    lanes = """layout T = rows(4, 32);
layout ROWS = spread(T, 1, 32, 4, 1);
layout PAIRS = spread(T, 2, 16, 2, 2);
layout ACROSS = spread(rows(2, 64), 1, 32, 1, 2);
layout DOWN = transpose(spread(rows(64, 2), 32, 1, 2, 2));
"""
    rows, pairs, across, down = (value(lanes, n) for n in ["ROWS", "PAIRS", "ACROSS", "DOWN"])
    assert L.conversion(rows, pairs) == "shuffle"  # 32 participants: one warp
    assert L.conversion(across, down) == "registers"  # the same four elements each, in another order


def test_a_consumer_that_takes_a_pointer_and_a_leading_dimension():
    assert L.affine(value("layout T = pad(rows(64, 32), 8);", "T")) == ("row", 40)
    assert L.affine(value("layout T = cols(16, 16);", "T")) == ("col", 16)
    assert L.affine(value("layout T = swizzle(rows(64, 32), 2, 3, 3);", "T")) is None
    assert L.rows16(value("layout T = swizzle(rows(64, 32), 2, 3, 3);", "T"), 2)
    assert not L.rows16(value("layout T = pad(rows(64, 32), 4);", "T"), 2)  # rows start 8 bytes apart


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-LAYOUT-GAP", "layout T = rows(32, 32);\nlayout D = spread(T, 7, 32, 1, 1);", "(28, 0)"),
        ("E-LAYOUT-OVERLAP", "layout D = spread(rows(4, 4), 2, 4, 2, 2);", "participant 0"),
        ("E-LAYOUT-OVERLAP", "layout T = swizzle(rows(32, 32), 3, 0, 0);", "share offset"),  # a shift of 0 clears
        ("E-LAYOUT-OVERLAP", "layout T = strided(32, 32, 16, 1);", "share offset"),
        ("E-LAYOUT-OVERLAP", "layout D = spread(strided(8, 8, 4, 1), 2, 2, 4, 4);", "spread's tile"),
        ("E-LAYOUT-CONSUMER", "layout T = rows(32, 32);\nlayout W = rows(64, 32);\nlayout D = spread(T, 8, 32, 1, 1);\n"
         "fn f(t:usize, v:usize) -> usize = W.at(D.row(t, v), D.col(t, v));", "64 x 32"),
        ("E-LAYOUT", "layout T = tile(rows(30, 32), 16, 16);", "does not divide"),
        ("E-LAYOUT", "layout T = rows(0, 4);", "at least 1"),
        ("E-LAYOUT", "layout T = pad(swizzle(rows(8, 8), 1, 0, 3), 1);", "before any swizzle"),
        ("E-LAYOUT", "layout A = transpose(B);\nlayout B = transpose(A);", "itself"),
        ("E-LAYOUT", "layout T = rows(1024, 1024);", "at most 262144"),
        ("E-LAYOUT", "layout T = spread(spread(rows(8, 8), 2, 2, 4, 4), 1, 1, 1, 1);", "storage layout"),
        ("E-LAYOUT", "layout T = rows(4, 4);\nfn f() -> usize = T.values();", "counts a spread's"),
        ("E-LAYOUT", "fn f(x:usize) -> usize { return x; }\nlayout T = f(4);", "one of rows"),
        ("E-ARITY", "layout T = rows(4, 4);\nfn f(r:usize) -> usize = T.at(r);", "2 coordinates"),
        ("E-ARITY", "layout T = rows(4);", "takes 2"),
        ("E-CALLEE", "layout T = rows(4, 4);\nfn f(r:usize) -> usize = T.find(r, r);", "none of them"),
        ("E-TYPE-MISMATCH", "layout T = rows(4, 4);\nfn f(r:u32) -> usize = T.at(r, 0);", "usize"),
    ],
)  # fmt: skip
def test_rejections(code, source, said):
    assert said in refused(code, source)["message"]


def test_a_local_of_the_same_name_hides_a_layout():
    compile_source("layout T = rows(4, 4);\nstruct P { at:usize; }\nfn f(T:P) -> usize = T.at;")


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
@pytest.mark.parametrize("tile", TILES)
def test_one_transpose_through_three_tiles_equals_the_plain_one(tmp_path, cxx, tile):
    done = run(tmp_path, compile_source(transpose(TILES[tile]))[0], *sanitized(cxx), cxx=cxx)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_the_transpose_is_clean_under_the_address_and_undefined_sanitizers_as_the_project_builds_it(tmp_path):
    done = watched(tmp_path, compile_source(transpose(TILES["swizzled"]))[0], "clang++", "address,undefined")
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stderr[-2000:]


@pytest.mark.parametrize("call", ["T.at(n, 0)", "T.at(0, n)", "D.row(n * 4, 0)", "D.col(0, n)", "D.at(3, n + 1)"])
def test_a_coordinate_or_participant_outside_its_layout_traps(tmp_path, call):
    source = f"""layout T = swizzle(rows(4, 4), 1, 0, 2);
layout D = spread(T, 2, 2, 2, 2);
fn at(n:usize) -> usize = {call};
fn main() -> i32 {{ let x = at(4); return i32(x); }}
"""
    done = run(tmp_path, compile_source(source)[0], "-std=c++20", "-O1")
    assert done.returncode == -signal.SIGABRT, (call, done.returncode, done.stderr)


def test_the_receipt_and_explain_answer_from_the_layout():
    receipt = compile_source(transpose(TILES["rows"]))[1]
    store = receipt["layouts"]["STORE"]
    assert store["covers"] == "exactly-once" and store["participants"] == 256 and store["values"] == 4
    assert store["bank_ways"]["4"] == 32 and store["tile"]["affine"] == "col-major, leading dimension 32"
    assert receipt["layouts"]["TILE"]["cosize"] == 1024
    assert compile_source(transpose(TILES["padded"]))[1]["layouts"]["STORE"]["bank_ways"]["4"] == 1
    assert "layouts" not in compile_source("fn main() -> i32 { return 0; }")[1]


def test_explain_says_which_participant_holds_each_element(tmp_path):
    from cairn.agent.explain import explain

    report = explain(transpose(TILES["padded"]), cxx="g++")
    grid = report["layouts"]["LOAD"]["owners"]
    assert grid[0][:3] == [0, 1, 2] and grid[8][0] == 0 and grid[31][31] == 255  # row r is held by threads r % 8
    assert report["functions"]["through"]["guards"]["sites"]["layout"] == 6
    json.dumps(report)


def test_the_canonical_projection_keeps_every_layout_and_lowers_to_the_same_code():
    source = transpose(TILES["swizzled"]) + "pub layout WIDE = tile(rows(64, 64), 16, 16);\n"
    projected = canonical_source(source)
    assert "layout TILE = swizzle(rows(32, 32), 5, 0, 5);" in projected
    assert compile_source(projected)[0] == compile_source(source)[0]


def test_a_layout_count_is_a_constant_wherever_a_constant_may_stand():
    source = """layout T = pad(rows(16, 16), 2);
const N:usize = T.cosize();
const W:usize = T.extent(1);
fn main() -> i32 {
  stack cells:u32[N] = zeroed;
  let tiles = Array[u8, W]();
  if len(cells) != 16 * 18 - 2 || len(tiles) != 16 || T.size() != 256 { return 1; }
  return 0;
}
"""
    assert run_ok(source)


def run_ok(source: str) -> bool:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as scratch:
        return run(Path(scratch), compile_source(source)[0], "-std=c++20").returncode == 0


def test_a_vector_plan_and_a_stage_plan_ask_the_layout_what_they_used_to_compute():
    """chunks.py and staging.py take these answers from layouts.py; they are the arithmetic the plans always used,
    at every width, element size, radius and offset a plan admits, so no plan is accepted or refused anew."""
    for size in (1, 2, 4, 8):
        for width in (2, 4, 8, 16):
            assert (L.moved(width, size) == width) == (width * size <= 16), (width, size)
        assert L.moved(16, size) == 16 // size
    for radius in (1, 2, 3, 8, 31, 32):
        for low in range(-34, 35, 3):
            for high in range(low, 35, 6):
                offsets = {low, high, (low + high) // 2}
                assert L.halo(offsets, radius) == (max(abs(d) for d in offsets) <= radius), (offsets, radius)


def test_a_rule_that_runs_the_body_with_numbers_gets_the_layout_s_answer():
    """`layouts.apply` is what compiler/phases.py asks of `L.at(...)` and `D.row(t, v)` when it runs a cooperative
    body thread by thread: the offset or coordinate, IndexError where the program traps, None for a symbol."""
    from cairn.compiler.tree import Expr

    source = transpose(TILES["swizzled"])
    _, checker, _ = compile_program(source)
    tile, load = L.value(checker, "TILE"), L.value(checker, "LOAD")

    def call(layout, name):
        return Expr("call", f"{layout}.{name}", ref=("layout", layout, name))

    assert L.apply(checker, call("TILE", "at"), (3, 5)) == tile.offset((3, 5))
    assert L.apply(checker, call("LOAD", "row"), (40, 2)) == load.coords(40, 2)[0]
    assert L.apply(checker, call("LOAD", "at"), (40, 2)) == tile.offset(load.coords(40, 2))
    assert L.apply(checker, call("TILE", "at"), (3, "t")) is None
    with pytest.raises(IndexError):
        L.apply(checker, call("LOAD", "col"), (256, 0))


def test_a_transpose_through_a_tile_moves_every_element_between_warps():
    """LOAD and STORE are one tile read two ways: an element is its offset, and a transpose sends it to another warp."""
    for tile in TILES.values():
        _, checker, _ = compile_program(transpose(tile))
        load, store = L.value(checker, "LOAD"), L.value(checker, "STORE")
        assert L.shares(load, store) and L.conversion(load, store) == "shared"
        assert L.conversion(load, load) == "none"
