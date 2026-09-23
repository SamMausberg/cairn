"""What changed between two versions of a program, function by function, and what establishes each answer.

Both versions are checked and lowered by this compiler, so the diff compares two sources, never two compilers. Each
function of the program's own modules that both versions hold gets one class:

  identical-code    its emitted C++ is the same up to a consistent renaming (verify/emission.py), and so is that of
                    everything it calls: the native code that runs is the same code.
  smt-equivalent    Z3 found no admitted input on which the two differ in their result, in what they leave in an rw
                    borrow or in whether they abort, within the modeled fragment (verify/scalar_semantics.py).
  behavior-changed  an input on which they differ: found by Z3, replayed by the value model, and replayed by native
                    builds under each compiler present where the task runner takes the signature.
  unknown           none of these was established, and the reason says why. It is never counted as unchanged.

A function whose parameter or result types differ is `signature-changed` and is not compared value for value; one
that a version alone holds is `added` or `removed`; one whose code is identical under another name is `renamed`.
Beside its class, every function reports what the compiler established exactly on both sides, whatever the solver
decided: signature, effect row, guard sites written and discharged, allocations, local storage, tasks and `unsafe`
blocks. Test blocks are compared by their code. A cost change from `cairn predict` is labelled predicted. The semantic
version verdict reads the public interface: what the program's own modules export, or every function of a program
that declares no module.
"""

from __future__ import annotations

import dataclasses
import multiprocessing
import re
import shutil
import time
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from ..compiler.cairnc import VERSION, Diagnostic
from ..compiler.codegen import mangle
from ..compiler.lexing import lex
from ..compiler.syntax import Parser
from ..compiler.tree import Arm, Expr, Function, Program, Stmt
from .emission import CALLED, canonical, emitted, guard_count, unguarded
from .scalar_semantics import equivalent
from .scalar_values import MAX_SOURCE_BYTES, MAX_UNROLL

ORDER = ["identical-code", "identical-source", "smt-equivalent", "behavior-changed", "unknown", "signature-changed",
         "renamed", "added", "removed"]  # fmt: skip
UNCHANGED = {"identical-code", "identical-source"}
LEVELS = ["none", "patch", "minor", "major"]
FOOTPRINT = {"read", "write", "lane"}  # effects that name a parameter, compared by its position
UNSIGNED = {"u8", "u16", "u32", "u64", "usize"}


@dataclass
class Version:
    source: str
    p: Program
    receipts: dict[str, Any]
    code: dict[str, str]
    types: dict[str, str]
    functions: dict[str, Function]
    own: set[str]  # the program's own functions: not linked from the package, not tests, not templates
    tests: set[str]
    device: bool  # it holds device code, so no witness of it is ever run on this machine


def version(source: str) -> Version:
    p, receipts, code, types = emitted(source)
    device = receipts.pop("$device")
    functions = {f.name: f for f in p.functions}
    linked = set(p.sources)  # modules the package supplied, which are not this program's to report
    own = {f.name for f in p.functions if not f.test and p.modules.get(f.name, f.module) not in linked}
    return Version(source, p, receipts, code, types, functions, own, {f.name for f in p.functions if f.test}, device)


def signature(f: Function) -> dict[str, Any]:
    return {"params": [[n, t.display()] for n, t in f.params], "returns": f.ret.display(),
            "public": f.public or bool(f.owner)}  # fmt: skip


def shape(f: Function) -> tuple:
    return tuple(t.display() for _, t in f.params), f.ret.display()


def positional(f: Function, effects: list[str]) -> dict[str, str]:
    """Each effect under a key a parameter rename leaves alone: `read:xs` of the second parameter is `read:#1`."""
    where = {n: f"#{i}" for i, (n, _) in enumerate(f.params)}
    out = {}
    for e in effects:
        kind, _, what = e.partition(":")
        out[f"{kind}:{where[what]}" if kind in FOOTPRINT and what in where else e] = e
    return out


def walk(node: Any):
    if isinstance(node, list):
        for x in node:
            yield from walk(x)
    elif isinstance(node, (Stmt, Expr, Arm)):
        yield node
        for f in dataclasses.fields(node):
            if f.name not in {"ref", "ty", "proof", "span"}:  # resolutions point elsewhere in the program
                yield from walk(getattr(node, f.name))


