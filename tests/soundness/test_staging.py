"""`plan f { stage R; }`: each block of a device region loads, into shared memory, the elements its lanes read near
their index, once, and the lanes read them there.

The staged lambdas are run on the host by tests/runtime/gpu_host.hpp, block by block with every load before every
body and the tile poisoned first, under the sanitizers and against the unplanned region. Device code is compiled for
sm_120 here and never run: the runs that compare a staged region with the unplanned one are in `make gpu`.
"""

import re
import subprocess

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from emitted import build, device_build, ran_on_device, refused

BLUR = """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 2 && i + 2 < n { out[i] = x[i - 2] + x[i - 1] * 2.0 + x[i] * 3.0 + x[i + 1] + x[i + 2]; }
    else { out[i] = x[i]; }
  }
}
"""
SELF = "fn self_read(n:usize, x:rw<f32>[n]@device) { parallel i in n { if i + 1 < n { x[i] = x[i + 1]; } } }\n"
FAR = "fn far(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { if i + 5 < n { out[i] = x[i + 5]; } } }\n"
ONLY = "fn only(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = x[i]; } }\n"
GUARDED = "fn guarded(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = x[i + 1]; } }\n"
HOST = "fn host(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { parallel i in n { if i + 1 < n { out[i] = x[i + 1]; } } }\n"


@pytest.mark.parametrize(
    ("source", "said"),
    [
        (FAR + "plan far { stage 2; }", "reads none that way"),  # an offset of 5 is past a reach of 2
        (ONLY + "plan only { stage 1; }", "reads none that way"),  # every read at [i]: nothing is shared
        (GUARDED + "plan guarded { stage 1; }", "reads none that way"),  # x[i + 1] keeps its guard
        (HOST + "plan host { stage 1; }", "device parallel region"),
        (BLUR + "plan blur { stage 2; vector 2; }", "takes one"),
        (BLUR + "plan blur { stage 0; }", "from 1 to 32"),
    ],
)
def test_a_stage_plan_is_refused_where_a_tile_could_be_wrong_or_would_change_nothing(source, said):
    assert said in refused("E-PLAN", source)["message"]


def test_an_array_the_lanes_write_is_never_read_off_the_lane_s_own_index():
    """So a tile of it could never be stale: the lane rule refuses the read before a plan is looked at."""
    refused("E-PARALLEL-RACE", SELF + "plan self_read { stage 1; }")


def test_a_stage_plan_loads_a_tile_and_reads_the_array_from_it():
    planned, receipt = compile_source(BLUR + "plan blur { stage 2; block 64; }")
    assert "cr::gpu::run_staged<2>(cr::gpu::here(), v_n, " in planned and planned.rstrip().endswith("}, 64);\n}")
    assert "if (cr_g >= 2 && cr_g - 2 < v_n) cr_s_x[cr_e] = v_x[cr_g - 2];" in planned
    assert "v_x[v_i" not in planned  # every read of x is the tile's
    plain = compile_source(BLUR)[1]["functions"]["blur"]
    assert {k: v for k, v in receipt["functions"]["blur"].items() if k != "plan"} == plain
    canonical = canonical_source(BLUR + "plan blur { stage 2; block 64; }\n")
    assert "plan blur { block 64; stage 2; }" in canonical
    assert compile_source(canonical)[0] == planned


