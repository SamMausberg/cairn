"""derive grad: reverse-mode differentiation generated as ordinary CAIRN and checked like any other code.

Two oracles stand outside the generator. Central finite differences of the compiled function itself, at random
points away from every kink, and, where the system Python has torch, torch's autograd over the same formulas
written again in torch. Both run against the compiled gradients under both compilers.
"""

import ctypes as C
import json
import math
import random
import shutil
import subprocess

import pytest

from cairn.agent.projection import canonical_source, expanded_source
from cairn.compiler.cairnc import compile_source
from cairn.projects.toolchain import command
from emitted import WARNINGS, emit, refused, run, sanitized, watched

MODEL = """
fn square(x:f64) -> f64 = x * x;

fn relu(x:f64) -> f64 {
  if x < 0.0 { return 0.0; }
  return x;
}

fn poly(x:f64, y:f64) -> f64 {
  let a = x * y;
  let b = square(a) - sqrt(y) / x;
  return relu(b) + abs(x - 3.0) + floor(x) * y;
}

fn energy(n:usize, w:ro<f64>[n], x:ro<f64>[n], bias:f64) -> f64 {
  let dot = reduce + for i in n yield w[i] * x[i];
  let shifted = dot + bias;
  return square(shifted) / f64(n) - f64(f32(bias)) * 0.5;
}

fn scale(n:usize, a:f64, x:ro<f64>[n], out:rw<f64>[n]) {
  parallel i in n {
    let t = a * x[i];
    out[i] = t * x[i] - t / (1.0 + x[i] * x[i]);
  }
}

fn shift(n:usize, x:ro<f64>[n], out:rw<f64>[n]) {
  for i in 0..n { out[i] = x[(i + 1) % n] - x[i]; }
}

derive grad for square;
derive grad for relu;
derive grad for poly;
derive grad[w, bias] for energy;
derive grad for scale;
derive grad for shift;
"""

D = C.c_double
P = C.POINTER(D)


@pytest.fixture(scope="module", params=["g++", "clang++"])
def lib(request, tmp_path_factory):
    if not shutil.which(request.param):
        pytest.skip(f"{request.param} unavailable")
    directory = tmp_path_factory.mktemp(request.param.replace("+", "p"))
    source, artifact = emit(directory, compile_source(MODEL)[0], entry=None)
    subprocess.run(command(request.param, source, artifact + ".so", kind="library"), check=True, timeout=240)
    lib = C.CDLL(artifact + ".so")
    lib.cf_poly.restype = lib.cf_poly_grad.restype = lib.cf_energy.restype = lib.cf_energy_grad.restype = D
    lib.cf_poly.argtypes = [D, D]
    lib.cf_poly_grad.argtypes = [D, D, D, P, P]
    lib.cf_energy.argtypes = [C.c_size_t, P, P, D]
    lib.cf_energy_grad.argtypes = [C.c_size_t, P, P, D, D, P, P]
    lib.cf_scale.argtypes = [C.c_size_t, D, P, P]
    lib.cf_scale_grad.argtypes = [C.c_size_t, D, P, P, P, P, P]
    lib.cf_shift.argtypes = [C.c_size_t, P, P]
    lib.cf_shift_grad.argtypes = [C.c_size_t, P, P, P, P]
    return lib


def array(values):
    return (D * len(values))(*values)


def close(got: float, want: float, tolerance: float = 1e-6) -> bool:
    return abs(got - want) <= tolerance * max(1.0, abs(want))


def central(f, x: float, h: float = 1e-6) -> float:
    return (f(x + h) - f(x - h)) / (2 * h)


def poly_points(rng):
    """Points away from the kinks: relu at b = 0, abs at x = 3, floor at integers."""
    while len(points := getattr(poly_points, "cache", [])) < 60:
        x, y = rng.uniform(0.3, 5.0), rng.uniform(0.2, 3.0)
        b = (x * y) ** 2 - math.sqrt(y) / x
        if abs(b) > 1e-3 and abs(x - 3.0) > 1e-3 and abs(x - round(x)) > 1e-3:
            points.append((x, y))
            poly_points.cache = points
    return points


