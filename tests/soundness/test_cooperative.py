"""Cooperative regions, `blocks b in G threads t in T { }`: blocks of threads that share arrays and meet at barriers.

Every rule has a rejection naming its code. Accepted regions run on the host as real threads meeting at a
std::barrier, under ThreadSanitizer with both compilers, against a plain reference written in C++; the same programs
with the barrier taken out of the emitted C++ must make the sanitizer report a race, so the oracle is shown to bite.
The device lowering compiles for sm_120 and is inspected, never run here; its lambdas also run on the host through
tests/runtime/coop_host.hpp.
"""

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.compiler.cooperative.cooperative import participation
from emitted import device_build, refused, round_trips, watched

REDUCE = """fn block_sums(n:usize, x:ro<u64>[n], g:usize, out:rw<u64>[g]) {
  blocks b in g threads t in 256 {
    shared partial:u64[256] = zeroed;
    let i = b * 256 + t;
    if i < n { partial[t] = x[i]; }
    barrier;
    for k in 0..3 {
      let s:usize = shr(128, k);
      if t < s { partial[t] = partial[t] + partial[t + s]; }
      barrier;
    }
    if t < 32 {
      let total = reduce + warp yield partial[t];
      if t == 0 { out[b] = total; }
    }
  }
}
"""

TRANSPOSE = """fn transpose(gx:usize, gy:usize, n:usize, out:rw<f32>[n], x:ro<f32>[n]) {
  let w = gx * 32;
  let h = gy * 32;
  blocks bx, by in gx, gy threads tx, ty in 32, 8 {
    shared tile:f32[1056] = zeroed;
    for k in 0..4 {
      let r = ty + k * 8;
      tile[r * 33 + tx] = x[(by * 32 + r) * w + bx * 32 + tx];
    }
    barrier;
    for k in 0..4 {
      let r = ty + k * 8;
      out[(bx * 32 + r) * h + by * 32 + tx] = tile[tx * 33 + r];
    }
  }
}
"""