def census(f: Function) -> dict[str, int]:
    """What the checked body holds that a reader weighs: tasks started and `unsafe` blocks."""
    nodes = list(walk(f.body))
    return {"spawn": sum(isinstance(x, Expr) and x.tag == "spawn" for x in nodes),
            "unsafe": sum(isinstance(x, Stmt) and x.tag == "unsafe" for x in nodes)}  # fmt: skip


def storage(entries: Any) -> Any:
    return [{k: v for k, v in e.items() if k != "line"} for e in entries] if isinstance(entries, list) else entries


def deltas(o: Version, n: Version, old: str, new: str) -> dict[str, Any]:
    """What the compiler established differently about one function, exactly, on the two sides."""
    fo, fn, ro, rn = o.functions[old], n.functions[new], o.receipts.get(old, {}), n.receipts.get(new, {})
    out: dict[str, Any] = {}
    if signature(fo) != signature(fn):
        out["signature"] = {"before": signature(fo), "after": signature(fn)}
    eo, en = positional(fo, ro.get("effects", [])), positional(fn, rn.get("effects", []))
    if set(eo) != set(en):
        out["effects"] = {"added": sorted(en[k] for k in set(en) - set(eo)),
                          "removed": sorted(eo[k] for k in set(eo) - set(en))}  # fmt: skip
    for key in ("syntactic_check_sites", "discharged_check_sites", "heap_allocations", "implicit_synchronization"):
        if ro.get(key) != rn.get(key):
            out[key] = {"before": ro.get(key), "after": rn.get(key)}
    if storage(ro.get("local_storage")) != storage(rn.get("local_storage")):
        out["local_storage"] = {"before": storage(ro.get("local_storage")), "after": storage(rn.get("local_storage"))}
    if (co := census(fo)) != (cn := census(fn)):
        out["census"] = {"before": co, "after": cn}
    if (go := guard_count(o.code.get(old, ""))) != (gn := guard_count(n.code.get(new, ""))):
        out["emitted_guards"] = {"before": go, "after": gn}  # the guards the lowered code holds, discharged ones gone
    return out


def callees(v: Version, name: str, named: dict[str, str]) -> set[str]:
    """Every function `name` may call: the checker's call graph, and every symbol its code names (a value, a table)."""
    text = canonical(name, v.code.get(name, ""), v.types)
    return set(v.receipts.get(name, {}).get("calls", [])) | {named[m] for m in CALLED.findall(text) if m in named}


def identities(o: Version, n: Version, matched: dict[str, str]) -> tuple[set[str], set[str]]:
    """The functions whose own code is the same on both sides, and those whose reach is too (a fixed point)."""
    renamed = {new: old for new, old in matched.items() if new != old}
    own_same = set()
    for new, old in matched.items():
        fo, fn = o.functions[old], n.functions[new]
        if fo.extern or fn.extern:
            same = (fo.extern, fo.symbol, fo.effects, shape(fo)) == (fn.extern, fn.symbol, fn.effects, shape(fn))
        else:
            same = canonical(old, o.code.get(old, ""), o.types) == canonical(new, n.code.get(new, ""), n.types, renamed)
        if same:
            own_same.add(new)
    named = {mangle(f): f for f in n.functions}
    reach = {s: callees(n, s, named) for s in own_same}
    identical = set(own_same)
    while stale := {s for s in identical if reach[s] - identical - {s}}:
        identical -= stale
    return own_same, identical


def renames(o: Version, n: Version) -> dict[str, str]:
    """new -> old for each function one side alone names whose code is the other's under the other's name."""
    gone, fresh = sorted(o.own - n.own - n.functions.keys()), sorted(n.own - o.own - o.functions.keys())
    found: dict[str, list[str]] = {}
    for new in fresh:
        for old in gone:
            same = shape(o.functions[old]) == shape(n.functions[new]) and not o.functions[old].extern
            if same and canonical(old, o.code.get(old, ""), o.types) == canonical(new, n.code.get(new, ""), n.types):
                found.setdefault(new, []).append(old)
    olds = [x for xs in found.values() for x in xs]
    return {new: xs[0] for new, xs in found.items() if len(xs) == 1 and olds.count(xs[0]) == 1}


def zero(ty: str) -> Any:
    return False if ty == "bool" else 0.0 if ty in {"f32", "f64"} else 0