def test_a_scalar_gradient_agrees_with_central_differences(lib):
    for x, y in poly_points(random.Random(1)):
        dx, dy = D(0.0), D(0.0)
        value = lib.cf_poly_grad(x, y, 1.0, C.byref(dx), C.byref(dy))
        assert value == lib.cf_poly(x, y)  # the gradient returns the function's own result
        assert close(dx.value, central(lambda t: lib.cf_poly(t, y), x), 1e-5), (x, y)
        assert close(dy.value, central(lambda t: lib.cf_poly(x, t), y), 1e-5), (x, y)


def test_the_seed_scales_and_the_adjoints_accumulate(lib):
    dx, dy = D(10.0), D(0.0)
    lib.cf_poly_grad(1.7, 0.9, 1.0, C.byref(dx), C.byref(dy))
    once = dx.value - 10.0
    lib.cf_poly_grad(1.7, 0.9, 2.5, C.byref(dx), C.byref(dy))
    assert close(dx.value, 10.0 + 3.5 * once, 1e-12)


def test_a_reduction_gradient_covers_only_the_parameters_named(lib):
    rng = random.Random(2)
    n = 17
    w, x = [rng.uniform(-1, 1) for _ in range(n)], [rng.uniform(-1, 1) for _ in range(n)]
    bias = 0.3
    dw, dbias = array([0.0] * n), D(0.0)
    lib.cf_energy_grad(n, array(w), array(x), bias, 1.0, dw, C.byref(dbias))
    for k in range(n):

        def at(t, k=k):
            moved = list(w)
            moved[k] = t
            return lib.cf_energy(n, array(moved), array(x), bias)

        assert close(dw[k], central(at, w[k]))
    # bias passes through f32, whose rounding a step of 1e-6 would see; the rest is quadratic in bias, where a
    # central difference of any step is exact
    assert close(dbias.value, central(lambda t: lib.cf_energy(n, array(w), array(x), t), bias, 1e-3), 1e-4)


def test_a_region_s_gradient_writes_each_lane_s_element_and_gathers_the_shared_scalar(lib):
    rng = random.Random(3)
    n = 40000  # past the pool's cutoff, so the backward region runs on lanes
    x = [rng.uniform(-2, 2) for _ in range(n)]
    seed = [rng.uniform(-1, 1) for _ in range(n)]
    a = 0.7
    out, dx, da = array([0.0] * n), array([0.0] * n), D(0.0)
    lib.cf_scale_grad(n, a, array(x), out, C.byref(da), dx, array(seed))
    for i in range(0, n, 997):
        v = x[i]
        want = seed[i] * (2 * a * v - a * (1 - v * v) / (1 + v * v) ** 2)
        assert close(dx[i], want, 1e-12)
        assert close(out[i], a * v * v - a * v / (1 + v * v), 1e-12)
    want_a = sum(s * (v * v - v / (1 + v * v)) for s, v in zip(seed, x, strict=True))
    assert close(da.value, want_a, 1e-9)


def test_a_sequential_loop_may_read_other_elements(lib):
    n = 9
    x = [float(k * k) for k in range(n)]
    seed = [1.0 + k for k in range(n)]
    dx, out = array([0.0] * n), array([0.0] * n)
    lib.cf_shift_grad(n, array(x), out, dx, array(seed))
    for k in range(n):  # out[i] = x[i+1] - x[i]: x[k] gets seed[k-1] and loses seed[k]
        assert dx[k] == seed[(k - 1) % n] - seed[k]


TORCH = """
import json, sys, torch
cases = json.loads(sys.stdin.read())
out = []
for x, y in cases["poly"]:
    x, y = torch.tensor(x, dtype=torch.float64, requires_grad=True), torch.tensor(y, dtype=torch.float64, requires_grad=True)
    a = x * y
    b = a * a - torch.sqrt(y) / x
    value = torch.relu(b) + torch.abs(x - 3.0) + torch.floor(x) * y
    value.backward()
    out.append([x.grad.item(), y.grad.item()])
w = torch.tensor(cases["w"], dtype=torch.float64, requires_grad=True)
bias = torch.tensor(cases["bias"], dtype=torch.float64, requires_grad=True)
x = torch.tensor(cases["x"], dtype=torch.float64)
shifted = (w * x).sum() + bias
energy = shifted * shifted / len(cases["x"]) - bias.to(torch.float32).to(torch.float64) * 0.5
energy.backward()
print(json.dumps({"poly": out, "w": w.grad.tolist(), "bias": bias.grad.item()}))
"""


