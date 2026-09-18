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

ROOT = Path(__file__).resolve().parents[1]
PROOFS = ROOT / "proofs"
EXPORTER = ROOT / "tools" / "export_lean_certificates.py"
GENERATED = PROOFS / "Cairn" / "CollectorCertificates.lean"

FORBIDDEN_AXIOMS = ("sorryAx", "Lean.ofReduceBool", "Lean.trustCompiler")
EXPECTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
BANNED_SOURCE_TOKENS = ("sorry", "native_decide", "axiom ", "unsafe ", "implemented_by")

ELAN_BIN = Path.home() / ".elan" / "bin"

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
    ):
        assert headline in report, "audit does not mention " + headline


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
