"""The classifier trains a two-layer network on the gradient derive grad writes, then runs it quantized.

The oracle is the same training written again in Python with the backward pass derived by hand, so the generated
gradient, the training loop, quantize and the printed numbers are all held to something that shares none of their
code: forty steps must print the same loss, accuracies and drifts to the six places the program prints. The full
run is checked for what training should achieve and what quantization should cost.
"""

import math
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import SANITIZED, run
from oracles.float_formats import Format, quantize_integer

APP = Path(__file__).resolve().parents[2] / "examples" / "apps" / "classifier"
HIDDEN = 8


def single(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def points(draw, n):
    xs, ys = [], []
    for _ in range(n):
        x0, x1 = 3.0 * next(draw) - 1.5, 3.0 * next(draw) - 1.5
        xs.append((x0, x1))
        ys.append(1.0 if x0 * x0 + x1 * x1 < 1.0 else 0.0)
    return xs, ys


def generator(state: int):
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) % 2**64
        yield (state >> 11) / 9007199254740992.0


def unit(net, j, x0, x1):
    w, c, _, _ = net
    z = w[2 * j] * x0 + w[2 * j + 1] * x1 + c[j]
    grows = math.exp(2.0 * z)
    return grows, 1.0 - 2.0 / (grows + 1.0)


def logit(net, x0, x1):
    z = net[3]
    for j in range(HIDDEN):
        z += net[2][j] * unit(net, j, x0, x1)[1]
    return z


def loss(net, data):
    xs, ys = data
    total = 0.0
    for (x0, x1), y in zip(xs, ys, strict=True):
        z = logit(net, x0, x1)
        total += math.log(1.0 + math.exp(z)) - y * z
    return total / len(ys)


def gradient(net, data):
    """d loss / d parameters, by hand: sigmoid(z) - y at the output, 4e / (e + 1)^2 through each tanh."""
    v = net[2]
    xs, ys = data
    dw, dc, dv, db = [0.0] * 16, [0.0] * HIDDEN, [0.0] * HIDDEN, 0.0
    for (x0, x1), y in zip(xs, ys, strict=True):
        z = logit(net, x0, x1)
        e = math.exp(z)
        dz = (e / (1.0 + e) - y) / len(ys)
        db += dz
        for j in range(HIDDEN):
            grows, t = unit(net, j, x0, x1)
            dv[j] += dz * t
            inner = dz * v[j] * 4.0 * grows / ((grows + 1.0) * (grows + 1.0))
            dw[2 * j] += inner * x0
            dw[2 * j + 1] += inner * x1
            dc[j] += inner
    return dw, dc, dv, db


def accuracy(net, data):
    xs, ys = data
    return sum((logit(net, x0, x1) > 0.0) == (y > 0.5) for (x0, x1), y in zip(xs, ys, strict=True)) / len(ys)


def through(values, top, rounding):
    largest = max(abs(x) for x in values)
    scale = 1.0 if largest == 0.0 else single(largest / top)
    return [single(rounding(single(x), scale) * scale) for x in values]


def quantized(net, eight):
    f8 = Format("f8e4m3")
    if eight:
        rounding, top = (lambda x, s: f8.value(f8.quantize(x, s))), 448.0
    else:
        rounding, top = (lambda x, s: quantize_integer(x, s, -128, 127)), 127.0
    w, c, v, b = net
    return (
        through(w, top, rounding),
        through(c, top, rounding),
        through(v, top, rounding),
        through([b], top, rounding)[0],
    )


def drift(a, b, data):
    return max(abs(logit(a, x0, x1) - logit(b, x0, x1)) for x0, x1 in data[0])


def oracle(steps: int) -> list[float]:
    draw = generator(2026)
    train, test = points(draw, 400), points(draw, 400)
    w, c, v = ([2.0 * next(draw) - 1.0 for _ in range(k)] for k in (16, HIDDEN, HIDDEN))
    net = (w, c, v, 0.0)
    first, last = loss(net, train), 0.0
    for _ in range(steps):  # at a rate of 1, each step subtracts the gradient itself
        last = loss(net, train)
        grads = gradient(net, train)
        net = (*([x - g for x, g in zip(p, d, strict=True)] for p, d in zip(net[:3], grads[:3], strict=True)),
               net[3] - grads[3])  # fmt: skip
    low, tiny = quantized(net, False), quantized(net, True)
    return [first, last, accuracy(net, train), accuracy(net, test), accuracy(low, test), drift(net, low, test),
            accuracy(tiny, test), drift(net, tiny, test)]  # fmt: skip


def printed(stdout: str) -> list[float]:
    return [float(line.rsplit(" ", 1)[1]) for line in stdout.splitlines()]


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_forty_steps_print_what_the_hand_derived_training_says(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = build(load_project(APP), output=tmp_path / "build", cxx=cxx, kind="exe")
    assert record["status"] == "native-built", record.get("stderr", "")[-3000:]
    done = subprocess.run([record["artifact"], "40"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    got, want = printed(done.stdout), oracle(40)
    assert len(got) == 8
    assert all(abs(g - w) <= 2e-6 for g, w in zip(got, want, strict=True)), (got, want)


def test_the_full_run_learns_the_circle_and_quantization_costs_little(tmp_path):
    """Under the address and undefined-behaviour sanitizers: the default 800 steps."""
    done = run(tmp_path, compile_source(load_project(APP).source)[0], *SANITIZED, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    before, after, seen, unseen, low, low_drift, tiny, tiny_drift = printed(done.stdout)
    assert after < 0.1 < before and seen > 0.98 and unseen > 0.97
    assert low >= unseen - 0.01 and tiny >= unseen - 0.02 and low_drift < 0.5 and tiny_drift < 2.0


def test_every_gradient_the_model_uses_is_derived_and_its_row_says_so():
    rows = compile_source(load_project(APP).source)[1]["functions"]
    for name in ("unit", "logit", "sample", "loss"):
        assert f"model.{name}_grad" in rows
    assert "ffi:exp" in rows["model.loss_grad"]["effects"]  # the gradient calls libm too, and says so
    assert "write:d_w" in rows["model.loss_grad"]["effects"]
