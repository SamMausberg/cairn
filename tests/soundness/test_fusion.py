"""`plan f { fuse K; }` runs up to K adjacent regions of f as one traversal (compiler/fusion.py).

Fusing moves a later body's work for index i next to the earlier body's work for the same index. That keeps every
result when the bodies share arrays only at their own index, and when neither can trap: a failed guard aborts the
process, and fusing trapping bodies would let a later body fail before an earlier one. A local array only the
chain touches, each lane at its own index, is held in the lane and never allocated. The checker refuses a fuse
with nothing it may join; where the rules forbid a join, the regions are emitted as they were written.
"""

import os
import shutil
import subprocess

import pytest

from cairn.agent.plans import PlanHost
from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.perf.report import report
from emitted import contract, emit, refused, watched

HEAD = "fn f(n:usize, m:usize, out:rw<f64>[n], x:ro<f64>[n], y:rw<f64>[n], z:rw<f64>[m]) {\n"
PIPE = "  buffer t:f64[n] = zeroed;\n  parallel i in n { t[i] = 2.0 * x[i]; }\n  parallel j in n { out[j] = t[j] + 1.0; }\n"
TWO = "  parallel i in n { out[i] = x[i] * 2.0; }\n  parallel i in n { y[i] = out[i] + x[i]; }\n"


def planned(body: str, plan: str = "fuse 2;") -> str:
    return HEAD + body + "}\nplan f { " + plan + " }\n"


@pytest.mark.parametrize(
    "body",
    [
        # a body that can trap: the checked + may overflow
        "  buffer t:u64[n] = zeroed;\n  parallel i in n { t[i] = u64(i) + 1; }\n  parallel j in n { y[j] = f64(t[j]); }\n",
        # one extent against another
        "  parallel i in n { out[i] = x[i]; }\n  parallel j in m { z[j] = 1.0; }\n",
        # a statement between the two
        "  parallel i in n { out[i] = x[i]; }\n  let k:f64 = 3.0;\n  parallel j in n { y[j] = out[j] * k; }\n",
        # the later body reads an element an earlier lane writes, not its own
        "  if n == 0 { return; }\n  parallel i in n { out[i] = x[i]; }\n  parallel j in n { y[j] = out[0]; }\n",
        # a loop that need not finish
        "  parallel i in n { out[i] = x[i]; }\n"
        "  parallel j in n { let mut k:u64 = 0; while k < 3 { k = add_wrap(k, 1); } y[j] = out[j]; }\n",
        # only one region
        "  parallel i in n { out[i] = x[i]; }\n",
        # a callee that can trap
        "  parallel i in n { out[i] = x[i]; }\n  parallel j in n { y[j] = f64(up(u64(j))); }\n",
    ],
    ids=["trap", "extents", "between", "neighbour", "while", "one", "callee"],
)
def test_a_fuse_with_nothing_it_may_join_is_refused(body):
    refused("E-PLAN", "fn up(v:u64) -> u64 = v + 1;\n" + planned(body))


@pytest.mark.parametrize("value", ["1", "17", "0"])
def test_a_fuse_joins_two_to_sixteen_regions(value):
    refused("E-PLAN", planned(TWO, f"fuse {value};"))


def test_fused_regions_are_one_region_whose_scratch_lives_in_the_lane():
    cpp, receipt = compile_source(planned(PIPE))
    body = cpp.split("void ci_f(", 2)[2]
    assert body.count("cr::par::run(") == 1 and "double s_t{};" in body and "cr::Buffer" not in body
    assert "s_t = (2.0 * v_x[v_i]);" in body and "const std::size_t v_j = v_i;" in body
    fused = receipt["functions"]["f"]
    assert fused["fused"] == [{"line": 3, "regions": 2, "scratch_in_lanes": ["t"]}]
    plain = compile_source(HEAD + PIPE + "}\n")[1]["functions"]["f"]
    assert {k: v for k, v in fused.items() if k not in {"plan", "fused"}} == plain  # the row and the guards stay