MAIN = """#include <cstdint>
#include <cstdio>
#include <vector>
extern "C" void cf_block_sums(std::size_t, const std::uint64_t*, std::size_t, std::uint64_t*) noexcept;
extern "C" void cf_transpose(std::size_t, std::size_t, std::size_t, float*, const float*) noexcept;
int main() {
  for (std::size_t n : {0, 1, 255, 256, 257, 1000, 4096}) {
    const std::size_t g = (n + 255) / 256 + 1;  // one block past the data sums nothing
    std::vector<std::uint64_t> x(n + 1), out(g, 7);
    for (std::size_t i = 0; i < n; ++i) x[i] = i * 2654435761u % 1000003;
    cf_block_sums(n, x.data(), g, out.data());
    for (std::size_t b = 0; b < g; ++b) {
      std::uint64_t want = 0;
      for (std::size_t i = b * 256; i < n && i < b * 256 + 256; ++i) want += x[i];
      if (out[b] != want) { std::printf("sum n=%zu b=%zu: %llu, want %llu\\n", n, b, (unsigned long long)out[b], (unsigned long long)want); return 1; }
    }
  }
  for (std::size_t gx : {1, 2, 3}) for (std::size_t gy : {1, 2}) {
    const std::size_t w = gx * 32, h = gy * 32, n = w * h;
    std::vector<float> x(n), out(n, -1.0f);
    for (std::size_t i = 0; i < n; ++i) x[i] = float(i % 977) - 400.0f;
    cf_transpose(gx, gy, n, out.data(), x.data());
    for (std::size_t r = 0; r < h; ++r) for (std::size_t c = 0; c < w; ++c)
      if (out[c * h + r] != x[r * w + c]) { std::printf("transpose %zux%zu at %zu,%zu\\n", w, h, r, c); return 2; }
  }
  std::printf("every block agrees\\n");
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_block_reduction_and_a_tiled_transpose_race_nowhere(tmp_path, cxx):
    """Real threads, real barriers, the thread sanitizer watching, and a plain reference to agree with."""
    ran = watched(
        tmp_path,
        compile_source(REDUCE + TRANSPOSE)[0],
        cxx,
        "thread",
        timeout=600,
        entry=None,
        beside={"main.cpp": MAIN},
    )
    assert ran.returncode == 0 and "every block agrees" in ran.stdout, ran.stdout + ran.stderr[-4000:]
    assert "ThreadSanitizer" not in ran.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_sanitizer_reports_the_race_a_missing_barrier_makes(tmp_path, cxx):
    """The oracle bites: with the first barrier taken out of the emitted C++, which the checker would have refused,
    the sanitizer reports the race on the shared array."""
    cpp = compile_source(REDUCE + TRANSPOSE)[0]
    ran = watched(
        tmp_path,
        cpp.replace("cr_blk.sync();", "", 1),
        cxx,
        "thread",
        timeout=600,
        entry=None,
        beside={"main.cpp": MAIN},
    )
    assert ran.returncode != 0 and "ThreadSanitizer: data race" in ran.stderr, ran.stderr[-3000:]


def test_the_device_body_runs_on_host_threads_too(tmp_path):
    """The lambda written for the device, run by the host stand-in: the same agreement, the same silence."""
    device = (REDUCE + TRANSPOSE).replace("[n]", "[n]@device").replace("[g]", "[g]@device")
    cpp = compile_source(device)[0]
    assert "cr::coop::launch<256, 2048>" in cpp and "[=] CR_DEVICE(cr::coop::Device& cr_blk" in cpp
    ran = watched(
        tmp_path, cpp, "clang++", "thread", timeout=600, entry=None, beside={"main.cpp": MAIN}, stand_in="coop_host.hpp"
    )
    assert ran.returncode == 0 and "every block agrees" in ran.stdout, ran.stdout + ran.stderr[-4000:]
    assert "ThreadSanitizer" not in ran.stderr


def test_the_device_lowering_compiles_for_sm_120_to_shared_memory_barriers_and_shuffles(tmp_path):
    """Compiled for sm_120 and never run: a block's arrays are static shared memory, a barrier is bar.sync, and the
    warp reduction is five butterfly shuffles of a u64 (two 32-bit moves each)."""
    device = REDUCE.replace("[n]", "[n]@device").replace("[g]", "[g]@device")
    ptx = device_build(tmp_path, compile_source(device)[0], ptx=True).read_text()
    assert ".shared .align 128 .b8" in ptx and "[2048]" in ptx  # every array starts on 128 bytes (fragments.py)
    assert ptx.count("bar.sync") == 6  # zeroed, loaded, three tree steps, and the block's end
    assert ptx.count("shfl.sync.bfly") == 10
    assert "st.shared" in ptx and "ld.shared" in ptx


def test_a_region_reports_what_it_costs():
    receipt = compile_source(REDUCE)[1]["functions"]["block_sums"]
    assert {"par:host", "zero_init", "trap"} <= set(receipt["effects"])
    assert receipt["syntactic_check_sites"]["cooperative_regions"] == 1
    kinds = {x["kind"]: x for x in receipt["local_storage"]}
    assert kinds["shared"]["bytes"] == 2048 and kinds["blocks"]["threads"] == 256
    assert kinds["blocks"]["shared_bytes"] == 2048


def test_the_canonical_projection_compiles_to_the_same_code():
    for source in (REDUCE, TRANSPOSE):
        round_trips(source)


def test_a_rule_learns_who_reaches_a_statement_together():
    """participation(c, node): the block, each warp, or single threads; None outside a region."""
    from cairn.compiler.check.checking import Checker
    from cairn.compiler.cooperative import cooperative
    from cairn.compiler.syntax.parser import Parser

    seen: dict[str, str | None] = {}
    original = cooperative.s_barrier

    def spy(c, s):
        seen["barrier"] = participation(c, s)
        return original(c, s)

    program = Parser(REDUCE).parse()
    checker = Checker(program)
    checker.s_barrier = lambda s: spy(checker, s)  # type: ignore[method-assign]
    checker.check()
    assert seen["barrier"] == "block"
    block = program.functions[0].body[0].ref
    warp = next(x for x in program.functions[0].body[0].body if x.tag == "if" and x.line == 12)
    assert cooperative.WIDTHS[block.reach[id(warp.body[0])][0]] == "warp"


HEAD = "fn f(n:usize, x:ro<u64>[n], g:usize, out:rw<u64>[g]) {\n  blocks b in g threads t in 256 {\n"
TAIL = "  }\n}\n"


@pytest.mark.parametrize(
    ("code", "body", "said"),
    [
        (
            "E-COOP-UNORDERED",
            "shared s:u64[256] = zeroed;\ns[t] = 1;\nlet v = s[255 - t];\nif t == 0 { out[b] = v; }\n",
            "Put a barrier after line 4 and before line 5",
        ),
        (
            "E-COOP-REUSE",
            "shared s:u64[256] = zeroed;\ns[t] = 1;\nbarrier;\nlet v = s[(t + 1) % 256];\ns[t] = v * 2;\n",
            "may still be reading",
        ),
        (
            "E-COOP-CONFLICT",
            "shared s:u64[256] = zeroed;\ns[t / 2] = 1;\n",
            "written by thread t = 0 and by thread t = 1",
        ),
        (
            "E-COOP-UNORDERED",
            "shared p:u64[256] = zeroed;\np[t] = 1;\nbarrier;\nfor k in 0..3 {\n"
            "let s:usize = shr(128, k);\nif t < s { p[t] = p[t] + p[t + s]; }\n}\n",
            "before it runs again",
        ),
        (
            "E-COOP-UNDECIDED",
            "shared s:u64[256] = zeroed;\nlet i = b * 256 + t;\nif i < n { s[usize(x[i] % 256)] = 1; }\n",
            "cannot tell",
        ),
        (
            "E-COOP-UNDECIDED",
            "shared s:u64[512] = zeroed;\ns[t] = 1;\nif t < 256 { let v = s[t + n % 256]; }\n",
            "cannot tell",
        ),
        ("E-COOP-BARRIER", "if t < 5 { barrier; }\n", "every thread of the block"),
        ("E-COOP-BARRIER", "if t < 32 { barrier; }\n", "warp to warp"),
        ("E-COOP-BARRIER", "for k in 0..4 { if k == n { break; } barrier; }\n", "break at line"),
        ("E-COOP-WARP", "let v = reduce + warp yield t;\nif t % 2 == 0 { let w = reduce + warp yield t; }\n", "a warp"),
        ("E-COOP-WARP", "if t == 3 { let v = shuffle_xor(t, 1); }\n", "every thread of a warp"),
        ("E-COOP-GLOBAL", "out[b] = 1;\n", "does not depend on t"),
        (
            "E-COOP-GLOBAL",
            "if t == 0 { out[b] = 1; }\nif t == 1 { let v = out[(b + 1) % g]; }\n",
            "at the element it writes",
        ),
        ("E-COOP-GLOBAL", "if t == 0 { out[b / 2] = 1; }\n", "b"),
        ("E-COOP-SHARED", "if t < 4 { shared s:u64[4] = zeroed; }\n", "directly in the body"),
        ("E-COOP-SHARED", "shared s:u64[8192] = zeroed;\n", "49152"),
        ("E-PARALLEL-NEST", "parallel i in n { let v = i; }\n", "cannot start another"),
        ("E-PARALLEL-WRITE", "n = 3;\n", "Every lane would write"),
        ("E-PARALLEL-CALL", "println(t);\n", "cannot"),
        ("E-REDUCE-OP", "let v:i64 = reduce + warp yield i64(t);\n", "unsigned"),
    ],
)
def test_every_cooperative_rule_refuses_with_its_code(code, body, said):
    source = HEAD + body + TAIL
    if code == "E-PARALLEL-WRITE":
        source = source.replace("fn f(n:usize,", "fn f(m:usize,").replace("x:ro<u64>[n]", "x:ro<u64>[m]")
        source = source.replace("  blocks", "  let mut n:usize = 0;\n  blocks")
    assert said in refused(code, source)["message"]


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-COOP-SHAPE", "fn f(g:usize, k:usize) { blocks b in g threads t in k { } }"),
        ("E-COOP-SHAPE", "fn f(g:usize) { blocks b in g threads t in 48 { } }"),
        ("E-COOP-SHAPE", "fn f(g:usize) { blocks b in g threads t in 2048 { } }"),
        ("E-COOP-BARRIER", "fn f() { barrier; }"),
        ("E-COOP-WARP", "fn f(v:u64) -> u64 = shuffle(v, 0);"),
        ("E-PARALLEL-NEST", "fn f(n:usize, g:usize) { parallel i in n { blocks b in g threads t in 32 { } } }"),
        (
            "E-PLACEMENT",
            "fn f(n:usize, x:ro<u64>[n], d:rw<u64>[n]@device) { blocks b in 1 threads t in 32 { "
            "if t < n { d[t] = x[t]; } } }",
        ),
    ],
)
def test_a_region_s_shape_and_its_collectives_are_refused_outside_what_runs(code, source):
    refused(code, source)


TILE_STORE = """fn store(m:usize, n:usize, across:usize, down:usize, k:usize, c:rw<f32>[k]) {
  blocks bx, by in across, down threads tx, ty in 32, 4 {
    for i in 0..16 {
      for j in 0..WIDE {
        if by * 64 + ty + 4 * i < m && bx * 64 + tx + 32 * j < n { c[(by * 64 + ty + 4 * i) * n + bx * 64 + tx + 32 * j] = 1.0; }
      }
    }
  }
}
"""


def test_a_guarded_tile_store_is_one_writer_per_element_and_a_wider_one_is_not():
    """A 64 x 64 output tile per block, each thread 16 rows and 2 columns 32 apart, cut by the matrix's edge: the
    column guard bounds tx + 32 j + 64 bx below n, so the row's weight n is above everything lighter. Three columns
    32 apart reach 96 elements, into the next block's tile: refused."""
    compile_source(TILE_STORE.replace("WIDE", "2"))
    assert "is not shown to exceed" in refused("E-COOP-GLOBAL", TILE_STORE.replace("WIDE", "3"))["message"]