def torch_python():
    python = shutil.which("python3", path="/usr/bin")
    probe = subprocess.run([python, "-c", "import torch"], capture_output=True) if python else None
    if probe is None or probe.returncode:
        pytest.skip("the system python3 has no torch")
    return python


def test_torch_autograd_agrees(lib):
    python = torch_python()
    rng = random.Random(4)
    points = poly_points(random.Random(1))[:20]
    n = 11
    w, x, bias = [rng.uniform(-1, 1) for _ in range(n)], [rng.uniform(-1, 1) for _ in range(n)], -0.4
    cases = {"poly": points, "w": w, "x": x, "bias": bias}
    done = subprocess.run([python, "-c", TORCH], input=json.dumps(cases), capture_output=True, text=True, timeout=300,
                          env={"CUDA_VISIBLE_DEVICES": "", "PATH": "/usr/bin"})  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    want = json.loads(done.stdout)
    for (px, py), (tx, ty) in zip(points, want["poly"], strict=True):
        dx, dy = D(0.0), D(0.0)
        lib.cf_poly_grad(px, py, 1.0, C.byref(dx), C.byref(dy))
        assert close(dx.value, tx, 1e-12) and close(dy.value, ty, 1e-12)
    dw, dbias = array([0.0] * n), D(0.0)
    lib.cf_energy_grad(n, array(w), array(x), bias, 1.0, dw, C.byref(dbias))
    assert all(close(g, t, 1e-12) for g, t in zip(dw, want["w"], strict=True))
    assert close(dbias.value, want["bias"], 1e-12)


