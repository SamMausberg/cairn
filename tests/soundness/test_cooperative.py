"""Cooperative regions, `blocks b in G threads t in T { }`: blocks of threads that share arrays and meet at barriers.

Every rule has a rejection naming its code. Accepted regions run on the host as real threads meeting at a
std::barrier, under ThreadSanitizer with both compilers, against a plain reference written in C++; the same programs
with the barrier taken out of the emitted C++ must make the sanitizer report a race, so the oracle is shown to bite.
The device lowering compiles for sm_120 and is inspected, never run here; its lambdas also run on the host through
tests/runtime/coop_host.hpp.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.compiler.cooperative import participation
from cairn.projects.toolchain import command
from emitted import device_build, refused

ROOT = Path(__file__).resolve().parents[2]

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


def build_and_run(tmp_path: Path, cpp: str, cxx: str, sanitizer: str, stand_in: bool = False):
    """The emitted C++ beside a C++ main holding the plain references, built by the project's own command line under
    `sanitizer`, and run with address randomization off, which ThreadSanitizer needs on newer kernels."""
    if not shutil.which(cxx) or not shutil.which("setarch"):
        pytest.skip(f"needs {cxx} and setarch")
    if stand_in:
        cpp = cpp.replace('#include "cairn_gpu.hpp"', '#include "coop_host.hpp"')
    (tmp_path / "kernels.cpp").write_text(cpp)
    (tmp_path / "main.cpp").write_text(MAIN)
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    for stand_in in ("coop_host.hpp", "gpu_host.hpp"):
        (tmp_path / stand_in).write_text((ROOT / "tests/runtime" / stand_in).read_text())
    line = command(cxx, str(tmp_path / "kernels.cpp"), str(tmp_path / "t"), kind="exe")
    line[line.index(str(tmp_path / "kernels.cpp")) : line.index(str(tmp_path / "kernels.cpp")) + 1] = [
        str(tmp_path / "kernels.cpp"), str(tmp_path / "main.cpp")]  # fmt: skip
    built = subprocess.run([*line, "-g", f"-fsanitize={sanitizer}", "-fno-sanitize-recover=all"],
                           capture_output=True, text=True, timeout=600)  # fmt: skip
    assert built.returncode == 0, built.stderr[-3000:]
    env = {**os.environ, "TSAN_OPTIONS": "halt_on_error=1"}
    return subprocess.run(["setarch", "-R", str(tmp_path / "t")], capture_output=True, text=True, timeout=600, env=env)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_block_reduction_and_a_tiled_transpose_race_nowhere(tmp_path, cxx):
    """Real threads, real barriers, the thread sanitizer watching, and a plain reference to agree with."""
    ran = build_and_run(tmp_path, compile_source(REDUCE + TRANSPOSE)[0], cxx, "thread")
    assert ran.returncode == 0 and "every block agrees" in ran.stdout, ran.stdout + ran.stderr[-4000:]
    assert "ThreadSanitizer" not in ran.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_sanitizer_reports_the_race_a_missing_barrier_makes(tmp_path, cxx):
    """The oracle bites: with the first barrier taken out of the emitted C++, which the checker would have refused,
    the sanitizer reports the race on the shared array."""
    cpp = compile_source(REDUCE + TRANSPOSE)[0]
    ran = build_and_run(tmp_path, cpp.replace("cr_blk.sync();", "", 1), cxx, "thread")
    assert ran.returncode != 0 and "ThreadSanitizer: data race" in ran.stderr, ran.stderr[-3000:]


def test_the_device_body_runs_on_host_threads_too(tmp_path):
    """The lambda written for the device, run by the host stand-in: the same agreement, the same silence."""
    device = (REDUCE + TRANSPOSE).replace("[n]", "[n]@device").replace("[g]", "[g]@device")
    cpp = compile_source(device)[0]
    assert "cr::coop::launch<256, 2048>" in cpp and "[=] CR_DEVICE(cr::coop::Device& cr_blk" in cpp
    ran = build_and_run(tmp_path, cpp, "clang++", "thread", stand_in=True)
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
        canonical = canonical_source(source)
        assert canonical_source(canonical) == canonical
        assert compile_source(canonical)[0] == compile_source(source)[0]


def test_a_rule_learns_who_reaches_a_statement_together():
    """participation(c, node): the block, each warp, or single threads; None outside a region."""
    from cairn.compiler import cooperative
    from cairn.compiler.checking import Checker
    from cairn.compiler.syntax import Parser

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
