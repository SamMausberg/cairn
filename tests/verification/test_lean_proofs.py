"""The Lean development must stay in step with the Python rules and must build.

Two levels of evidence:

* the fast tests re-run the exporter in ``--check`` mode, so a change to
  ``collector_rules()`` that is not mirrored into ``proofs/`` fails immediately;
* the build test actually runs ``lake build`` and inspects the ``#print axioms``
  output.  A proof script that exists is not a proof; only a successful pinned
  build with audited axioms counts.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROOFS = ROOT / "proofs"
EXPORTER = ROOT / "tools" / "checks" / "export_lean_certificates.py"
GENERATED = PROOFS / "Cairn" / "CollectorCertificates.lean"

FORBIDDEN_AXIOMS = ("sorryAx", "Lean.ofReduceBool", "Lean.trustCompiler")
EXPECTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
BANNED_SOURCE_TOKENS = ("sorry", "native_decide", "axiom ", "unsafe ", "implemented_by")

ELAN_BIN = Path.home() / ".elan" / "bin"

# The ownership and lease calculus: these must exist, be audited and stay free of excluded middle.
OWNERSHIP_THEOREMS = (
    # Array parts: the chain of guarded bounds, and the bridge from the checker's syntactic
    # disjointness to the real footprints under a valuation.
    "Cairn.Ownership.reaches_sound",
    "Cairn.Ownership.ovl_sound",
    "Cairn.Ownership.accepted_no_fault",
    "Cairn.Ownership.accepted_no_use_after_move",
    "Cairn.Ownership.accepted_no_use_after_free",
    "Cairn.Ownership.accepted_no_double_free",
    "Cairn.Ownership.accepted_race_free",
    # Lanes: a region forks one lane per index, and the lanes of an accepted region --
    # together with every task still live -- are pairwise compatible.
    "Cairn.Ownership.accepted_threads_disjoint",
    "Cairn.Ownership.lanesOf_pairwise",
    "Cairn.Ownership.lane_borrows_dont_race",
    "Cairn.Ownership.races_borrow_of_lease",
    "Cairn.Ownership.accepted_no_leaked_ticket",
    "Cairn.Ownership.accepted_no_use_after_wait",
    "Cairn.Ownership.accepted_no_aliased_args",
    "Cairn.Ownership.accepted_frees_each_allocation_once",
    "Cairn.Ownership.accepted_progress",
    "Cairn.Ownership.Ok_succ",
    "Cairn.Ownership.ownership_regression",
    # Non-vacuity: without these the safety theorems would also hold of a machine that never faults.
    "Cairn.Ownership.Regress.leasedRead_races",
    "Cairn.Ownership.Regress.overlappingTasks_races",
    "Cairn.Ownership.Regress.overlappingParts_races",
    "Cairn.Ownership.Regress.sameFieldToTwoTasks_races",
    "Cairn.Ownership.Regress.fieldPartsOverlapInOneCall_aliases",
    "Cairn.Ownership.Regress.laneWritesFixedIndex_races",
    "Cairn.Ownership.Regress.laneWritesShared_races",
    "Cairn.Ownership.Regress.laneReadsOther_races",
    "Cairn.Ownership.Regress.laneTwoStrides_races",
    "Cairn.Ownership.meets_own_block",
    # An accepted program whose parts are ordered by a backwards one aborts at that guard.
    "Cairn.Ownership.Regress.backwardsPart_traps",
    "Cairn.Ownership.Regress.copyAnOwner_doubleFrees",
    "Cairn.Ownership.Regress.useAfterDrop_usesDeadPlace",
    "Cairn.Ownership.Regress.unawaitedTicket_leaks",
    "Cairn.Ownership.Regress.witnesses_are_rejected",
    # Task groups: a group never waited leaks, a waited one is gone, and what one path lent it or
    # ordered by a part it formed stays lent after the join.
    "Cairn.Ownership.Regress.groupNeverWaited_leaks",
    "Cairn.Ownership.Regress.collectAfterWait_deadGroup",
    "Cairn.Ownership.Regress.groupLeaseFromOnePath_races",
    "Cairn.Ownership.Regress.groupFactFromOnePath_races",
    "Cairn.Ownership.Regress.group_witnesses_are_rejected",
    # The join claims no more than either path, and forgetting keeps the invariant.
    "Cairn.Ownership.joinOf_le",
    "Cairn.Ownership.Sync.weaken",
    # The lane pool's region protocol: every index once, no worker inside after return, never stuck.
    "Cairn.Region.runs_once",
    "Cairn.Region.quiet_when_back",
    "Cairn.Region.finishes",
)

# `#print axioms` prints one line per declaration; names may end in a prime.
AXIOM_LINE = re.compile(r"^'(?P<name>.+)' (?:depends on axioms: \[(?P<axioms>.*)\]|does not depend on any axioms)$")


def find_lake() -> str | None:
    """`lake` on PATH, or the elan shim in the user's home directory."""
    found = shutil.which("lake")
    if found:
        return found
    candidate = ELAN_BIN / "lake"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def lake_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if ELAN_BIN.is_dir():
        environment["PATH"] = str(ELAN_BIN) + os.pathsep + environment.get("PATH", "")
    return environment