def replay(v: Version, name: str, inputs: dict[str, Any], seen: dict[str, Any], cxx: str) -> str:
    """Run `name` natively on the witness and say whether it did what the value model said it does."""
    from .testing import evaluate, validate_contract  # a native build; imported only when a witness exists

    if v.device:
        return "not replayed: a program with device code is compiled here, never run"
    f = v.functions[name]
    case: dict[str, Any] = {"args": inputs}
    rw = [p for p, t in f.params if t.mode == "rw"]
    if seen["defined"]:
        case |= {"return": seen["return"]} if f.ret.name != "void" else {}
        case["after"] = {p: seen["written"][p] for p in rw}
    else:  # a trap: any admissible expected value; only an abort agrees
        case |= {"return": zero(f.ret.name)} if f.ret.name != "void" else {}
        case["after"] = {p: inputs[p] for p in rw}
    contract = {"schema": "cairn.task/1", "symbol": name, "cases": [case]}
    try:
        validate_contract(v.source, contract)
    except (ValueError, KeyError, TypeError) as e:
        return f"not replayed: {e}"
    result = evaluate(v.source, contract, cxx)
    if seen["defined"]:
        return "agrees" if result["status"] == "passed-finite-tests" else f"disagrees: {result['status']}"
    aborted = result.get("status") == "native-trap-or-crash" and result.get("execution_exit_code") == -6
    return "agrees" if aborted else f"disagrees: {result['status']}"


MODULE = re.compile(r"^module\s+([\w.]+)\s*;", re.M)
IMPORT = re.compile(r"^import\s+([\w.]+)", re.M)


def reach(source: str, module: str) -> str:
    """The part of `source` a function of `module` can reach: its module and every module that imports lead to.
    Modules cannot import the root, so a module function needs no root code; a root function needs all it imports."""
    cuts = [0, *(m.start() for m in MODULE.finditer(source)), len(source)]
    chunks = [(h.group(1) if (h := MODULE.match(source, a)) else "", source[a:b]) for a, b in pairwise(cuts)]
    texts: dict[str, list[str]] = {}
    for m, text in chunks:
        texts.setdefault(m, []).append(text)
    need, todo = set(), [module]
    while todo:
        m = todo.pop()
        if m not in need and m in texts:
            need.add(m)
            todo += [x for text in texts[m] for x in IMPORT.findall(text)]
    return "".join(text for m, text in chunks if m in need)


def isolated(call, seconds: float) -> dict[str, Any]:
    """`call()` in a forked child that is stopped after `seconds`. Z3 cannot be interrupted while it reads a query,
    and a large one can take it longer than any budget, so a comparison runs where it can be stopped."""
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)

    def run():
        try:
            send.send(call())
        except Exception as e:  # a comparison that fails establishes nothing
            send.send({"class": "unknown", "reason": f"The comparison failed: {type(e).__name__}: {e}"})

    child = context.Process(target=run, daemon=True)
    child.start()
    send.close()
    try:
        if receive.poll(seconds):
            return receive.recv()
        return {"class": "unknown", "reason": f"The comparison ran past its {seconds:.0f} s limit and was stopped."}
    except EOFError:
        return {"class": "unknown", "reason": "The comparison's process ended without an answer."}
    finally:
        if child.is_alive():
            child.kill()
        child.join()


def compare(o: Version, n: Version, name: str, deadline: float, timeout_ms: int) -> dict[str, Any]:
    """The solver's answer for one function whose code differs, as a class and its evidence. A program past the
    value model's size limit is handed to it as the modules the function can reach."""
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining < 10:
        return {"class": "unknown", "reason": "The diff's solver budget ran out before this function."}
    module = n.p.modules.get(name, n.functions[name].module)
    old, new = (v.source if len(v.source.encode()) <= MAX_SOURCE_BYTES else reach(v.source, module) for v in (o, n))
    r = equivalent(old, new, name, allow_reference_traps=True, timeout_ms=min(timeout_ms, remaining))
    counts = [p for p, t in n.functions[name].params if t.mode == "value" and t.name in UNSIGNED]
    if r["status"] == "unknown" and "unrolling budget" in r.get("reason", "") and counts:
        assume = " && ".join(f"{p} <= {MAX_UNROLL}" for p in counts)  # every trip count the model can bound
        bounded = equivalent(old, new, name, assume=assume, allow_reference_traps=True,
                             timeout_ms=min(timeout_ms, max(1, int((deadline - time.monotonic()) * 1000))))  # fmt: skip
        if bounded["status"] == "counterexample":  # a difference inside the bound is a difference
            return {"class": "behavior-changed", "witness": {"inputs": bounded["counterexample"],
                    "before": bounded["expected"], "after": bounded["actual"], "value_model": "agrees",
                    "found_where": assume}}  # fmt: skip
        if bounded["status"] == "smt-equivalent":
            return {"class": "unknown", "reason": r["reason"], "bounded": {"where": assume, "status": "smt-equivalent"}}
    if r["status"] == "smt-equivalent":
        return {"class": "smt-equivalent", "evidence": {k: r[k] for k in ("quantification", "observation", "trust")}}
    if r["status"] == "counterexample":
        return {"class": "behavior-changed", "witness": {"inputs": r["counterexample"], "before": r["expected"],
                                                         "after": r["actual"], "value_model": "agrees"}}  # fmt: skip
    if r["status"] == "invalid-contract" and "definition" in r.get("reason", ""):
        return {"class": "signature-changed", "reason": r["reason"]}
    return {"class": "unknown", "reason": r.get("reason") or r.get("diagnostic", {}).get("message") or r["status"]}


