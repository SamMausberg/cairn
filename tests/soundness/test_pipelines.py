"""Pipeline stages in cooperative regions: `pipeline s:T[N] depth D;`, filled from an outside array while the block's
threads read another stage, with every stage's state followed by the checker.

The same program at depth 2 and depth 3 computes the same sums, held to a plain reference in C++, on real host threads
under ThreadSanitizer with both compilers; a fill moved past its barrier in the emitted C++ makes the sanitizer report
the race the checker refuses. Depth changes the shared memory a block holds and the number of transfers a wait leaves
in flight, which the sm_120 PTX shows; nothing here runs on a GPU.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.projects.toolchain import command
from emitted import device_build, refused

ROOT = Path(__file__).resolve().parents[2]

ROWS = """fn row_sums[D:nat](rows:usize, cols:usize, n:usize, x:ro<u64>[n], out:rw<u64>[rows]) {
  let steps = (cols + 255) / 256;
  blocks r in rows threads t in 256 {
    pipeline tiles:u64[256] depth D;
    shared partial:u64[256] = zeroed;
    for k in 0..D - 1 {
      let start = min(k * 256, cols);
      tiles.fill(x, r * cols + start, min(256, cols - start));
    }
    let mut sum:u64 = 0;
    for k in 0..steps {
      let ahead = min((k + D - 1) * 256, cols);
      tiles.fill(x, r * cols + ahead, min(256, cols - ahead));
      tiles.wait();
      sum += tiles[255 - t];                     // a stage element another thread copied in
      tiles.release();
      barrier;
    }
    partial[t] = sum;
    barrier;
    for k in 0..3 {
      let s:usize = shr(128, k);
      if t < s { partial[t] = partial[t] + partial[t + s]; }
      barrier;
    }
    if t < 32 {
      let total = reduce + warp yield partial[t];
      if t == 0 { out[r] = total; }
    }
  }
}
fn double(rows:usize, cols:usize, n:usize, x:ro<u64>[n], out:rw<u64>[rows]) { row_sums[2](rows, cols, n, x, out); }
fn triple(rows:usize, cols:usize, n:usize, x:ro<u64>[n], out:rw<u64>[rows]) { row_sums[3](rows, cols, n, x, out); }
"""

MAIN = """#include <cstdint>
#include <cstdio>
#include <vector>
extern "C" void cf_double(std::size_t, std::size_t, std::size_t, const std::uint64_t*, std::uint64_t*) noexcept;
extern "C" void cf_triple(std::size_t, std::size_t, std::size_t, const std::uint64_t*, std::uint64_t*) noexcept;
int main() {
  for (std::size_t rows : {1, 3}) for (std::size_t cols : {0, 1, 255, 256, 700, 1024}) {
    const std::size_t n = rows * cols;
    std::vector<std::uint64_t> x(n + 1), two(rows, 7), three(rows, 9);
    for (std::size_t i = 0; i < n; ++i) x[i] = i * 2654435761u % 1000003;
    cf_double(rows, cols, n, x.data(), two.data());
    cf_triple(rows, cols, n, x.data(), three.data());
    for (std::size_t r = 0; r < rows; ++r) {
      std::uint64_t want = 0;
      for (std::size_t c = 0; c < cols; ++c) want += x[r * cols + c];
      if (two[r] != want || three[r] != want) { std::printf("rows %zu cols %zu row %zu\\n", rows, cols, r); return 1; }
    }
  }
  std::printf("both depths agree\\n");
  return 0;
}
"""


def build_and_run(tmp_path: Path, cpp: str, cxx: str, stand_in: bool = False):
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
    at = line.index(str(tmp_path / "kernels.cpp"))
    line[at : at + 1] = [str(tmp_path / "kernels.cpp"), str(tmp_path / "main.cpp")]
    built = subprocess.run([*line, "-g", "-fsanitize=thread"], capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr[-3000:]
    env = {**os.environ, "TSAN_OPTIONS": "halt_on_error=1"}
    return subprocess.run(["setarch", "-R", str(tmp_path / "t")], capture_output=True, text=True, timeout=600, env=env)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_double_and_a_triple_buffered_row_sum_agree_with_a_plain_loop(tmp_path, cxx):
    ran = build_and_run(tmp_path, compile_source(ROWS)[0], cxx)
    assert ran.returncode == 0 and "both depths agree" in ran.stdout, ran.stdout + ran.stderr[-4000:]
    assert "ThreadSanitizer" not in ran.stderr


def test_the_sanitizer_reports_a_read_of_a_stage_still_being_filled(tmp_path):
    """The oracle bites: with every wait's barrier taken out of the emitted C++, a thread reads a stage element
    another thread is still copying in, and the sanitizer reports it."""
    cpp = compile_source(ROWS)[0].replace("block.sync();", "")
    runtime = RUNTIME_FILES["cairn_coop.hpp"]
    assert "block.sync();" in runtime  # the wait's barrier is in the runtime, so take it out there
    patched = dict(RUNTIME_FILES)
    patched["cairn_coop.hpp"] = runtime.replace("    block.sync();\n", "", 1)
    if not shutil.which("clang++") or not shutil.which("setarch"):
        pytest.skip("needs clang++ and setarch")
    (tmp_path / "kernels.cpp").write_text(cpp)
    (tmp_path / "main.cpp").write_text(MAIN)
    for name, text in patched.items():
        (tmp_path / name).write_text(text)
    line = command("clang++", str(tmp_path / "kernels.cpp"), str(tmp_path / "t"), kind="exe")
    at = line.index(str(tmp_path / "kernels.cpp"))
    line[at : at + 1] = [str(tmp_path / "kernels.cpp"), str(tmp_path / "main.cpp")]
    subprocess.run([*line, "-g", "-fsanitize=thread"], check=True, capture_output=True, timeout=600)
    ran = subprocess.run(["setarch", "-R", str(tmp_path / "t")], capture_output=True, text=True, timeout=600)
    assert "ThreadSanitizer: data race" in ran.stderr, ran.stderr[-3000:]


def test_the_device_body_of_both_depths_runs_on_host_threads(tmp_path):
    device = ROWS.replace("[n]", "[n]@device").replace("[rows]", "[rows]@device")
    ran = build_and_run(tmp_path, compile_source(device)[0], "clang++", stand_in=True)
    assert ran.returncode == 0 and "both depths agree" in ran.stdout, ran.stdout + ran.stderr[-4000:]


def test_depth_changes_the_shared_memory_and_the_wait_and_nothing_else():
    cpp, receipt = compile_source(ROWS)
    kinds = {
        f: {x["kind"]: x for x in receipt["functions"][f]["local_storage"]} for f in ("row_sums[2]", "row_sums[3]")
    }
    assert kinds["row_sums[2]"]["pipeline"]["bytes"] == 4096 and kinds["row_sums[3]"]["pipeline"]["bytes"] == 6144
    assert (
        kinds["row_sums[2]"]["blocks"]["shared_bytes"] == 6144
        and kinds["row_sums[3]"]["blocks"]["shared_bytes"] == 8192
    )
    assert "wait<1>(cr_blk)" in cpp and "wait<2>(cr_blk)" in cpp


def test_a_deeper_pipeline_leaves_more_transfers_in_flight_on_the_device(tmp_path):
    device = ROWS.replace("[n]", "[n]@device").replace("[rows]", "[rows]@device")
    ptx = device_build(tmp_path, compile_source(device)[0], ptx=True).read_text()
    assert "cp.async.ca.shared.global" in ptx and "cp.async.commit_group" in ptx
    assert "cp.async.wait_group 1" in ptx and "cp.async.wait_group 2" in ptx
    assert "[6144]" in ptx and "[8192]" in ptx  # each instance's static shared memory


def test_the_canonical_projection_compiles_to_the_same_code():
    canonical = canonical_source(ROWS)
    assert canonical_source(canonical) == canonical and compile_source(canonical)[0] == compile_source(ROWS)[0]


HEAD = "fn f(n:usize, x:ro<u64>[n], g:usize, out:rw<u64>[g]) {\n  blocks b in g threads t in 256 {\n"
HEAD += "    pipeline p:u64[256] depth 2;\n"
TAIL = "  }\n}\n"


@pytest.mark.parametrize(
    ("code", "body", "said"),
    [
        ("E-STAGE-UNREADY", "p.fill(x, 0, min(256, n));\nlet v = p[t];\np.wait();\n", "may still be in flight"),
        ("E-STAGE-UNREADY", "let v = p[t];\n", "no stage has been filled"),
        ("E-STAGE-UNREADY", "p.wait();\n", "waits for"),
        ("E-STAGE-UNREADY", "p.fill(x, 0, 0);\np.wait();\np.release();\nlet v = p[t];\n", "released at line"),
        (
            "E-STAGE-BUSY",
            "p.fill(x, 0, 0);\np.wait();\nlet v = p[t];\np.release();\np.fill(x, 0, 0);\np.fill(x, 0, 0);\n",
            "still be reading it (p[...] at line 6)",
        ),
        ("E-STAGE-BUSY", "p.fill(x, 0, 0);\np.fill(x, 0, 0);\np.fill(x, 0, 0);\n", "all 2 are in flight"),
        ("E-STAGE-BUSY", "p.fill(x, 0, 0);\np.fill(x, 0, 0);\np.wait();\np.wait();\n", "release it first"),
        (
            "E-STAGE-BUSY",
            "p.fill(x, 0, 0);\nfor k in 0..g {\np.fill(x, 0, 0);\np.wait();\nlet v = p[t];\np.release();\n}\n",
            "Put a barrier after line 9",
        ),
        ("E-STAGE-LOOP", "for k in 0..g {\np.fill(x, 0, 0);\n}\n", "leaves p with 1 transfer(s)"),
        ("E-STAGE-LOOP", "if g > 3 { p.fill(x, 0, 0); }\n", "two states"),
        ("E-COOP-BARRIER", "if t == 0 { p.fill(x, 0, 0); }\n", "every thread of the block"),
        ("E-COOP-SHARED", "if g > 1 { pipeline q:u64[4] depth 2; }\n", "directly in the body"),
        ("E-COOP-GLOBAL", "if t == 0 { out[b] = 1; }\np.fill(out, 0, 0);\n", "at the element it writes"),
        # a stage used whole or in part before its wait, which dereferenced the null stage pointer
        ("E-STAGE-UNREADY", "let v = first(256, p);\np.fill(x, 0, 0);\np.wait();\n", "p at line 4 uses"),
        ("E-STAGE-UNREADY", "let v = first(4, p[0..4]);\np.fill(x, 0, 0);\np.wait();\n", "p at line 4 uses"),
        # a fill loop left early: with one fill in flight the wait after it left one in flight too (wait<1>)
        ("E-STAGE-LOOP", "for k in 0..2 {\np.fill(x, 0, 0);\nif n > 5 { break; }\n}\np.wait();\n", "break at line 6"),
        ("E-STAGE-LOOP", "for k in 0..2 {\nif n > 5 { continue; }\np.fill(x, 0, 0);\n}\n", "continue at line 5"),
        ("E-PLACEMENT", "pipeline q:u64[256] depth 1;\nq.fill(x, 0, 0);\nq.wait();\np.fill(q, 0, 0);\n", "q is not"),
        ("E-PLACEMENT", "shared s:u64[256] = zeroed;\np.fill(s, 0, 0);\n", "s is not"),
    ],
)
def test_every_stage_rule_refuses_with_its_code(code, body, said):
    first = "fn first(n:usize, x:ro<u64>[n]) -> u64 = x[0];\n"
    assert said in refused(code, HEAD + body + TAIL + first)["message"]


@pytest.mark.parametrize(
    ("source", "view"),
    [("x:ro<u64>[n]@device, out:rw<u64>[256]", "host"), ("x:ro<u64>[n], out:rw<u64>[256]@device", "device")],
)
def test_a_fill_copies_from_memory_where_the_region_runs(source, view):
    """A region runs where the views it indexes live, and a fill's source must live there too."""
    program = f"fn f(n:usize, {source}) {{\n  blocks r in 1 threads t in 256 {{\n    pipeline p:u64[256] depth 2;\n"
    program += "    p.fill(x, 0, min(256, n));\n    p.wait();\n    out[t] = p[t];\n    p.release();\n  }\n}\n"
    assert f"from {view} memory" in refused("E-PLACEMENT", program)["message"]


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("pipeline p:u16[256] depth 2;", "4- or 8-byte"),
        ("pipeline p:u64[256] depth 9;", "from 1 to 8"),
        ("pipeline p:u64[4096] depth 2;", "49152"),
    ],
)
def test_a_pipeline_s_declaration_is_refused_outside_what_fits(source, said):
    program = "fn f(g:usize) {\n  blocks b in g threads t in 32 {\n    " + source + "\n  }\n}\n"
    assert said in refused("E-COOP-SHARED", program)["message"]


def test_a_fill_reads_its_source_and_cairn_doc_shows_what_depth_holds():
    """The row says the region reads x through its fills, and the reference shows each instance's shared memory, which
    only the depth changes."""
    from cairn.editor.docs import document

    assert "read:x" in compile_source(ROWS)[1]["functions"]["row_sums[2]"]["effects"]
    assert "shared memory a block: 6144 bytes in row_sums[2], 8192 bytes in row_sums[3]" in document(ROWS)
