"""The capability matrix: the table in docs/verification.md is the data in docs/project/capability_matrix.json, and
every row keeps its claims apart and names what they rest on.
"""

import copy
import json
import subprocess
import sys

import pytest

from release.capability_matrix import DATA, DOC, ROOT, rows, table, written


def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_the_table_in_verification_md_is_the_data():
    doc = DOC.read_text(encoding="utf-8")
    assert written(doc, table()) == doc, "run make docs (python3 tools/release/capability_matrix.py)"
    done = subprocess.run([sys.executable, "tools/release/capability_matrix.py", "--check"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.returncode == 0, done.stderr


def test_every_row_keeps_its_claims_apart_and_names_what_they_rest_on():
    checked = rows()
    assert {r["side"] for r in checked} == {"host", "device"} and len(checked) >= 20


def broken(change) -> str:
    changed = copy.deepcopy(data())
    change(changed["rows"])
    with pytest.raises(ValueError) as refused:
        rows(changed)
    return str(refused.value)


def test_a_claim_without_its_record_or_outside_the_vocabulary_is_refused():
    device = next(i for i, r in enumerate(data()["rows"]) if r["side"] == "device")
    host = next(i for i, r in enumerate(data()["rows"]) if r["side"] == "host")

    def ran_on_a_gpu(rs):
        rs[device] |= {"ran_on_gpu": "yes: on an H100", "records": ["tests/projects/test_emulation.py"]}

    assert "evidence/" in broken(ran_on_a_gpu)
    assert "evidence/" in broken(
        lambda rs: rs[host].update(measured="yes: fast", records=["tests/soundness/test_scan.py"])
    )
    assert "GPU" in broken(lambda rs: rs[host].update(ran_on_gpu="no"))
    assert "no such record" in broken(lambda rs: rs[host].update(records=["evidence/v9/nothing/README.md"]))
    assert "yes, partial, no, unknown or n/a" in broken(lambda rs: rs[host].update(sanitizers="probably"))
    assert "feature no other row names" in broken(lambda rs: rs.append(copy.deepcopy(rs[host])))
    assert "docs/" in broken(lambda rs: rs[host].update(docs="nowhere.md#x"))