def test_scratch_read_after_the_chain_stays_in_memory():
    cpp, receipt = compile_source(planned(PIPE + "  y[0] = t[0];\n"))
    body = cpp.split("void ci_f(", 2)[2]
    assert body.count("cr::par::run(") == 1 and "cr::Buffer<double>" in body and "s_t" not in body
    assert receipt["functions"]["f"]["fused"][0]["scratch_in_lanes"] == []


def test_regions_that_share_a_binder_need_no_alias():
    body = compile_source(planned(TWO))[0].split("void ci_f(", 2)[2]
    assert body.count("cr::par::run(") == 1 and "const std::size_t v_i = v_i" not in body


THREE = TWO + "  parallel k in n { out[k] = y[k] - x[k]; }\n"


@pytest.mark.parametrize(("plan", "runs"), [("fuse 2;", 2), ("fuse 3;", 1), ("fuse 16;", 1)])
def test_a_fuse_joins_at_most_as_many_regions_as_it_says(plan, runs):
    assert compile_source(planned(THREE, plan))[0].split("void ci_f(", 2)[2].count("cr::par::run(") == runs


def test_a_callee_whose_every_guard_was_discharged_may_run_in_a_fused_body():
    mix = "fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);\n"
    body = "  parallel i in n { out[i] = f64(mix(u64(i))); }\n  parallel j in n { y[j] = out[j] + x[j]; }\n"
    cpp, receipt = compile_source(mix + planned(body))
    assert receipt["functions"]["f"]["fused"][0]["regions"] == 2 and "cf_mix(" in cpp.split("void ci_f(", 2)[2]


ENERGY = """fn energy(n:usize, x:ro<f64>[n]) -> f64 {
  buffer sq:f64[n] = zeroed;
  parallel i in n { sq[i] = x[i] * x[i]; }
  let e = reduce + for i in n yield sq[i];
  return e;
}
"""
DIGEST = """fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);
fn digest(n:usize, keys:ro<u64>[n]) -> u64 {
  buffer h:u64[n] = zeroed;
  parallel i in n { h[i] = mix(keys[i]); }
  let d = reduce add_wrap parallel j in n yield h[j];
  return d;
}
"""


def test_a_host_reduce_may_end_a_chain_and_fold_what_the_bodies_made():
    cpp, receipt = compile_source(ENERGY + "plan energy { fuse 2; }\n")
    body = cpp.split("double ci_energy(", 2)[2]
    assert "cr::par::run(" not in body and "cr::Buffer" not in body  # one in-order fold, its scratch in each step
    assert "double s_sq{};" in body and "b = s_sq;" in body
    assert receipt["functions"]["energy"]["fused"] == [
        {"line": 3, "regions": 2, "scratch_in_lanes": ["sq"], "into": "reduce"}
    ]
    pooled = compile_source(DIGEST + "plan digest { fuse 2; }\n")[0].split("ci_digest(", 2)[2]
    assert "cr::par::reduce<" in pooled and "const std::size_t v_i = v_j;" in pooled and "cr::par::run(" not in pooled


def test_a_device_reduce_stays_apart():  # CUB may evaluate one index's value more than once
    device = """fn dev(n:usize, x:ro<f32>[n]@device) -> f32 {
  buffer t:f32[n]@device = zeroed;
  parallel i in n { t[i] = x[i] * 2.0; }
  let s = reduce + for i in n yield t[i];
  return s;
}
plan dev { fuse 2; }
"""
    refused("E-PLAN", device)


def test_a_chain_into_a_fold_is_priced_where_it_runs():
    sizes = [{"n": 1e7}]
    fold = report(ENERGY + "plan energy { fuse 2; }\n", sizes, {"energy"})["functions"]["energy"]
    assert fold["regions"] == [] and fold["sequential"]["bytes_read"] == {"x": "8*n"}  # one thread, no scratch
    pooled = report(DIGEST + "plan digest { fuse 2; }\n", sizes, {"digest"})["functions"]["digest"]
    assert [r["kind"] for r in pooled["regions"]] == ["pooled"]