def classes(o: Version, n: Version, timeout_ms: int, budget_s: float, compilers: list[str], replays: int) -> dict:
    matched = {s: s for s in n.functions.keys() & o.functions.keys()}
    moved = renames(o, n)
    own_same, identical = identities(o, n, {**matched, **moved})
    deadline, out, left = time.monotonic() + budget_s, {}, replays
    for name in sorted(n.own | o.own):
        if name in moved:
            out[name] = {"class": "renamed", "from": moved[name], "deltas": deltas(o, n, moved[name], name)}
            continue
        if name in moved.values():  # reported under its new name
            continue
        if name not in o.functions:
            out[name] = {"class": "added", "signature": signature(n.functions[name])}
            continue
        if name not in n.functions:
            out[name] = {"class": "removed", "signature": signature(o.functions[name])}
            continue
        entry: dict[str, Any] = {"own_code": "identical" if name in own_same else "changed"}
        if name not in own_same and name in o.code and name in n.code:
            bare = {v is o: canonical(name, unguarded(v.code[name]), v.types) for v in (o, n)}
            if bare[True] == bare[False]:
                entry["own_code"] = "changed only in which guards it emits"
        if shape(o.functions[name]) != shape(n.functions[name]):
            entry["class"] = "signature-changed"
        elif name in identical:
            entry["class"] = "identical-code"
        elif o.functions[name].extern or n.functions[name].extern:
            entry |= {"class": "unknown", "reason": "A foreign declaration's body is outside both programs."}
        else:
            limit = max(1.0, min(deadline - time.monotonic(), 20 * timeout_ms / 1000))  # its share, at most
            entry |= isolated(lambda name=name: compare(o, n, name, deadline, timeout_ms), limit)
            if entry["class"] == "unknown" and name in own_same:
                entry["reason"] += " Its own code is identical; something it calls changed."
        if entry["class"] == "behavior-changed" and left > 0:
            left -= 1
            w = entry["witness"]
            native = {cxx: {"before": replay(o, name, w["inputs"], w["before"], cxx),
                            "after": replay(n, name, w["inputs"], w["after"], cxx)} for cxx in compilers}  # fmt: skip
            w["native"] = native
            if any(r.startswith("disagrees") for side in native.values() for r in side.values()):
                entry = {**entry, "class": "unknown", "reason": "The value model and a native build disagree on the "
                         "witness, so no difference is certified."}  # fmt: skip
        elif entry["class"] == "behavior-changed":
            entry["witness"]["native"] = "not replayed: the diff's replay budget ran out"
        if d := deltas(o, n, name, name):
            entry["deltas"] = d
        out[name] = entry
    return out


def single(old: str, new: str, name: str, timeout_ms: int = 3000) -> dict[str, Any]:
    """The class of one function both versions hold: identical-code, or whatever the solver establishes."""
    o, n = version(old), version(new)
    if shape(o.functions[name]) != shape(n.functions[name]):
        return {"class": "signature-changed"}
    _, identical = identities(o, n, {s: s for s in n.functions.keys() & o.functions.keys()})
    if name in identical:
        return {"class": "identical-code"}
    return compare(o, n, name, time.monotonic() + timeout_ms / 1000 * 3, timeout_ms)


