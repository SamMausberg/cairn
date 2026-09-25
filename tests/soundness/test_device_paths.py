"""The device fast paths: `plan f { vector W; }` chunks, and what they may and may not change.

A vectored lane runs W adjacent indices over one W-wide chunk of each array it touches only at [i], loaded before its
body and stored after; a region whose pointers are not all on their chunk's width runs its scalar lanes instead.
Every index runs once either way, so a plan changes the time a region takes and never what it computes. Device code
is compiled here for sm_120 and never run: the runs that compare a vectored region with its scalar lanes are in
`make gpu`.
"""

import re

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_program, compile_source
from cairn.perf import model
from cairn.perf.profile import packaged
from cairn.perf.tuning.tune import tune
from cairn.perf.work import count
from emitted import contract, device_build, on_device, refused

SAXPY = """fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
"""
BUMP = """fn bump(n:usize, acc:rw<u32>[n]@device, x:ro<u8>[n]@device) {
  parallel i in n {
    acc[i] = add_wrap(acc[i], u32(x[i]));
    if x[i] > 3 { acc[i] = 0; }
  }
}
"""
WIDE = "fn wide(n:usize, out:rw<f64>[n]@device) { parallel i in n { out[i] = 1.0; } }\n"
OTHER = "fn other(n:usize, m:usize, out:rw<f32>[n]@device) { parallel i in m { out[i] = 1.0; } }\n"
HOST = "fn host(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = 1; } }\n"


@pytest.mark.parametrize(
    ("source", "said"),
    [
        (SAXPY + "plan saxpy { vector 3; }", "is not one"),  # a chunk is one access, of a power of two
        (SAXPY + "plan saxpy { vector 1; }", "from 2 to 16"),
        (SAXPY + "plan saxpy { vector 32; }", "from 2 to 16"),
        (WIDE + "plan wide { vector 4; }", "at most 16"),  # four f64 are 32 bytes, and a lane moves 16 at once
        (OTHER + "plan other { vector 4; }", "touches none that way"),  # over m, out[i] keeps its guard
        (HOST + "plan host { vector 4; }", "device parallel region"),
        (SAXPY + SAXPY.replace("saxpy", "again") + "plan saxpy { vector 4; fuse 2; }", "one or the other"),
    ],
)
def test_a_vector_plan_is_refused_where_it_would_change_nothing_or_could_not_hold(source, said):
    assert said in refused("E-PLAN", source)["message"]


def test_a_vector_plan_chunks_what_its_lanes_touch_only_at_their_index():
    planned, receipt = compile_source(SAXPY + "plan saxpy { vector 4; }")
    assert "cr::gpu::run_vector<4>(cr::gpu::here(), v_n, cr::gpu::aligned<4>(v_out, v_x, v_y), " in planned
    assert "auto cr_c_x = cr::gpu::Chunk<float, 4>::load(v_x + cr_base);" in planned
    assert "cr::gpu::Chunk<float, 4> cr_c_out{};" in planned  # written at the top of the body, never read: no load
    assert "cr_c_out.store(v_out + cr_base);" in planned and "cr_c_x.store" not in planned
    plain = compile_source(SAXPY)[1]["functions"]["saxpy"]
    assert receipt["functions"]["saxpy"]["plan"] == {"vector": 4}
    assert {k: v for k, v in receipt["functions"]["saxpy"].items() if k != "plan"} == plain


def test_a_chunk_the_body_reads_or_writes_in_a_branch_is_loaded_first():
    planned = compile_source(BUMP + "plan bump { vector 4; block 128; }")[0]
    assert "auto cr_c_acc = cr::gpu::Chunk<std::uint32_t, 4>::load(v_acc + cr_base);" in planned
    assert "auto cr_c_x = cr::gpu::Chunk<std::uint8_t, 4>::load(v_x + cr_base);" in planned  # four bytes, one load
    assert "cr_c_acc.store(v_acc + cr_base);" in planned and planned.rstrip().endswith("}, 128);\n}")


def test_an_array_used_any_other_way_stays_a_pointer_access():
    source = """fn mixed(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { out[i] = x[i] + x[0]; }
}
plan mixed { vector 4; }
"""
    planned = compile_source(source)[0]
    assert "cr::gpu::aligned<4>(v_out)" in planned and "cr_c_x" not in planned  # x is also read at [0]