def test_conservative_emission_keeps_every_region_apart():
    cpp, _ = compile_source(planned(PIPE), keep_guards=True)  # the reference build a differential run compares with
    assert cpp.split("void ci_f(", 2)[-1].count("cr::par::run(") == 2 and "cr::Buffer<double>" in cpp
    quiet = planned("  parallel i in n { let a = 1.0; }\n  parallel j in n { let b = 2.0; }\n")  # no guard at all
    assert compile_source(quiet, keep_guards=True)[0].count("cr::par::run(") == 2


def test_a_fuse_is_its_own_item_in_the_canonical_projection():
    source = planned(PIPE, "fuse 2; grain 64;")
    canonical = canonical_source(source)
    assert "plan f { grain 64; fuse 2; }" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0]


def test_the_model_prices_a_chain_as_one_region_without_its_scratch():
    fused = report(planned(PIPE), [{"n": 1e8, "m": 1}], {"f"})["functions"]["f"]
    plain = report(HEAD + PIPE + "}\n", [{"n": 1e8, "m": 1}], {"f"})["functions"]["f"]
    assert len(fused["regions"]) == 1 and len(plain["regions"]) == 2
    assert "t" not in fused["regions"][0]["per_index"]["bytes_read"]
    assert fused["predictions"][0]["ns"] < plain["predictions"][0]["ns"]


def test_tune_tries_fuse_counting_each_plan_as_it_would_be_written():
    from cairn.perf.tune import tune

    for source in (HEAD + PIPE + "}\n", planned(PIPE)):  # unplanned, and already fused: the same ranking either way
        found = tune(source, "f", [{"n": 1e8, "m": 1}])
        best, plain = found["chosen"], next(r for r in found["candidates"] if r["plan"] == "(no plan for f)")
        assert best.get("fuse") == 2 and best["predicted_ns"] < plain["predicted_ns"]
    assert tune(planned(PIPE), "f", [{"n": 1e8, "m": 1}])["current"] == "plan f { fuse 2; }"


def test_a_plan_session_offers_fuse_and_admits_it():
    host = PlanHost()
    packet = host.open(HEAD + PIPE + "}\n", "f")
    assert "fuse" in packet["items"] and packet["items"]["fuse"]["least"] == 2
    admitted = host.respond({"protocol": "cairn.plan/1", "session": packet["session"], "items": {"fuse": 2}})
    assert admitted["status"] == "admitted" and "plan f { fuse 2; }" in admitted["plan"]
    single = host.open(HEAD + "  parallel i in n { out[i] = x[i]; }\n}\n", "f")
    assert "fuse" not in single["items"]  # nothing to join
    again = host.open(planned(PIPE), "f")  # a fused function still has its two regions to plan
    assert again["current"] == {"fuse": 2} and "fuse" in again["items"]