def test_a_read_at_a_pinned_writer_s_index_is_not_the_reader_s_own():
    """out[b] is thread 0's element; thread 5 reading out[b] reads what thread 0 writes, in the same phase."""
    source = HEAD + "if t == 0 { out[b] = 1; }\nlet v = out[b];\n" + TAIL
    assert "at the element it writes" in refused("E-COOP-GLOBAL", source)["message"]


def test_a_block_s_warp_names_are_warp_wide():
    """ty is one warp's name when tx counts 32, so a shuffle under `ty < 4` has whole warps; under `tx < 4` not."""
    shape = "fn f(g:usize) {\n  blocks b in g threads tx, ty in 32, 8 {\n    if COND { let v = shuffle_down(tx, 1); }\n  }\n}\n"
    compile_source(shape.replace("COND", "ty < 4"))
    refused("E-COOP-WARP", shape.replace("COND", "tx < 4"))
    compile_source(shape.replace("tx, ty in 32, 8", "tx, ty in 64, 2").replace("COND", "tx < 32"))
    refused("E-COOP-WARP", shape.replace("tx, ty in 32, 8", "tx, ty in 16, 4").replace("COND", "ty < 2"))


def test_a_name_or_array_the_body_leaves_unused_still_compiles_for_the_device(tmp_path):
    """nvcc treats an unused variable as an error under the project's flags; every name the lowering declares may go
    unused."""
    source = "fn f(n:usize, out:rw<u64>[n]@device) {\n  blocks g in 1 threads t in 32 {\n"
    source += "    shared unused:u64[4] = zeroed;\n    if t < n { out[t] = 1; }\n  }\n}\n"
    device_build(tmp_path, compile_source(source)[0])