def strip_lean_comments(text: str) -> str:
    """Drop `/- ... -/` blocks (including `/--` and `/-!`) and `--` line comments."""
    text = re.sub(r"/-.*?-/", " ", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", text)


def lean_sources() -> list[Path]:
    return [p for p in sorted(PROOFS.glob("**/*.lean")) if ".lake" not in p.parts]


def test_generated_lean_file_matches_python_rules():
    result = subprocess.run(
        [sys.executable, str(EXPORTER), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_lean_file_is_reproducible(tmp_path):
    destination = tmp_path / "CollectorCertificates.lean"
    result = subprocess.run(
        [sys.executable, str(EXPORTER), "--output", str(destination)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert destination.read_text(encoding="utf-8") == GENERATED.read_text(encoding="utf-8")


def test_exporter_check_mode_detects_drift(tmp_path):
    destination = tmp_path / "CollectorCertificates.lean"
    destination.write_text("-- stale\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(EXPORTER), "--check", "--output", str(destination)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0


def test_lean_sources_contain_no_escape_hatches():
    sources = lean_sources()
    assert sources, "no Lean sources found under proofs/"
    for path in sources:
        code = strip_lean_comments(path.read_text(encoding="utf-8"))
        for needle in BANNED_SOURCE_TOKENS:
            assert needle not in code, str(path) + " contains " + needle


def test_lake_build_succeeds_and_axioms_are_clean():
    lake = find_lake()
    if lake is None:
        pytest.skip(
            "lake is not installed: nothing named 'lake' on PATH and no ~/.elan/bin/lake. "
            "Install with: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y "
            "--default-toolchain none"
        )
    environment = lake_environment()
    build = subprocess.run([lake, "build"], cwd=PROOFS, capture_output=True, text=True, env=environment, timeout=3600)
    output = build.stdout + build.stderr
    assert build.returncode == 0, output
    assert "error:" not in output, output

    audit = subprocess.run(
        [lake, "env", "lean", str(Path("Cairn") / "Audit.lean")],
        cwd=PROOFS,
        capture_output=True,
        text=True,
        env=environment,
        timeout=3600,
    )
    report = audit.stdout + audit.stderr
    assert audit.returncode == 0, report
    for banned in FORBIDDEN_AXIOMS:
        assert banned not in report, "audit reports " + banned + ":\n" + report

    reported = {}
    for line in report.splitlines():
        match = AXIOM_LINE.match(line.strip())
        if match is None or match.group("axioms") is None:
            continue
        reported[match.group("name")] = [part.strip() for part in match.group("axioms").split(",") if part.strip()]
    assert reported, "audit produced no '#print axioms' lines:\n" + report
    for name, axioms in reported.items():
        for axiom in axioms:
            assert axiom in EXPECTED_AXIOMS, name + " uses unexpected axiom " + axiom
    for headline in (
        "Cairn.check_sound",
        "Cairn.Collector.all_checked",
        "Cairn.Collector.collect_spec",
        "Cairn.Collector.store_index_lt_capacity",
        "Cairn.Collector.increments_fit",
        "Cairn.Collector.run_preserves_inv",
        *OWNERSHIP_THEOREMS,
    ):
        assert headline in report, "audit does not mention " + headline

    # The ownership calculus is proved without excluded middle; keep it that way.
    for name in OWNERSHIP_THEOREMS:
        assert "Classical.choice" not in reported.get(name, ()), name + " now needs Classical.choice"

    # `Audit.lean` evaluates the Lean encodings of the programs `tests/soundness/test_soundness.py`
    # pins; the checker must classify every one of them the way the Python checker does.
    assert "ownership-regression: pass" in report, "the Lean ownership regression did not pass:\n" + report


def test_recorded_evidence_matches_the_pinned_toolchain():
    summary_path = ROOT / "evidence" / "v1_0" / "lean" / "summary.json"
    if not summary_path.is_file():
        pytest.skip("no captured Lean evidence in evidence/v1_0/lean")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    pinned = (PROOFS / "lean-toolchain").read_text(encoding="utf-8").strip()
    assert summary["toolchain"] == pinned
    assert summary["sorry_free"] is True
    assert set(summary["axioms_used"]) <= set(EXPECTED_AXIOMS)
    assert summary["certificate_count"] == 17