# Floats, wrapping integers and a quiet callee, each fused; main recomputes every element in order and compares.
PROGRAM = """
fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);

fn pipe(n:usize, out:rw<f64>[n], x:ro<f64>[n], a:f64, b:f64) {
  buffer t:f64[n] = zeroed;
  parallel i in n { t[i] = a * x[i] + sqrt(x[i]); }
  parallel j in n { out[j] = t[j] * b - x[j]; }
}

fn hashes(n:usize, out:rw<u64>[n], keys:ro<u64>[n], seen:rw<u64>[n]) {
  parallel i in n { seen[i] = mix(keys[i]); }
  parallel i in n { out[i] = seen[i] ^ keys[i]; }
  parallel k in n { seen[k] = add_wrap(seen[k], out[k]); }
}

fn energy(n:usize, x:ro<f64>[n]) -> f64 {
  buffer sq:f64[n] = zeroed;
  parallel i in n { sq[i] = x[i] * x[i]; }
  let e = reduce + for i in n yield sq[i];
  return e;
}

fn digest(n:usize, keys:ro<u64>[n]) -> u64 {
  buffer h:u64[n] = zeroed;
  parallel i in n { h[i] = mix(keys[i]); }
  let d = reduce add_wrap parallel j in n yield h[j];
  return d;
}

PLAN

fn main() -> i32 {
  let n:usize = 200003;
  buffer x:f64[n] = zeroed;
  buffer out:f64[n] = zeroed;
  buffer keys:u64[n] = zeroed;
  buffer hashed:u64[n] = zeroed;
  buffer seen:u64[n] = zeroed;
  for i in 0..n {
    x[i] = f64(i) * 0.25;
    keys[i] = mul_wrap(u64(i), 2654435761);
  }
  pipe(n, out, x, 1.5, 0.75);
  hashes(n, hashed, keys, seen);
  let mut e:f64 = 0.0;
  let mut d:u64 = 0;
  for i in 0..n {
    let t = 1.5 * x[i] + sqrt(x[i]);
    if out[i] != t * 0.75 - x[i] { return 1; }
    let s = mix(keys[i]);
    if hashed[i] != (s ^ keys[i]) || seen[i] != add_wrap(s, s ^ keys[i]) { return 2; }
    e += x[i] * x[i];                                  // the in-order sum the fold must give, bit for bit
    d = add_wrap(d, s);
  }
  let folded = energy(n, x);                             // it may allocate its scratch: a statement of its own
  let digested = digest(n, keys);
  if folded != e { return 3; }
  if digested != d { return 4; }
  return 0;
}
"""
PLANS = ["", "plan pipe { fuse 2; }\nplan hashes { fuse 3; }\nplan energy { fuse 2; }\nplan digest { fuse 2; }",
         "plan pipe { fuse 2; grain 1; lanes 3; }\nplan hashes { fuse 2; grain 4096; }"]  # fmt: skip


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("plan", PLANS)
def test_a_fused_chain_computes_what_the_regions_compute(tmp_path, cxx, plan):
    cpp = compile_source(PROGRAM.replace("PLAN", plan))[0]
    done = contract(tmp_path, cpp, cxx, env={**os.environ, "CAIRN_LANES": "8"})
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_fused_chain_is_clean_under_thread_sanitizer(tmp_path):
    cpp = compile_source(PROGRAM.replace("PLAN", PLANS[1]))[0]
    assert cpp.count("cr::par::run(") == 2  # pipe's two regions and hashes' three are one region each
    done = watched(tmp_path, cpp, "clang++", "thread")
    assert done.returncode == 0, done.stderr[-4000:]
    assert "WARNING: ThreadSanitizer" not in done.stderr


DEVICE = """
fn smooth(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  buffer t:f32[n]@device = zeroed;
  parallel i in n { t[i] = x[i] * 0.5; }
  parallel j in n { out[j] = t[j] + x[j]; }
}
plan smooth { fuse 2; block 128; }
"""


def test_a_fused_device_chain_is_one_launch_without_its_scratch():
    cpp, receipt = compile_source(DEVICE)
    assert cpp.count("cr::gpu::launch(") == 1 and "}, 128);" in cpp and "float s_t{};" in cpp
    assert "gpu::Buffer" not in cpp and "gpu::Buffer" in compile_source(DEVICE.replace("fuse 2; ", ""))[0]
    assert receipt["functions"]["smooth"]["fused"] == [{"line": 4, "regions": 2, "scratch_in_lanes": ["t"]}]


@pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")
def test_a_fused_device_chain_compiles_for_the_device_without_touching_it(tmp_path):
    source, _ = emit(tmp_path, compile_source(DEVICE)[0], entry=None)
    command = ["nvcc", "-std=c++20", "-O3", "--fmad=false", "-arch=sm_120", "--extended-lambda",
               "--expt-relaxed-constexpr", "-Werror", "all-warnings", "-x", "cu", "-c", source, "-o", str(tmp_path / "p.o")]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
