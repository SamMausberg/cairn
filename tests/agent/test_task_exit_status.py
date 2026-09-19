"""A printed pass cannot override process failure after that print."""

import subprocess

from cairn.verify.testing import evaluate


def test_late_native_failure_invalidates_pass(monkeypatch):
    import cairn.verify.testing as m

    calls = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 7, '{"status":"passed-finite-tests","cases":1}\n', "late crash"),
        ]
    )
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: next(calls))
    r = evaluate("fn f()->u64=0;", {"schema": "cairn.task/1", "symbol": "f", "cases": [{"args": {}, "return": 0}]})
    assert r["status"] == "native-trap-or-crash" and r["execution_exit_code"] == 7