def test_a_vector_plan_is_its_own_item_in_the_canonical_projection():
    source = SAXPY + "plan saxpy { vector 4; }\n"
    canonical = canonical_source(source)
    assert "plan saxpy { vector 4; }" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical


def test_a_vectored_lane_moves_its_chunks_in_single_wide_accesses_on_the_device(tmp_path):
    """Compiled for sm_120 and never run: the PTX holds one 128-bit load per read chunk and one store per written
    chunk, where the scalar lanes make 32-bit ones."""
    ptx = device_build(tmp_path, compile_source(SAXPY + "plan saxpy { vector 4; }")[0], ptx=True).read_text()
    # CUDA 13 writes a 128-bit access of four floats .v4.b32, and CUDA 12.9 .v4.f32.
    assert len(re.findall(r"ld\.global\.v4\.[bf]32", ptx)) == 2 and len(re.findall(r"st\.global\.v4\.[bf]32", ptx)) == 1


def predicted(source: str, name: str, sizes: dict[str, float]) -> dict:
    p, checker, _ = compile_program(source)
    return model.predict(count(p, checker)[name], packaged("zen4-7800x3d"), sizes)


def test_the_model_prices_a_chunk_as_one_access_for_every_w_indices():
    """saxpy issues five loads, stores and operations per index; chunked, its three arrays cost 3/4 of an access
    each for four indices. It stays bound by the device's memory, so its predicted time does not move."""
    plain = predicted(SAXPY, "saxpy", {"n": 1e7})["parts"][0]
    chunked = predicted(SAXPY + "plan saxpy { vector 4; }", "saxpy", {"n": 1e7})["parts"][0]
    assert chunked["compute_ns"] < plain["compute_ns"] and chunked["memory_ns"] == plain["memory_ns"]
    assert chunked["bound"] == plain["bound"] == "device memory"


def test_the_tuner_tries_only_the_widths_the_checker_allows():
    widths = {row.get("vector", 0) for row in tune(SAXPY, "saxpy", [{"n": 1e7}])["candidates"]}
    assert widths == {0, 2, 4}
    assert {row.get("vector", 0) for row in tune(OTHER, "other", [{"n": 1e7, "m": 1e7}])["candidates"]} == {0}


# Each index writes its own element, so a vectored launch must leave what the scalar lanes leave: over an extent that
# is no multiple of W, where the last indices run one at a time, and from a part one element off a chunk's width,
# where the alignment check sends the whole region to its scalar lanes.
ON_DEVICE = """
fn fill(n:usize, out:rw<u64>[n]@device, x:ro<u64>[n]@device) { parallel i in n { out[i] = x[i] * 3 + u64(i); } }
PLAN
fn main() -> i32 {
  let n:usize = 100003;
  buffer h:u64[n] = zeroed;
  for i in 0..n { h[i] = mul_wrap(u64(i), 2654435761); }
  buffer x:u64[n]@device = zeroed;
  transfer(x, h);
  buffer d:u64[n]@device = zeroed;
  fill(n, d, x);
  buffer back:u64[n] = zeroed;
  transfer(back, d);
  for i in 0..n { if back[i] != h[i] * 3 + u64(i) { return 1; } }
  let m = n - 1;
  fill(m, d[1..n], x[1..n]);
  transfer(back, d);
  for i in 0..m { if back[i + 1] != h[i + 1] * 3 + u64(i) { return 2; } }
  return 0;
}
"""
VECTORS = ["plan fill { vector 2; }", "plan fill { vector 2; block 64; per_lane 8; unroll 2; }"]


@pytest.mark.parametrize("plan", VECTORS)
def test_every_vector_plan_compiles_for_the_device_without_touching_it(tmp_path, plan):
    device_build(tmp_path, compile_source(ON_DEVICE.replace("PLAN", plan))[0], entry="main")


@pytest.mark.parametrize("plan", ["", *VECTORS])
def test_every_vector_plan_computes_what_the_scalar_lanes_compute(tmp_path, plan):
    with on_device():  # runs only under `make gpu`
        done = contract(tmp_path, compile_source(ON_DEVICE.replace("PLAN", plan))[0], "g++", cuda=True)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
