"""What was tried on a function, what failed and why, what was measured and how, and what is only a hypothesis.

Every record is about one candidate of one function and carries the candidate's identity, in five parts:

- `source`: the function and everything it calls as the compiler lowered them, canonical as `cairn diff` compares
  code, with the variant that makes this candidate (a plan, parameter values, another implementation). An edit
  elsewhere, a comment or a renamed local leaves it alone.
- `contract`: what the candidate must preserve, as its host states it.
- `target`: what it was compiled or run for.
- `compiler`: `implementation_hash()` of the checker and emitter, with the runtime headers the emitted code includes.
- `artifact`: the build output the record is about, when one was built.

A record is current while its source, contract and compiler are what they are now and its target is one the caller
works on. Otherwise it is stale: it is returned apart, with the parts that moved, and never as a current fact.

Each record has a kind (`KINDS`), and a kind requires what makes it evidence: a measurement its procedure, a
profiler reading the run it came from, a hypothesis its claim. Records are appended to `records.jsonl` in the history
directory, and a record equal to one already there is not added again. A finished analysis is kept under
`analysis/` by the key of everything it read, so the same work is not done twice (`History.analysis`, `keep`).
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROTOCOL = "cairn.history/1"
PARTS = ("source", "contract", "target", "compiler", "artifact")
KINDS = {
    "attempt": "a candidate was tried: what it is and what made it",
    "failure": "it failed: at which stage (check, build, validation, run) and why",
    "validation": "a check of its behaviour ran: the evidence class it established, and on what",
    "observation": "a compiler's static reading, nothing run: resources, instruction counts, a prediction",
    "measurement": "a timed run: its numbers and the procedure that produced them",
    "profile": "a profiler's reading from an explicit profiling run, kept apart from timing",
    "hypothesis": "an explanation nothing has confirmed",
    "experiment": "a run that would confirm or refute a hypothesis, not yet done",
}
REQUIRED = {
    "failure": ("stage", "why"),
    "validation": ("evidence",),
    "observation": ("by",),
    "measurement": ("procedure",),
    "profile": ("tool", "run"),
    "hypothesis": ("claim",),
    "experiment": ("run", "tests"),
}
MAX_RECORD = 64_000  # bytes of one record: a record names its artifacts, it does not hold them


def digest(value: Any) -> str:
    """The sha256 of a JSON value, keys sorted, or of text as it is."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@functools.cache
def compiler() -> str:
    """The checker and emitter (`implementation_hash`) with the runtime headers, read once per process."""
    from ..verify.scalar_semantics import implementation_hash

    runtime = Path(__file__).parents[1] / "runtime"
    headers = b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in sorted(runtime.glob("*.hpp")))
    return digest([implementation_hash(), hashlib.sha256(headers).hexdigest()])


def closure(source: str, symbol: str) -> str:
    """The digest of `symbol` and everything it calls as lowered, each canonical (verify/emission.py). An
    implementation of a function is not one of its callees: a plan selects it, and a candidate names it."""
    from ..verify.emission import emitted

    return closed(emitted(source), symbol)


def closed(lowered: tuple[Any, ...], symbol: str) -> str:
    """`closure` of `symbol` in a program already lowered by `verify.emission.emitted`."""
    from ..verify.emission import canonical

    _, receipts, code, types = lowered
    if symbol not in receipts:
        raise ValueError(f"No function {symbol} to identify.")
    seen, pending = set(), [symbol]
    while pending:
        name = pending.pop()
        if name not in seen and name in code:
            seen.add(name)
            alternatives = receipts[name].get("implementations", {})  # what a plan may select instead, not a callee
            pending += [callee for callee in receipts[name]["calls"] if callee not in alternatives]
    return digest("".join(f"// {n}\n{canonical(n, code[n], types)}\n" for n in sorted(seen)))


def as_written(source: str, symbol: str) -> str:
    """`symbol` as written, without a plan or a selected implementation: the `closure` every candidate's source is
    made from."""
    from ..perf.plan_source import Placement

    return closure(Placement(source, symbol).apply((), use=None), symbol)