IN_PLACE = """fn scale(m:usize, n:usize, across:usize, down:usize, k:usize, c:rw<f32>[k]) {
  blocks bx, by in across, down threads tx, ty in 32, 4 {
    let mut held:f32 = 0.0;
    for i in 0..READ {
      for j in 0..2 {
        if GUARD { held = held + c[(by * 64 + ty + 4 * i) * n + bx * 64 + tx + 32 * j]; }
      }
    }
    for i in 0..16 {
      for j in 0..2 {
        if by * 64 + ty + 4 * i < m && bx * 64 + tx + 32 * j < n { c[(by * 64 + ty + 4 * i) * n + bx * 64 + tx + 32 * j] = held; }
      }
    }
  }
}
"""
BOTH = "by * 64 + ty + 4 * i < m && bx * 64 + tx + 32 * j < n"


def test_a_thread_may_read_what_it_writes_through_other_loops_over_the_same_range():
    """c += ...: a read in one loop nest and the write in another, over the same ranges and under the same
    conditions, is the thread's own element. A read over a longer range, or without the write's conditions, is not."""
    compile_source(IN_PLACE.replace("READ", "16").replace("GUARD", BOTH))
    refused("E-COOP-GLOBAL", IN_PLACE.replace("READ", "17").replace("GUARD", BOTH))
    refused("E-COOP-GLOBAL", IN_PLACE.replace("READ", "16").replace("GUARD", "bx * 64 + tx + 32 * j < n"))


