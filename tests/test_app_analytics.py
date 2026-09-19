"""The columnar analytics engine: one source tree, a host configuration and a device one.

The application judges itself — every query is answered twice and `main` returns 0 only when the
two answers agree — so most assertions here are "it built, it ran and it agreed with itself".
What this file adds is what a program cannot check about itself: the effect rows, the recipes a
receipt pins, that every template of the program certifies against its bounds, that an
incremental rebuild after a body-only edit compiles exactly one unit, and that the host path is
clean under Address and UndefinedBehavior sanitizers.

The device configuration is `gpu.toml`, which replaces `src/main.cairn` with
`src/device_main.cairn`: any @device view sends the whole program through nvcc, so the host-only
build must not contain that module. A manifest is named by its path (`cairn run
examples/apps/analytics/gpu.toml`); the tests that need a GPU skip when nvcc or the device node
is missing.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from cairn.build import build
from cairn.cairnc import RUNTIME_FILES, certify_templates, compile_source
from cairn.codegen import mangle
from cairn.project import load_project

APP = Path(__file__).resolve().parents[1] / "examples" / "apps" / "analytics"
ENTRY = "analytics.main.main"


def device_missing():
    if not shutil.which("nvcc"):
        return "nvcc is not installed"
    if not Path("/dev/nvidiactl").exists():
        return "no CUDA device node"
    return None


def copied(tmp_path):
    """A private copy of the app, so a test may edit its source."""
    target = tmp_path / "analytics"
    shutil.copytree(APP, target, ignore=shutil.ignore_patterns("build"))
    return target


@pytest.fixture(scope="module")
def host_receipt():
    return compile_source(load_project(APP).source)[1]


def test_both_configurations_typecheck(tmp_path, host_receipt):
    """The device half typechecks wherever the compiler runs; only building it needs nvcc."""
    assert host_receipt["function_count"] > 0
    assert all(name.startswith("std.") for name in host_receipt["uninstantiated_templates"])
    assert "cuda" not in compile_source(load_project(APP).source, "", (ENTRY,))[1]["requires"]
    device = compile_source(load_project(APP / "gpu.toml").source)[1]
    assert "analytics.device.notional_of" in device["functions"]  # `device` is a module name now
    assert "cuda" in device["requires"]


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_host_path_agrees_with_itself(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = build(load_project(APP), output=tmp_path / "build", cxx=cxx, timeout=240)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert "every cross-check passed on the host" in done.stdout
    assert "rows ingested     1000000" in done.stdout


def test_the_costs_are_in_the_rows(host_receipt):
    rows = host_receipt["functions"]
    lanes = set(rows["analytics.query.map_par[u64, u64]"]["effects"])
    assert {"par:host", "lane:f", "indirect_call"} <= lanes  # the lanes call the caller's closure
    assert "par:host" in rows["analytics.query.notional_par"]["effects"]
    assert "par:host" not in rows["analytics.query.notional_loop"]["effects"]
    tasks = set(rows["analytics.query.venue_sums_tasks"]["effects"])
    assert {"spawn", "join", "alloc", "free"} <= tasks  # four tasks over one scratch buffer
    assert "dispatch" in rows["analytics.agg.run_dyn"]["effects"]
    assert "dispatch" not in rows["analytics.agg.run_static[analytics.agg.SumAgg]"]["effects"]
    assert {"io", "ffi:write", "alloc"} <= set(rows["analytics.table.write_dataset"]["effects"])
    assert rows["analytics.cols.chain"]["effects"] == []  # the helper the recipes splice in is pure
    assert rows["analytics.agg.counting"]["effects"] == []  # and so is the one `folded` splices
    assert set(rows["analytics.schema.Trade_key"]["effects"]) <= {"read:row", "trap"}
    assert "trap" in rows["analytics.schema.Trade_total_qty"]["effects"]  # checked `reduce +`


def test_the_generated_implementations_are_ordinary_implementations(host_receipt):
    """Three recipes write `impl`s here: std.derived's eq and ord, and the app's own folded/1."""
    rows = host_receipt["functions"]
    assert rows["analytics.schema.std.core.Ord.analytics.schema.Trade.less"]["effects"] == ["read:a", "read:b"]
    assert rows["analytics.schema.std.core.Eq.analytics.schema.Trade.same"]["effects"] == ["read:a", "read:b"]
    assert "std.sort.sort[analytics.schema.Trade]" in rows  # driven by the derived Ord
    for agg in ["SumAgg", "MaxAgg", "CountAgg"]:
        member = f"analytics.agg.Aggregator.analytics.agg.{agg}.absorb"
        assert rows[member]["effects"] == ["read:self", "write:self"]


def test_the_receipt_pins_every_generator(host_receipt):
    assert set(host_receipt["recipes"]) == {
        "analytics.agg.folded",
        "analytics.cols.columns",
        "analytics.cols.stats",
        "analytics.cols.unrolled",
        "analytics.cols.device_columns",
        "std.derived.eq",
        "std.derived.hash",
        "std.derived.ord",
        "std.wire.wire",
    }
    assert all(len(digest) == 64 for digest in host_receipt["recipes"].values())
    derived = {(d["recipe"], d["for"], tuple(d["naturals"])) for d in host_receipt["derivations"]}
    assert ("cols.columns", "Trade", ()) in derived
    assert ("cols.columns", "sensor.Reading", ()) in derived  # another module's record
    assert ("cols.unrolled", "Trade", (4,)) in derived and ("cols.unrolled", "sensor.Reading", (8,)) in derived
    assert ("ord", "Trade", ()) in derived and ("eq", "Trade", ()) in derived
    assert ("folded", "SumAgg", ("add_wrap",)) in derived  # a recipe applied to a function name
    assert ("folded", "CountAgg", ("counting",)) in derived
    assert host_receipt["wire_derivations"] == ["analytics.sensor.Reading", "analytics.schema.Trade"]
    generated = host_receipt["functions"]
    assert {"analytics.schema.Trade_summarize", "analytics.schema.Reading_summarize"} <= set(generated)


@pytest.mark.parametrize("cxx", ["clang++"])
def test_a_body_only_edit_recompiles_one_unit(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    root = copied(tmp_path)
    out = root / "build"

    def units():
        record = build(load_project(root), output=out, cxx=cxx, timeout=240, incremental=True)
        assert record["status"] == "native-built", record.get("stderr", "")[:4000]
        return {unit["unit"]: unit["reused"] for unit in record["units"]}

    first = units()
    assert first["analytics_query.cpp"] is False and first["analytics_main.cpp"] is False
    assert all(units().values())  # nothing changed: nothing compiles
    query = root / "src" / "query.cairn"
    query.write_text(
        query.read_text().replace("let slot = usize(venue[i]) % bins;", "let slot = usize(venue[i] % u8(bins));")
    )
    after = units()
    assert after["analytics_query.cpp"] is False  # the one module whose body changed
    assert all(reused for unit, reused in after.items() if unit != "analytics_query.cpp")


def test_the_host_path_is_sanitizer_clean(tmp_path):
    """Address and UndefinedBehavior sanitizers over the whole self-check, owners included."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    generated = compile_source(load_project(APP).source, "", (ENTRY,))[0]
    entry = f"int main() {{ return static_cast<int>(cf_{mangle(ENTRY)}()); }}\n"
    (tmp_path / "p.cpp").write_text(generated + entry)
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined"]
    flags += ["-fno-sanitize-recover=all"]
    subprocess.run(["clang++", *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=300)
    done = subprocess.run([tmp_path / "p"], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-4000:]
    assert "every cross-check passed on the host" in done.stdout


@pytest.mark.parametrize("configuration", ["cairn.toml", "gpu.toml"])
def test_every_template_certifies_against_its_bounds(configuration):
    """`cairn check --generics` exits 0 for this project: seven templates of its own, and std's."""
    verdicts = certify_templates(load_project(APP / configuration).source)
    assert {name: verdict for name, verdict in verdicts.items() if verdict != "ok"} == {}
    mine = {name for name in verdicts if not name.startswith("std.")}
    assert mine == {
        "analytics.agg.bins_new",
        "analytics.agg.bins_slot",
        "analytics.agg.bins_tally",
        "analytics.agg.bins_total",
        "analytics.agg.run_static",
        "analytics.query.map_loop",
        "analytics.query.map_par",
    }


def test_a_label_of_the_wrong_width_is_a_type_error(tmp_path):
    """report.line takes ro<u8>[LABEL]: the compiler counts the label, so no caller has to."""
    root = copied(tmp_path)
    main = root / "src" / "main.cairn"
    main.write_text(main.read_text().replace('"analytics: ingest            "', '"analytics: ingest"'))
    with pytest.raises(Exception) as raised:
        compile_source(load_project(root).source)
    assert "ro<u8>[29]" in str(raised.value)


def test_the_device_agrees_with_the_host(tmp_path):
    reason = device_missing()
    if reason:
        pytest.skip(f"the analytics device path needs a GPU: {reason}")
    started = time.monotonic()
    record = build(load_project(APP / "gpu.toml"), output=tmp_path / "build", cxx="g++", timeout=290)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    assert time.monotonic() - started < 290
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert "the device agrees with the host bit for bit" in done.stdout


def test_the_device_configuration_pays_for_what_it_uses(tmp_path):
    rows = compile_source(load_project(APP / "gpu.toml").source)[1]["functions"]
    assert "par:device" in rows["analytics.device.notional"]["effects"]
    assert {"gpu_alloc", "gpu_free", "par:device"} <= set(rows["analytics.device.above"]["effects"])
    queued = set(rows["analytics.device.queued_notional"]["effects"])
    assert {"spawn", "join", "par:device", "transfer:h2d", "transfer:d2h", "gpu_alloc"} <= queued
    # The staging buffers are lent to host helpers, so the pipeline needs no host copy of them.
    assert {"read:price", "read:qty", "write:back"} <= queued
    # One generated name per column on each side: the module says which side it runs on.
    assert "par:device" in rows["analytics.device.Trade_wrapping_price"]["effects"]
    assert "par:device" not in rows["analytics.schema.Trade_wrapping_price"]["effects"]