MAIN = """#include <cstdio>
#include <cstring>
#include <vector>
extern "C" void cf_blur(std::size_t, float*, const float*) noexcept;
extern "C" void cf_plain(std::size_t, float*, const float*) noexcept;
int main() {
  for (std::size_t n : {0, 1, 3, 4, 5, 31, 32, 33, 64, 65, 200, 1001}) {
    std::vector<float> x(n + 1), a(n + 1), b(n + 1);
    for (std::size_t i = 0; i < n; ++i) x[i] = float((i * 37) % 11) - 5.0f;
    cf_blur(n, a.data(), x.data());
    cf_plain(n, b.data(), x.data());
    if (n && std::memcmp(a.data(), b.data(), n * sizeof(float))) { std::printf("differs at n=%zu\\n", n); return 1; }
  }
  std::printf("every tile agrees\\n");
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
@pytest.mark.parametrize("plan", ["plan blur { stage 2; block 32; }", "plan blur { stage 3; block 64; }"])
def test_every_tile_a_staged_region_loads_holds_what_its_lanes_read(tmp_path, cxx, plan):
    """The lambdas the compiler writes, run on the host: every block's loads before its bodies, the tile filled with
    a pattern first, under the sanitizers, against the unplanned region at sizes around the block's edges."""
    planned, plain = compile_source(BLUR + plan)[0], compile_source(BLUR.replace("blur", "plain"))[0]
    flags = ("-std=c++20", "-O1", "-g", "-ffp-contract=off", "-fno-fast-math", "-fsanitize=address,undefined",
             "-fno-sanitize-recover=all")  # fmt: skip
    exe = build(tmp_path, planned, *flags, cxx=cxx, entry=None, timeout=300,
                beside={"plain.cpp": plain, "main.cpp": MAIN}, stand_in="gpu_host.hpp")  # fmt: skip
    done = subprocess.run([exe], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "every tile agrees" in done.stdout, done.stdout + done.stderr[-3000:]


def test_a_staged_region_reads_shared_memory_on_the_device(tmp_path):
    """Compiled for sm_120 and never run: the tile is stored to and read from shared memory between barriers."""
    ptx = device_build(tmp_path, compile_source(BLUR + "plan blur { stage 2; block 128; }")[0], ptx=True).read_text()

    def count(op: str) -> int:  # a 32-bit access: CUDA 13 writes it .b32, CUDA 12.9 .f32
        return len(re.findall(re.escape(op) + r"\.[bf]32\b", ptx))

    assert count("ld.global") == 1 and count("ld.shared") == 6  # one tile load, six reads
    assert count("st.shared") == 1 and ptx.count("bar.sync") == 2


ON_DEVICE = """
fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 2 && i + 2 < n { out[i] = x[i - 2] + x[i - 1] * 2.0 + x[i] * 3.0 + x[i + 1] + x[i + 2]; }
    else { out[i] = x[i]; }
  }
}
PLAN
fn main() -> i32 {
  let n:usize = 100003;
  buffer h:f32[n] = zeroed;
  for i in 0..n { h[i] = f32(i64(mul_wrap(u64(i), 2654435761) % 23) - 11); }
  buffer x:f32[n]@device = zeroed;
  transfer(x, h);
  buffer d:f32[n]@device = zeroed;
  blur(n, d, x);
  buffer back:f32[n] = zeroed;
  transfer(back, d);
  for i in 0..n {
    let mut want = h[i];
    if i >= 2 && i + 2 < n { want = h[i - 2] + h[i - 1] * 2.0 + h[i] * 3.0 + h[i + 1] + h[i + 2]; }
    if to_bits(back[i]) != to_bits(want) { return 1; }
  }
  return 0;
}
"""
STAGES = ["plan blur { stage 2; }", "plan blur { stage 2; block 32; per_lane 16; unroll 2; }",
          "plan blur { stage 32; block 1024; }"]  # fmt: skip


@pytest.mark.parametrize("plan", STAGES)
def test_every_stage_plan_compiles_for_the_device_without_touching_it(tmp_path, plan):
    device_build(tmp_path, compile_source(ON_DEVICE.replace("PLAN", plan))[0], entry="main")


@pytest.mark.parametrize("plan", ["", *STAGES])
def test_every_stage_plan_computes_what_the_unplanned_region_computes(tmp_path, plan):
    ran_on_device(tmp_path, compile_source(ON_DEVICE.replace("PLAN", plan))[0])  # only under `make gpu`