COUNTED = """fn f(g:usize, n:usize, out:rw<u64>[n]) {
  blocks b in g threads t in 32 {
    for k in 0..2 {
      if t + 32 * k < 40 { out[INDEX + 32 * k + 40 * b] = 1; }
    }
  }
}
"""


def test_a_condition_bounds_a_sum_of_digits_only_while_they_count_one_way():
    """Under t + 32 k < 40, t + 32 k stays below 40, so blocks 40 apart never meet. With 31 - t in its place the lighter
    part reaches 0 to 63, and blocks 0 and 1 both write element 63: (b, t, k) = (0, 0, 1) and (1, 8, 0)."""
    compile_source(COUNTED.replace("INDEX", "t"))
    refused("E-COOP-GLOBAL", COUNTED.replace("INDEX", "31 - t"))


def test_a_digit_whose_range_moves_with_another_is_not_bounded_by_it():
    """c runs from l to l + 1, so l + 3 c reaches 0 to 11 while the ranges add up to 5: threads 0 and 1 both write
    element 7, at (t, l, c) = (1, 0, 0) and (0, 1, 2)."""
    source = "fn f(n:usize, out:rw<u64>[n]) {\n  blocks b in 1 threads t in 32 {\n    for l in 0..3 {\n"
    source += "      for c in RANGE { out[l + 3 * c + 7 * t] = 1; }\n    }\n  }\n}\n"
    assert "moves with l" in refused("E-COOP-GLOBAL", source.replace("RANGE", "l..l + 2"))["message"]
    compile_source(source.replace("RANGE", "0..2"))