def templates(v: Version) -> dict[str, Function]:
    """The program's own generic functions as written; the checker keeps only their instances."""
    return {f.name: f for f in Parser(v.source).parse().functions if f.generics and not f.bindings}


def tokens(v: Version, f: Function) -> list[str]:
    return [t.s for t in lex(v.source[f.start : f.end])]


def header(f: Function) -> tuple:
    return tuple(f.generics), shape(f)


def uninstantiated(o: Version, n: Version, functions: dict[str, Any]) -> dict[str, Any]:
    """A template no code instantiates has no code to compare. Its tokens are compared instead, with everything it
    names: `identical-source` when both are the same, and `unknown` otherwise, never left out of the diff."""
    to, tn = templates(o), templates(n)
    written = {name: tokens(o, to[name]) == tokens(n, tn[name]) for name in to.keys() & tn.keys()}
    changed = {k.rsplit(".", 1)[-1].split("[")[0] for k, e in functions.items() if e["class"] not in UNCHANGED}
    changed |= {k.rsplit(".", 1)[-1] for k, same in written.items() if not same}
    out: dict[str, Any] = {}
    for name in sorted(to.keys() | tn.keys()):
        if name not in to:
            out[name] = {"class": "added", "signature": signature(tn[name]), "template": True}
        elif name not in tn:
            out[name] = {"class": "removed", "signature": signature(to[name]), "template": True}
        elif header(to[name]) != header(tn[name]):
            out[name] = {"class": "signature-changed", "template": True}
        else:
            named = set(tokens(n, tn[name])) & (changed - {name.rsplit(".", 1)[-1]})
            if written[name] and not named:
                out[name] = {"class": "identical-source", "template": True}
            else:
                why = (
                    "its tokens changed" if not written[name] else f"it names {', '.join(sorted(named))}, which changed"
                )
                out[name] = {"class": "unknown", "template": True, "reason": f"A generic function no code "
                             f"instantiates, so there is no code to compare, and {why}."}  # fmt: skip
    return out


def types_of(v: Version) -> dict[str, Any]:
    """The program's own type declarations, each as a comparable value."""
    p, linked = v.p, set(v.p.sources)
    out: dict[str, Any] = {}
    bodies: dict[str, tuple[str, Any]] = {n: ("enum", list(b)) for n, b in p.enums.items()}
    bodies |= {n: ("record", [[f, t.display()] for f, t in b]) for n, b in p.records.items()}
    bodies |= {n: ("sum", [[v, t.display() if t else None] for v, t in b]) for n, b in p.sums.items()}
    for name, (kind, body) in bodies.items():
        if p.modules.get(name, "") not in linked:
            out[name] = {"kind": kind, "body": body, "attributes": sorted(p.attributes.get(name, set())),
                         "extents": p.field_extents.get(name, {})}  # fmt: skip
    return out


def public(v: Version) -> tuple[set[str], set[str]]:
    """The functions and types a user of the program may name."""
    generic = templates(v)
    own_modules = ({v.p.modules.get(f, "") for f in v.own} | {f.module for f in generic.values()}) - {""}
    if not own_modules:  # a program that declares no module: a library build exports every function
        return set(v.own) | set(generic), set(types_of(v))
    functions = {f for f in v.own if v.functions[f].public or v.functions[f].owner}
    functions |= {name for name, f in generic.items() if f.public or f.owner}
    return functions, {t for t in types_of(v) if t in v.p.public}


