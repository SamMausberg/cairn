"""Device implementations validated on a host emulation of the device: `cairn validate --emulate`.

Without it a device implementation is `unknown`, since nothing runs on a device outside `make gpu`. With it, the
reference and the implementation are judged against the device target and built for the host, their device work on
host threads (projects/emulation.py). A kernel wrong at a partial tile then fails at its shrunk input, which is kept
and replayed by `cairn test --emulate`; a right one passes as `finite-tested-emulated`, which the candidate history
keeps apart from finite testing on the host or the device, and which `cairn tune` chooses on only when asked to.
"""

import json
from pathlib import Path

from cairn.agent.history import History
from cairn.agent.hosts.implementations import ImplementationHost
from cairn.agent.mcp.tools import Tools
from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic
from cairn.perf.tuning.search import Budget
from cairn.perf.tuning.tune import tune
from cairn.projects.project import load_project
from cairn.projects.target import parse
from cairn.verify.validation.validation import replay, validate, validate_project

# Its ceiling lets an implementation stage the input in a block's shared memory.
SCALE = """fn scale(
  n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device
) effects(read:x, write:out, trap, ffi_precondition, par:device, local_read, local_write, zero_init) {
  parallel i in n { out[i] = mul_wrap(x[i], 3); }
}
"""
# Wrong at a partial tile: its grid counts whole tiles of 64 and drops the rest.
TILES = """fn scale_tiles(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) implements scale {
  let g = n / 64;
  blocks b in g threads t in 64 {
    shared tile:u32[64] = zeroed;
    let i = b * 64 + t;
    if i < n { tile[t] = x[i]; }
    barrier;
    if i < n { out[i] = mul_wrap(tile[t], 3); }
  }
}
"""
FIXED = TILES.replace("let g = n / 64;", "let g = (n + 63) / 64;")
SM_120 = parse("sm_120")
SMALL = {"budget": 48, "domain": {"largest_extent": 300}}


def test_without_emulation_a_device_implementation_is_unknown():
    record = validate(SCALE + TILES, "scale", "scale_tiles", SMALL)
    assert record["status"] == "unknown" and "--emulate" in record["reason"], record


def test_a_kernel_wrong_at_a_partial_tile_fails_emulated_at_its_shrunk_input(tmp_path):
    kept = tmp_path / "regressions.json"
    record = validate(SCALE + TILES, "scale", "scale_tiles", SMALL, regressions=kept, emulate=SM_120)
    assert record["status"] == "failed" and record["evidence"] == "finite-tested-emulated", record
    failed = record["finite"]["failed"]
    assert failed["inputs"]["n"] < 64 and failed["implementation"]["outcome"] == "return"  # a partial first tile
    assert record["emulation"]["judged_against"] == "sm_120"
    assert (
        "host emulation of sm_120" in record["finite"]["claim"] and "never of the device" in record["finite"]["claim"]
    )
    assert [c["args"] for c in json.loads(kept.read_text())["cases"]] == [failed["inputs"]]
    replayed = replay(SCALE + TILES, json.loads(kept.read_text()), emulate=SM_120)
    assert replayed["status"] == "failed-tests" and replayed["emulation"]["judged_against"] == "sm_120"
    assert replay(SCALE + FIXED, json.loads(kept.read_text()), emulate=SM_120)["status"] == "passed-finite-tests"
    assert replay(SCALE + FIXED, json.loads(kept.read_text()))["status"] == "unknown"  # nothing runs unemulated


def test_the_corrected_kernel_passes_as_finite_tested_emulated(tmp_path):
    record = validate(SCALE + FIXED, "scale", "scale_tiles", SMALL, emulate=SM_120)
    assert record["status"] == "passed" and record["evidence"] == "finite-tested-emulated", record
    assert record["finite"]["implementation_ran"] > 0 and record["tiles"]  # the literal tile, 64, gave the extents
    on_host = (SCALE + FIXED).replace("@device", "").replace("par:device", "par:host")
    host = validate(on_host, "scale", "scale_tiles", SMALL)
    assert host["status"] == "passed" and host["evidence"] == "finite-tested" and "emulation" not in host


def test_cairn_validate_emulate_on_a_project_and_what_tune_makes_of_it(tmp_path, capsys):
    path = tmp_path / "scale.cairn"
    path.write_text(SCALE + TILES)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(SMALL))
    code = main(["validate", str(path), "--symbol", "scale_tiles", "--policy", str(policy), "--emulate",
                 "--device-target", "sm_120", "--format", "human"])  # fmt: skip
    said = capsys.readouterr().out
    assert code == 1 and "finite-tested on a host emulation of sm_120" in said and "fails at n = " in said, said
    path.write_text(SCALE + FIXED)
    history = tmp_path / "history"
    record = validate_project(load_project(path), "scale_tiles", SMALL, history=history, emulate=SM_120)
    assert record["status"] == "passed" and record["history"]
    (kept,) = [r for r in History(history).records() if r["kind"] == "validation"]
    assert kept["detail"]["evidence"] == "finite-tested-emulated" and kept["detail"]["judged_against"] == "sm_120"
    assert kept["identity"]["target"] == "host emulation of sm_120"
    sizes, budget = [{"n": 1e6}], Budget(compiles=0)
    plain = tune(SCALE + FIXED, "scale", sizes, device_target=SM_120, budget=budget, history=history, pinned=SMALL)
    row = next(r for r in plain["candidates"] if r.get("use") == "scale_tiles")
    assert isinstance(row["validated"], str) and "--accept-emulated" in row["validated"]
    assert plain["chosen"].get("use") is None  # the reference: nothing else may be chosen on emulated evidence
    opted = tune(SCALE + FIXED, "scale", sizes, device_target=SM_120, budget=budget, history=history,
                 accept_emulated=True, pinned=SMALL)  # fmt: skip
    row = next(r for r in opted["candidates"] if r.get("use") == "scale_tiles")
    assert row["validated"]["evidence"] == "finite-tested-emulated" and row["validated"]["judged_against"] == "sm_120"


def test_an_implementation_session_validates_device_code_emulated():
    host = ImplementationHost()
    packet = host.open(SCALE, "scale", SMALL, emulate=SM_120)
    assert "never a device run" in packet["emulation"]
    try:
        host.respond({**packet["reply"], "source": TILES})
        raise AssertionError("a kernel wrong at a partial tile validated")
    except Diagnostic as refused:
        assert refused.data["code"] == "E-VALIDATION" and refused.data["finite"]["failed"]["inputs"]["n"] < 64
    answer = host.respond({**packet["reply"], "source": FIXED})
    assert answer["status"] == "validated" and answer["evidence"] == "finite-tested-emulated"
    assert answer["emulation"]["judged_against"] == "sm_120" and "host emulation of sm_120" in answer["claim"]
    assert host.submissions[-1]["target"] == "host emulation of sm_120"


def test_mcp_opens_an_emulated_session_and_refuses_a_bad_target():
    tools = Tools(Path.cwd())
    packet, failed = tools.call("implementation_open", {"source": SCALE, "reference": "scale", "emulate": "sm_120"})
    assert not failed and "sm_120" in packet["emulation"]
    wrong, failed = tools.call("implementation_open", {"source": SCALE, "reference": "scale", "emulate": "gfx90a"})
    assert failed and wrong["code"] == "E-TARGET"