SHUFFLES = """fn lanes(g:usize, n:usize, out:rw<u64>[n], most:rw<u64>[n], sums:rw<f32>[n]) {
  blocks b in g threads t in 64 {
    let v:u64 = u64(t) * 3 + 1;
    let a = shuffle_xor(v, 5);
    let c = shuffle_down(v, 7);
    let d = shuffle(v, 31);
    let top = reduce max warp yield v;
    let total = reduce + warp yield f32(t) * 0.5;
    let i = b * 64 + t;
    if i < n {
      out[i] = a * 1000000 + c * 1000 + d;
      most[i] = top;
      sums[i] = total;
    }
  }
}

fn main() -> i32 {
  let n:usize = 128;
  buffer out:u64[n] = zeroed;
  buffer most:u64[n] = zeroed;
  buffer sums:f32[n] = zeroed;
  lanes(2, n, out, most, sums);
  for i in 0..n {
    let t = i % 64;
    let lane = t % 32;
    let base = t - lane;
    let mut down = lane + 7;
    if down >= 32 { down = lane; }
    let want = u64(base + (lane ^ 5)) * 3 + 1;
    let got = (u64(base + down) * 3 + 1) * 1000 + u64(base + 31) * 3 + 1;
    if out[i] != want * 1000000 + got { return 1; }
    if most[i] != u64(base + 31) * 3 + 1 { return 2; }
    let mut half:f32 = 0.0;
    for k in 0..32 { half = half + f32(base + k) * 0.5; }   // small halves: every order gives these bits
    if to_bits(sums[i]) != to_bits(half) { return 3; }
  }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_each_shuffle_moves_the_value_of_the_lane_it_names(tmp_path, cxx):
    """shuffle(v, 31), shuffle_xor(v, 5) and shuffle_down(v, 7), which keeps its own value past the warp's end,
    against the plain arithmetic of which lane each names, on host threads under the thread sanitizer."""
    ran = watched(tmp_path, compile_source(SHUFFLES)[0], cxx, "thread")
    assert ran.returncode == 0 and "ThreadSanitizer" not in ran.stderr, ran.stdout + ran.stderr[-3000:]


def test_each_shuffle_is_one_warp_shuffle_on_the_device(tmp_path):
    device = SHUFFLES.split("fn main")[0].replace("[n])", "[n]@device)").replace("[n],", "[n]@device,")
    ptx = device_build(tmp_path, compile_source(device)[0], ptx=True).read_text()
    assert "shfl.sync.bfly" in ptx and "shfl.sync.down" in ptx and "shfl.sync.idx" in ptx


VARIETY = """fn variety(g:usize, h:usize, n:usize, out:rw<f32>[n]@device, flags:rw<u8>[n]@device) {
  blocks bx, by, bz in g, h, 2 threads tx, ty, tz in 32, 2, 2 {
    shared halves:f16[128] = zeroed;
    shared marks:bool[128] = zeroed;
    let t = tx + 32 * ty + 64 * tz;
    halves[t] = f16(f32(t));
    marks[t] = t % 2 == 0;
    barrier;
    let other = f32(halves[127 - t]);
    let small:u8 = u8(tx);
    let flip = shuffle_xor(marks[t], 1);
    let low = shuffle_down(small, 3);
    let wide:i16 = i16(tx) - 16;
    let neg = shuffle(wide, 2);
    let sum = reduce + warp yield small;
    let block = (bz * h + by) * g + bx;
    let i = block * 128 + t;
    if i < n {
      out[i] = other + f32(neg);
      if flip { flags[i] = low + sum; } else { flags[i] = 0; }
    }
  }
}
"""


def test_three_dimensional_shapes_and_narrow_values_compile_for_the_device(tmp_path):
    """Three block and three thread names, f16 and bool shared arrays, shuffles of a bool, a u8 and an i16 (moved as
    32 bits), and a u8 warp sum: compiled for sm_120, never run."""
    ptx = device_build(tmp_path, compile_source(VARIETY)[0], ptx=True).read_text()
    assert ptx.count("shfl.sync") == 8 and ptx.count("bar.sync") == 3