CHECKED = """
fn cube(x:f64) -> f64 = x * x * x;
fn spread(n:usize, a:f64, x:ro<f64>[n], out:rw<f64>[n]) { parallel i in n { out[i] = a * x[i] * x[i]; } }
derive grad for cube;
derive grad for spread;

fn main() -> i32 {
  let mut d:f64 = 0.0;
  let v = cube_grad(2.0, 1.0, d);
  if v != 8.0 || d != 12.0 { return 1; }
  let n:usize = 40000;
  buffer x:f64[n] = zeroed;
  buffer out:f64[n] = zeroed;
  buffer dx:f64[n] = zeroed;
  buffer seed:f64[n] = zeroed;
  for i in 0..n { x[i] = f64(i % 7); seed[i] = 1.0; }
  let mut da:f64 = 0.0;
  spread_grad(n, 0.5, x, out, da, dx, seed);
  let mut want:f64 = 0.0;
  for i in 0..n {
    if dx[i] != x[i] || out[i] != 0.5 * x[i] * x[i] { return 2; }
    want += x[i] * x[i];
  }
  if da != want { return 3; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_gradients_run_clean_under_the_sanitizers(tmp_path, cxx):
    done = run(tmp_path, compile_source(CHECKED)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_the_backward_region_is_free_of_races(tmp_path):
    done = watched(tmp_path, compile_source(CHECKED)[0], "clang++", "thread")
    assert done.returncode == 0, done.stderr[-3000:]


def test_the_gradient_reads_as_ordinary_code_and_projects_to_the_same_program():
    shown = expanded_source(MODEL)
    assert (
        "fn scale_grad(n:usize, a:f64, x:ro<f64>[n]@host, out:rw<f64>[n]@host, d_a:rw<f64>, d_x:rw<f64>[n]@host, d_out:ro<f64>[n]@host)"
        in shown
    )
    assert (
        "fn energy_grad(n:usize, w:ro<f64>[n]@host, x:ro<f64>[n]@host, bias:f64, seed:f64, d_w:rw<f64>[n]@host, d_bias:rw<f64>) -> f64"
        in shown
    )
    assert compile_source(canonical_source(MODEL))[0] == compile_source(MODEL)[0]


def test_the_row_says_what_the_gradient_writes():
    rows = compile_source(MODEL)[1]["functions"]
    assert "write:d_x" in rows["poly_grad"]["effects"] and "write:d_y" in rows["poly_grad"]["effects"]
    assert "par:host" in rows["scale_grad"]["effects"] and "write:d_x" in rows["scale_grad"]["effects"]
    assert "read:d_out" in rows["scale_grad"]["effects"]


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-GRAD", "fn f(n:usize) -> usize = n;\nderive grad for f;"),  # nothing to differentiate
        ("E-GRAD", "fn f(x:f64, k:u32) -> f64 = x;\nderive grad[k] for f;"),  # not a float parameter
        ("E-GRAD", "derive grad for missing;"),
        ("E-GRAD", "fn f[T:copy](x:f64) -> f64 = x;\nderive grad for f;"),
        ("E-GRAD-FORM", "fn f(x:f64) -> f64 { let mut s = x; s = s * x; return s; }\nderive grad for f;"),
        ("E-GRAD-FORM", "fn f(x:f64) -> f64 { while x > 1.0 { return x; } return x; }\nderive grad for f;"),
        (
            "E-GRAD-FORM",  # an element loop has no index to accumulate the adjoint at
            "fn f(n:usize, x:ro<f64>[n]) -> f64 { let mut s:f64 = 0.0; for v in x { s += v; } return s; }\n"
            "derive grad for f;",
        ),
        (
            "E-GRAD-FORM",
            "fn f(n:usize, x:ro<f64>[n]) -> f64 { let p = reduce * for i in n yield x[i]; return p; }\n"
            "derive grad for f;",
        ),
        (
            "E-GRAD-FORM",
            "fn f(n:usize, x:ro<f64>[n], out:rw<f64>[n]) { for i in 0..n { out[i] = out[i] + x[i]; } }\n"
            "derive grad for f;",
        ),
        (
            "E-GRAD-FORM",
            "fn f(n:usize, x:ro<f64>[n], out:rw<f64>[n]) { for i in 0..n { out[0] = x[i]; } }\nderive grad for f;",
        ),
        (
            "E-GRAD-RACE",
            "fn f(n:usize, x:ro<f64>[n], out:rw<f64>[n]) { parallel i in n { out[i] = x[(i + 1) % n]; } }\n"
            "derive grad for f;",
        ),
        ("E-GRAD-CALL", "fn g(x:f64) -> f64 = x;\nfn f(x:f64) -> f64 = g(x) * 2.0;\nderive grad for f;"),
        (
            "E-GRAD-CALL",
            "fn g(x:f64, y:f64) -> f64 = x * y;\nfn f(x:f64) -> f64 = g(1.0, x);\n"
            "derive grad[x] for g;\nderive grad for f;",
        ),
        ("E-DERIVE-COLLISION", "fn f(x:f64, d_x:f64) -> f64 = x * d_x;\nderive grad[x] for f;"),
        ("E-DERIVE-COLLISION", "fn f(x:f64) -> f64 = x;\nfn f_grad() -> f64 = 1.0;\nderive grad for f;"),
    ],
)
def test_what_derive_grad_refuses(code, source):
    refused(code, source)


def test_a_program_s_own_recipe_named_grad_wins():
    source = "struct P { x:f64; }\nrecipe grad for R { fn tagged(r:ro<R>) -> f64 = 1.0; }\nderive grad for P;\n"
    assert "cf_tagged" in compile_source(source)[0]


DEVICE = """
fn glow(n:usize, x:ro<f32>[n]@device, out:rw<f32>[n]@device, gain:f32) {
  parallel i in n { out[i] = gain * x[i] * x[i]; }
}
derive grad[x] for glow;
"""


def test_a_device_region_s_gradient_compiles_for_the_device(tmp_path):
    if not shutil.which("nvcc"):
        pytest.skip("nvcc unavailable")
    source, artifact = emit(tmp_path, compile_source(DEVICE)[0], entry=None)
    line = [f for f in command("g++", source, artifact + ".o", cuda=True) if f not in {"-arch=native", "-shared"}]
    line[line.index("-o") : line.index("-o")] = ["-arch=sm_120", "-c"]  # a named architecture: nothing asks the device
    done = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]  # compiled only; device code runs under `make gpu` alone


def test_a_device_region_does_not_gather_a_shared_scalar_on_the_host():
    refused("E-GRAD-FORM", DEVICE.replace("derive grad[x] for glow;", "derive grad for glow;"))
