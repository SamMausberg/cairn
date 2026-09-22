#!/usr/bin/env python3
"""Run a host-owned CAIRN task with a user-selected external adapter.

Adapter stdin: one JSON object with `messages`, `attempt`, `protocol`.
Adapter stdout: one strict cairn.edit/2 JSON request, no Markdown fences: a body edit, or an
expand request naming functions or types to read first. An expansion uses an attempt.
The adapter executable is user supplied. No network/model is built in, no API
key is read by this runner, and a local process is NOT a security sandbox.
Public examples may be used for repairs. Reserved cases are checked once after
public examples pass, never sent as repair feedback. Full answer isolation
requires an external sandbox. All shipped examples are public research data.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R / "src"), str(R / "tools")]
from ai.task_eval import evaluate, validate_contract
from cairn.agent.agent_tools import EditHost, digest, explain, load_json_strict, stable_json
from cairn.compiler.cairnc import Diagnostic


def ask(command, encoded, host, public, source):
    """One adapter call: its reply, the bytes it sent, the feedback it gets, and the candidate when that candidate
    passed the public cases."""
    try:
        # The command is explicit argv, never a shell string. External adapter trust
        # is separate from the parser's bounded edit request.
        with tempfile.TemporaryDirectory(prefix="cairn-adapter-") as tmp:
            cp = subprocess.run(command, input=encoded, text=True, capture_output=True, cwd=tmp, timeout=120)
    except subprocess.TimeoutExpired:
        return "", 0, {"status": "unknown", "stage": "adapter-timeout"}, None
    except OSError as e:
        return "", 0, {"status": "adapter-error", "message": str(e)}, None
    raw, sent = cp.stdout, len(cp.stdout.encode())
    if sent > 1000000:
        return "", sent, {"status": "adapter-error", "message": "Adapter reply exceeds 1 MB."}, None
    if cp.returncode:
        return raw, sent, {"status": "adapter-failed", "exit_code": cp.returncode, "stderr": cp.stderr[:2000]}, None
    try:
        edit = load_json_strict(raw)
        typed = host.respond(edit)
        if typed.get("status") != "typed":  # An expansion or an explanation, not a candidate.
            return raw, sent, typed, None
        candidate = host.admitted[edit["handle"]][-1][0]
        tests = evaluate(candidate, public)
        passed = tests["status"] == "passed-finite-tests"
        return raw, sent, {"admission": typed, "tests": tests}, candidate if passed else None
    except Diagnostic as e:
        return raw, sent, explain(e, source), None
    except (ValueError, TypeError, KeyError, RecursionError) as e:
        return raw, sent, {"status": "invalid-reply", "message": str(e)}, None


def run(source, contract, command, attempts=4, public_cases=3, adapter_kind="external-unverified"):
    if not 1 <= attempts <= 20:
        raise ValueError("Attempt limit must be 1..20.")
    validate_contract(source, contract)
    if not 1 <= public_cases < len(contract["cases"]):
        raise ValueError("Need nonempty public and reserved case sets.")
    host = EditHost()
    public = {**contract, "cases": contract["cases"][:public_cases]}
    reserved = {**contract, "cases": contract["cases"][public_cases:]}
    packet = host.open(source, contract["symbol"], contract)
    packet["public_examples"] = public["cases"]
    messages = [
        {
            "role": "system",
            "content": "Return one strict cairn.edit/2 JSON request: a body edit, or an expand request for a function or type you need to read. Change only the authorized function body. Preserve the signature, host-owned contract and allowed effects. Compiler acceptance alone is not task completion.",
        },
        {"role": "user", "content": stable_json(packet)},
    ]
    log = []
    request_bytes = response_bytes = 0
    start = time.monotonic()
    final_source = None
    status = "attempt-budget-exhausted"
    for i in range(attempts):
        request = {"protocol": "cairn.adapter/1", "attempt": i, "messages": messages}
        encoded = stable_json(request)
        request_bytes += len(encoded.encode())
        raw, sent, feedback, candidate = ask(command, encoded, host, public, source)
        response_bytes += sent
        log.append({"attempt": i, "request": request, "reply": raw, "feedback": feedback})
        if candidate is not None:  # Reserved cases are checked once, and their outcome never reaches the adapter.
            log[-1]["reserved_verdict"] = hidden = evaluate(candidate, reserved)
            passed = hidden["status"] == "passed-finite-tests"
            status = "passed-reserved-finite-tests" if passed else "reserved-tests-not-passed"
            final_source = candidate if passed else None
            break
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": stable_json(feedback)}]
    return final_source, {
        "status": status,
        "adapter_kind": adapter_kind,
        "adapter_command": command,
        "source_sha256": digest(source),
        "contract_sha256": digest(stable_json(contract)),
        "attempts": len(log),
        "public_cases": public_cases,
        "reserved_cases": len(reserved["cases"]),
        "request_utf8_bytes_including_repeated_history": request_bytes,
        "response_utf8_bytes": response_bytes,
        "tokenizer": "Plain ByT5 byte mapping, excluding specials; not frontier BPE",
        "elapsed_seconds": time.monotonic() - start,
        "security_sandbox": False,
        "formal_status": "not-verified",
        "model_identity_verified": False,
        "trace": log,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--public-cases", type=int, default=3)
    p.add_argument("--adapter-kind", choices=["external-unverified", "scripted-fixture"], default="external-unverified")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--adapter", nargs=argparse.REMAINDER, required=True)
    a = p.parse_args()
    if a.out_dir.exists():
        p.error("Choose a new output directory, preserving previous transcripts.")
    if not a.adapter:
        p.error("Supply an explicit executable argv after --adapter.")
    source = a.source.read_text()
    contract = load_json_strict(a.contract.read_text())
    candidate, result = run(source, contract, a.adapter, a.attempts, a.public_cases, a.adapter_kind)
    a.out_dir.mkdir(parents=True)
    (a.out_dir / "transcript.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if candidate is not None:
        (a.out_dir / "candidate.cairn").write_text(candidate)
    print(json.dumps({k: v for k, v in result.items() if k != "trace"}, indent=2))
    return 0 if candidate is not None else 1


if __name__ == "__main__":
    sys.exit(main())