def semver(o: Version, n: Version, functions: dict[str, Any], types: dict[str, Any]) -> dict[str, Any]:
    fo, to = public(o)
    fn, tn = public(n)
    reasons: list[tuple[str, str]] = []
    unproven = []
    for name, entry in functions.items():
        was, now = name in fo or entry.get("from") in fo, name in fn
        c = entry["class"]
        if c == "removed" and was:
            reasons.append(("major", f"{name} was removed"))
        elif c == "renamed" and was:
            reasons.append(("major", f"{entry['from']} is now named {name}"))
        elif c == "added" and now:
            reasons.append(("minor", f"{name} was added"))
        elif was and not now and c not in {"removed", "added"}:
            reasons.append(("major", f"{name} is no longer public"))
        elif now and not was and c not in {"removed", "added"}:
            reasons.append(("minor", f"{name} is now public"))
        if not (was and now):
            continue
        if c == "signature-changed":
            reasons.append(("major", f"{name}'s signature changed"))
        if added := entry.get("deltas", {}).get("effects", {}).get("added"):
            reasons.append(("major", f"{name}'s effect row gained {', '.join(added)}"))
        if c == "behavior-changed":
            given = ", ".join(f"{k} = {v}" for k, v in entry["witness"]["inputs"].items()) or "no input"
            reasons.append(("major", f"{name} behaves differently at {given}"))
        if c == "unknown":
            unproven.append(name)
    for name in sorted(to - tn - {t for t in types if types[t] == "added"}):
        reasons.append(("major", f"type {name} is no longer public"))
    for name, how in sorted(types.items()):
        if how == "changed" and name in to and name in tn:
            reasons.append(("major", f"type {name}'s definition changed"))
        if how == "added" and name in tn:
            reasons.append(("minor", f"type {name} was added"))
    changed = any(e["class"] not in UNCHANGED for e in functions.values()) or types
    level = max((lvl for lvl, _ in reasons), key=LEVELS.index, default="patch" if changed else "none")
    shown = [why for _, why in sorted(reasons, key=lambda r: -LEVELS.index(r[0]))]
    verdict: dict[str, Any] = {"level": level, "reasons": shown}
    if unproven:  # unknown is never success: an unproven public function may be a change of any size
        verdict["unproven"] = sorted(unproven)
        if level != "major":  # below the top, what the proven facts give is a lower bound, not the level
            verdict["level"], verdict["at_least"] = "unknown", level
    return verdict


def diff(old: str, new: str, *, timeout_ms: int = 3000, budget_s: float = 60.0, replays: int = 8,
         compilers: tuple[str, ...] = ("clang++", "g++"), predict: bool = True) -> dict[str, Any]:  # fmt: skip
    sides = []
    for side, source in (("old", old), ("new", new)):
        try:
            sides.append(version(source))
        except Diagnostic as e:  # the caller places the refusal in the version it came from
            e.data["side"] = side
            raise
    o, n = sides
    present = [cxx for cxx in compilers if shutil.which(cxx)]
    functions = classes(o, n, timeout_ms, budget_s, present, replays)
    functions |= uninstantiated(o, n, functions)
    to, tn = types_of(o), types_of(n)
    types = {t: "added" if t not in to else "removed" if t not in tn else "changed" for t in to.keys() | tn.keys()
             if to.get(t) != tn.get(t)}  # fmt: skip
    tests = {t.replace("test$", ""): "added" if t not in o.tests else "removed" if t not in n.tests else
             "identical-code" if canonical(t, o.code.get(t, ""), o.types) == canonical(t, n.code.get(t, ""), n.types)
             else "changed" for t in o.tests | n.tests}  # fmt: skip
    counts = {c: sum(e["class"] == c for e in functions.values()) for c in ORDER}
    record: dict[str, Any] = {
        "schema": "cairn.diff/1",
        "compiler": VERSION,
        "compared": "two sources, each checked and lowered by this compiler; not two compilers",
        "summary": {c: k for c, k in counts.items() if k},
        "functions": functions,
        "types": dict(sorted(types.items())),
        "tests": dict(sorted(tests.items())),
        "semver": semver(o, n, functions, types),
        "formal_status": "not-verified",
    }
    if predict:
        record["predicted"] = predicted(o, n, functions)
    return record


def holds(record: dict[str, Any], required: str | None) -> bool:
    """Whether every function meets `--require`: identical code, or identical code or proven equivalence."""
    if required is None:
        return True
    allowed = UNCHANGED | ({"smt-equivalent"} if required == "equivalent" else set())
    functions = all(e["class"] in allowed for e in record["functions"].values())
    return functions and not record["types"] and all(how == "identical-code" for how in record["tests"].values())


def predicted(o: Version, n: Version, functions: dict[str, Any]) -> dict[str, Any]:
    """The predicted cost change of every compared function whose code changed; nothing is built or run."""
    compared = {"smt-equivalent", "behavior-changed", "unknown"}
    chosen = {name for name, e in functions.items() if e["class"] in compared and not e.get("template")}
    if not chosen:
        return {}
    try:
        from ..perf import report

        answer = report.delta(o.source, n.source, symbols=chosen)
    except Exception as e:  # a prediction is advice; a model that cannot price a program leaves the diff standing
        return {"unavailable": str(e)}
    return {"predicted": answer["predicted"], "profile": answer["profile"].get("name"),
            "functions": {k: v for k, v in answer["functions"].items() if v}}  # fmt: skip