def selectable(source: str, table: dict[str, Any] | None, vendored: dict[str, str] | None = None) -> dict[str, Any]:
    """A reference's implementations as its receipt lists them, each identity widened by the implementation and
    everything it calls as lowered (`closure`), and by the sha256 of each vendored source (`vendored`, by foreign
    symbol) its row reaches. The receipt's identity is the two declarations as written, so a helper or a vendored
    source changed after a validation would otherwise leave the validation current. A validation of an
    implementation, and every candidate that selects one, is kept under this identity."""
    if not table:
        return {}
    from ..verify.emission import emitted

    lowered, out = emitted(source), {}
    for g, row in table.items():
        effects = lowered[1][g]["effects"]
        reached = {
            e[4:]: (vendored or {})[e[4:]] for e in effects if e.startswith("ffi:") and e[4:] in (vendored or {})
        }
        out[g] = {**row, "identity": digest([row["identity"], closed(lowered, g), *([reached] if reached else [])])}
    return out


def vendored(project: Any) -> dict[str, str]:
    """The sha256 of the vendored source that defines each foreign symbol of a project's `[foreign]` table."""
    found: dict[str, str] = {}
    for path, symbols in getattr(project, "foreign", ()):
        sha = hashlib.sha256((project.root / path).read_bytes()).hexdigest()
        found |= dict.fromkeys(symbols, sha)
    return found


def identity(base: str, variant: Any, contract: Any, target: Any, artifact: str | None = None) -> dict[str, Any]:
    """A candidate's identity from `base` (the function as written, `as_written`), the variant that makes the candidate
    (a plan's items, parameter values, an implementation's digest), its contract and its target. A contract or a
    target that is not already a string is kept as its digest."""
    return {"source": digest([base, variant]), "contract": contract if isinstance(contract, str) else digest(contract),
            "target": target if isinstance(target, str) else digest(target), "compiler": compiler(),
            "artifact": artifact}  # fmt: skip


def validated(kind: str, function: str, candidate: str, identity: dict[str, Any], detail: dict[str, Any]) -> None:
    if kind not in KINDS:
        raise ValueError(f"A record is one of {', '.join(KINDS)}; {kind!r} is none of them.")
    if not function or not isinstance(candidate, str) or not candidate:
        raise ValueError("A record names its function and its candidate.")
    if not isinstance(identity, dict) or set(identity) != set(PARTS):
        raise ValueError(f"A record's identity has exactly {', '.join(PARTS)}.")
    if not all(isinstance(identity[p], str) and identity[p] for p in PARTS if p != "artifact"):
        raise ValueError("source, contract, target and compiler are each a nonempty string.")
    if not isinstance(detail, dict):
        raise ValueError("A record's detail is an object.")
    if missing := [k for k in REQUIRED.get(kind, ()) if not detail.get(k)]:
        raise ValueError(f"A {kind} record carries {', '.join(REQUIRED[kind])}; it lacks {', '.join(missing)}.")


def record(where: str | Path, kind: str, function: str, candidate: str, identity: dict[str, Any],
           detail: dict[str, Any], variant: Any = None) -> dict[str, Any]:  # fmt: skip
    """Append one record of `kind` about `candidate` (a short label: a plan, an implementation's name) of `function`
    (its qualified name) under `identity` to the history directory `where`, and return it with its id. `variant` is
    what `identity`'s source was made from beside the function, so a later program can tell whether it still holds.
    A record equal to one already kept is returned as it was, not added again."""
    validated(kind, function, candidate, identity, detail)
    body = {"protocol": PROTOCOL, "kind": kind, "function": function, "candidate": candidate, "variant": variant,
            "identity": identity, "detail": detail}  # fmt: skip
    body["id"] = digest(body)[:16]
    line = json.dumps({**body, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, sort_keys=True)
    if len(line.encode()) > MAX_RECORD:
        raise ValueError(f"A record is at most {MAX_RECORD} bytes; keep large output as an artifact and name it.")
    history = History(where)
    with history.locked():
        kept = next((r for r in history.records() if r["id"] == body["id"]), None)
        if kept is not None:
            return kept
        with history.file.open("a", encoding="utf-8") as out:
            out.write(line + "\n")
    return json.loads(line)


class History:
    """One history directory: its records, which of them hold now, and the analyses it keeps."""

    def __init__(self, where: str | Path):
        self.root = Path(where)
        self.file = self.root / "records.jsonl"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)

    def records(self, function: str | None = None) -> list[dict[str, Any]]:
        """Every record, oldest first, or those about `function`. A line that does not read is skipped: a
        history is evidence someone kept, not input the tools trust."""
        if not self.file.exists():
            return []
        out = []
        for line in self.file.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(r, dict)
                and r.get("protocol") == PROTOCOL
                and (function is None or r["function"] == function)
            ):
                out.append(r)
        return out

    def holding(self, function: str, base: str, kind: str, variant: Any) -> list[dict[str, Any]]:
        """The records of `kind` about `variant` of `function` whose source and compiler hold now, for any contract
        and target: what may be cited about that candidate as it is now, such as the validation of an
        implementation whose identity is `variant`."""
        now = {"source": digest([base, variant]), "compiler": compiler()}
        return [r for r in self.records(function) if r["kind"] == kind and r["variant"] == variant
                and all(r["identity"][part] == value for part, value in now.items())]  # fmt: skip

    def judged(self, function: str, base: str, contracts: set[str], targets: set[str]) -> dict[str, list[dict]]:
        """`function`'s records split into `current` and `stale`, against the function as written now (`base`, the
        `closure` of the source without a variant), the contracts it holds now and the targets the caller works on.
        A stale record names under `stale` the parts of its identity that no longer match."""
        split: dict[str, list[dict]] = {"current": [], "stale": []}
        for r in self.records(function):
            now = {"source": digest([base, r["variant"]]), "compiler": compiler()}
            moved = [p for p in ("source", "compiler") if r["identity"][p] != now[p]]
            moved += ["contract"] * (r["identity"]["contract"] not in contracts)
            moved += ["target"] * (r["identity"]["target"] not in targets)
            split["stale" if moved else "current"].append({**r, **({"stale": moved} if moved else {})})
        return split

    # Analyses ------------------------------------------------------------------------------------------------------

    def place(self, key: str) -> Path:
        if not (len(key) == 64 and all(c in "0123456789abcdef" for c in key)):
            raise ValueError("An analysis key is a sha256 in hex.")
        return self.root / "analysis" / key[:2] / key

    def analysis(self, key: str) -> dict[str, Any] | None:
        """The analysis kept under `key`, or None; one whose file does not read, or names another key, is none."""
        try:
            kept = json.loads((self.place(key) / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return kept if isinstance(kept, dict) and kept.get("key") == key else None

    def keep(self, key: str, result: dict[str, Any], files: dict[str, bytes] | None = None) -> dict[str, Any]:
        """Keep `result` and its artifacts (`files`, name -> bytes) under `key`, published by one rename, and return
        the result with the path of each artifact."""
        place = self.place(key)
        staging = place.with_name(f".{key}.{os.getpid()}")
        staging.mkdir(parents=True, exist_ok=True)
        named = {}
        for name, data in (files or {}).items():
            if "/" in name or name.startswith("."):
                raise ValueError(f"An artifact is a plain file name, not {name!r}.")
            (staging / name).write_bytes(data)
            named[name] = str(place / name)
        kept = {**result, "key": key, "artifacts": named}
        (staging / "result.json").write_text(json.dumps(kept, sort_keys=True, indent=1), encoding="utf-8")
        try:
            staging.rename(place)
        except OSError:  # another process kept the same analysis first: the same inputs, so the same answer
            for leftover in staging.iterdir():
                leftover.unlink()
            staging.rmdir()
        return self.analysis(key) or kept
