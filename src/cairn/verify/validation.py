"""Contract-driven validation: an implementation against its reference, on generated boundary inputs.

`validate` builds the program twice as a host library: once with no selection, where `cf_f` is the reference's own
body and `cf_g` the implementation's, and once with `plan f use g;`, where `cf_f` is the dispatch. Each case from
verify/boundaries.py, after the project's kept regressions, runs the reference, the dispatch on every input, and the
implementation where its condition holds, each call in a process of its own (verify/isolated_calls.py, the
discipline of verify/runner.py), so a trap or a crash is that call's outcome and nothing else's. Two traps agree; a result agrees
bit for bit, or within the host's tolerance for floats.

The reference is an independent algorithm, but both are checked and lowered by this compiler, so agreement is finite
testing on the cases that ran, never proof. A failing case is shrunk while it still fails (extents, offsets, then
values) and kept in the project's regressions file, which `cairn test` replays. Where the SMT fragment covers both
functions, `smt` reports what Z3 established apart from the finite result. Nothing here runs device code: a device
implementation is `unknown` here, and verify/device_validation.py is the `make gpu` path.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, Function, Parser, compile_program, compile_source, write_program
from ..compiler.codegen import mangle
from ..compiler.lexing import IDENT, lex
from ..compiler.tree import FLOAT
from ..projects.toolchain import command as native_command
from ..projects.toolchain import link_flags, linked
from . import boundaries
from .boundaries import Case, Param, Unsupported
from .isolated_calls import Calls

SCHEMA = "cairn.validation/1"
REGRESSIONS = "cairn.regressions/1"
FINITE = (
    "finite-tested: the reference is an independent algorithm, but both are compiled by this compiler, so agreement "
    "on these cases is finite testing, never proof"
)


@dataclass
class Policy:
    """What the host decides and an implementation cannot: the tolerance on float results, the inputs admitted, how
    many cases and with which seed, and how much work shrinking a failure may take."""

    tolerance: dict[str, float] = field(default_factory=lambda: {"absolute": 0.0, "relative": 0.0})
    domain: dict[str, Any] = field(default_factory=dict)
    budget: int = 256
    seed: int = 0
    probes: int = 300
    seconds: int = 5  # one call's limit

    @staticmethod
    def of(value: dict[str, Any] | None) -> Policy:
        value = dict(value or {})
        known = {"tolerance", "domain", "budget", "seed", "probes", "seconds"}
        if set(value) - known:
            raise ValueError(f"A validation policy holds {', '.join(sorted(known))}.")
        policy = Policy(**value)
        tolerance = policy.tolerance
        if set(tolerance) - {"absolute", "relative"} or not all(
            isinstance(v, int | float) and math.isfinite(v) and v >= 0 for v in tolerance.values()
        ):
            raise ValueError("tolerance is {absolute, relative}, each a finite number at least 0.")
        if not (1 <= policy.budget <= 4096 and 0 <= policy.probes <= 4096 and 1 <= policy.seconds <= 60):
            raise ValueError("budget is 1..4096 cases, probes 0..4096 and seconds 1..60.")
        return policy

    def record(self) -> dict[str, Any]:
        return {"tolerance": self.tolerance, "domain": self.domain, "budget": self.budget, "seed": self.seed,
                "probes": self.probes, "seconds": self.seconds}  # fmt: skip


def same(ty: str, a: Any, b: Any, tolerance: dict[str, float]) -> bool:
    if ty not in FLOAT or a == b:
        return a == b
    x, y = float.fromhex(a), float.fromhex(b)
    if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
        return (math.isnan(x) and math.isnan(y)) or x == y
    return abs(x - y) <= tolerance.get("absolute", 0.0) + tolerance.get("relative", 0.0) * abs(y)


def agree(expected: dict[str, Any], actual: dict[str, Any], returns: str, params: list[Param],
          tolerance: dict[str, float]) -> bool | None:  # fmt: skip
    """Whether two outcomes agree; None when either did not finish, which decides nothing."""
    if "timeout" in {expected["outcome"], actual["outcome"]}:
        return None
    if expected["outcome"] != "return" or actual["outcome"] != "return":
        return expected["outcome"] == actual["outcome"] == "trap"
    if returns != "void" and not same(returns, expected["return"], actual["return"], tolerance):
        return False
    types = {p.name: p.ty for p in params}
    return all(
        len(expected["after"][n]) == len(actual["after"][n])
        and all(same(types[n], a, b, tolerance) for a, b in zip(expected["after"][n], actual["after"][n], strict=True))
        for n in expected["after"]
    )


@dataclass
class Subject:
    """The two libraries and the symbols a case calls."""

    name: str  # the reference, as the program names it
    base: str  # the library of the program with no selection of the reference
    selected: str  # the library of the program with `plan reference use implementation;`
    reference: str  # the symbols a case calls
    implementation: str
    applies: str | None  # the implementation's condition as a function, or None when it applies everywhere
    params: list[Param]
    returns: str

    def call(self, calls: Calls, lib: str, symbol: str, case: Case, seconds: int, returns: str | None = None):
        request = {"lib": lib, "symbol": symbol, "params": [p.__dict__ for p in self.params],
                   "returns": self.returns if returns is None else returns,
                   "case": {"args": case.args, "offsets": case.offsets}, "seconds": seconds}  # fmt: skip
        return calls(request)

    def run(self, calls: Calls, case: Case, policy: Policy) -> dict[str, Any]:
        """The reference, the dispatch and, where its condition holds, the implementation, on one case."""
        applies = True
        if self.applies:
            answer = self.call(calls, self.base, self.applies, case, policy.seconds, "bool")
            applies = answer.get("return") is True
        out = {"applies": applies, "reference": self.call(calls, self.base, self.reference, case, policy.seconds),
               "dispatch": self.call(calls, self.selected, self.reference, case, policy.seconds)}  # fmt: skip
        if applies:
            out["implementation"] = self.call(calls, self.base, self.implementation, case, policy.seconds)
        verdicts = [agree(out["reference"], out[k], self.returns, self.params, policy.tolerance)
                    for k in ("implementation", "dispatch") if k in out]  # fmt: skip
        out["agrees"] = None if None in verdicts else all(verdicts)
        return out


def without_selection(source: str, p: Any, reference: str) -> str:
    """`source` with every `plan reference use ...;` taken out, so `cf_reference` is the reference's own body."""
    cuts = sorted(t.start for m, name, _, t in p.selections if full(p, m, name) == reference)
    for start in reversed(cuts):
        end = source.index(";", start) + 1
        source = source[:start] + source[end:]
    return source


def full(p: Any, module: str, name: str) -> str:
    qualified = f"{module}.{name}" if module and "." not in name else name
    return qualified if any(f.name == qualified for f in p.functions) else name


def fresh(p: Any, stem: str) -> str:
    names = {f.name.rsplit(".", 1)[-1] for f in p.functions}
    name = stem
    while name in names:
        name += "_"
    return name


def condition(impl: Function) -> str | None:
    """The checked implementation's condition as source, an instance's naturals written as their values."""
    from ..agent.projection import format_expr
    from ..compiler.implementations import fixed

    when = impl.implements.when if impl.implements is not None else None
    return format_expr(fixed(when)) if when is not None else None


def written_as(p: Any, checked: Function) -> Function:
    """The declaration of `checked` in the parsed program `p`: itself, or for an instance, its template."""
    return next(f for f in p.functions if f.name == (checked.source_name if checked.bindings else checked.name))


def body_of(base: str, declared: Function, checked: Function) -> str:
    """The body of `declared` as written in `base`, an instance's naturals written as the values they have."""
    text = base[declared.body_start : declared.end]
    for token in reversed(lex(text) if checked.bindings else []):
        if token.s in checked.bindings and IDENT.fullmatch(token.s):
            text = text[: token.start] + str(checked.bindings[token.s]) + text[token.end :]
    return text


def subject(source: str, reference: str, implementation: str, cxx: str, directory: Path,
            libraries: tuple[str, ...] = (), objects: tuple[str, ...] = ()) -> tuple[Subject, str]:  # fmt: skip
    """Both libraries built, and the program with no selection (what `smt` compares); `objects` are a project's
    vendored C++, linked into each (projects/foreign.py)."""
    from ..agent.projection import local

    p, _, receipts = compile_program(source)
    fs = {f.name: f for f in p.functions}
    impl = fs.get(implementation)
    if impl is None or receipts.get(implementation, {}).get("implements") != reference:
        raise ValueError(f"{implementation} is not an implementation of {reference}.")
    ref = fs[reference]
    params = boundaries.signature(ref)
    when = condition(impl)
    base = without_selection(source, p, reference)
    p = Parser(base).parse()  # where each declaration now stands
    impl = written_as(p, impl)
    ps = ", ".join(f"{n}:{t.display()}" for n, t in impl.params)
    predicate, applies = "", None
    if when is not None:
        applies = fresh(p, f"applies_{mangle(local(implementation))}")
        predicate = f"\nfn {applies}({ps}) -> bool = {when};\n"
    with_predicate = base[: impl.end] + predicate + base[impl.end :]
    selected = base[: impl.end] + f"\nplan {local(reference)} use {local(implementation)};\n" + base[impl.end :]
    built = {}
    for name, text in (("base", with_predicate), ("selected", selected)):
        cpp, receipt = compile_source(text)
        if "cuda" in receipt["requires"]:
            raise Unsupported("the program holds device code, and nothing runs on a device outside make gpu.")
        where = directory / name
        where.mkdir(parents=True, exist_ok=True)
        write_program(where, "program.cpp", cpp)
        line = native_command(cxx, str(where / "program.cpp"), str(where / "program.so"))
        line[line.index("-o") : line.index("-o")] = objects
        line += link_flags(linked(libraries, receipt["modules"]))
        done = subprocess.run(line, capture_output=True, text=True, timeout=300)
        if done.returncode:
            raise RuntimeError(f"the {name} library did not build: {done.stderr[-2000:]}")
        built[name] = str(where / "program.so")
    module = ref.module + "." if ref.module else ""
    symbol = {"reference": "cf_" + mangle(reference), "implementation": "cf_" + mangle(implementation),
              "applies": "cf_" + mangle(module + applies) if applies else None}  # fmt: skip
    return Subject(reference, built["base"], built["selected"], symbol["reference"], symbol["implementation"],
                   symbol["applies"], params, ref.ret.name), base  # fmt: skip


def shrink(subject: Subject, calls: Calls, case: Case, policy: Policy) -> tuple[Case, dict[str, Any], int]:
    """The smallest failing case found by greedy steps, each kept only while the case still fails: smaller extents
    (views cut to their prefix), aligned views, then each value toward zero. At most `policy.probes` runs."""
    probes = 0
    outcome = subject.run(calls, case, policy)
    extents = [p for p in subject.params if p.kind == "extent"]
    views = [p for p in subject.params if p.kind == "view"]
    least = {p.name: int(policy.domain.get("extents", {}).get(p.name, [0, 0])[0]) for p in extents}

    def fails(candidate: Case) -> bool:
        nonlocal probes, outcome
        if probes >= policy.probes:
            return False
        probes += 1
        found = subject.run(calls, candidate, policy)
        if found["agrees"] is False:
            outcome = found
            return True
        return False

    def resized(c: Case, name: str, size: int) -> Case:
        args = dict(c.args)
        args[name] = size
        for v in views:
            if v.extent == name:
                args[v.name] = list(args[v.name][:size])
        return Case(args, dict(c.offsets), c.why)

    changed = True
    while changed and probes < policy.probes:
        changed = False
        for p in extents:
            n = case.args[p.name]
            for size in sorted({least[p.name], n // 2, n - 1, *range(least[p.name], min(n, least[p.name] + 4))}):
                if least[p.name] <= size < n and fails(trial := resized(case, p.name, size)):
                    case, changed = trial, True
                    break
        if case.offsets and fails(trial := Case(dict(case.args), {}, case.why)):
            case, changed = trial, True
        for p in views:
            values = case.args[p.name]
            zero = 0.0 if p.ty in FLOAT else False if p.ty == "bool" else 0
            if any(v != zero for v in values) and fails(trial := Case({**case.args, p.name: [zero] * len(values)},
                                                                      dict(case.offsets), case.why)):  # fmt: skip
                case, changed = trial, True
                continue
            for i, v in enumerate(values):
                for smaller in simpler(p.ty, v):
                    trial = Case({**case.args, p.name: [*values[:i], smaller, *values[i + 1 :]]}, dict(case.offsets),
                                 case.why)  # fmt: skip
                    if fails(trial):
                        case, changed, values = trial, True, trial.args[p.name]
                        break
        for p in (p for p in subject.params if p.kind == "scalar"):
            for smaller in simpler(p.ty, case.args[p.name]):
                if fails(trial := Case({**case.args, p.name: smaller}, dict(case.offsets), case.why)):
                    case, changed = trial, True
                    break
    return case, outcome, probes


def simpler(ty: str, v: Any) -> list[Any]:
    """Values closer to zero than `v`, the closest to zero first."""
    if ty == "bool":
        return [False] if v else []
    if ty in FLOAT:
        return [x for x in (0.0, 1.0, float(int(v)) if math.isfinite(v) else 0.0) if abs(x) < abs(v)]
    out = [0, 1, v // 2, v - 1] if v > 0 else [0, -1, -((-v) // 2), v + 1] if v < 0 else []
    return list(dict.fromkeys(x for x in out if abs(x) < abs(v)))


def kept(path: Path | None, reference: str) -> tuple[list[Case], dict[str, Any] | None]:
    if path is None or not path.is_file():
        return [], None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != REGRESSIONS or record.get("reference") != reference:
        raise ValueError(f"{path} is not the {REGRESSIONS} record of {reference}.")
    return [Case(c["args"], c.get("offsets", {}), c.get("why", "kept regression")) for c in record["cases"]], record


def keep(path: Path, reference: str, case: Case, implementation: str, policy: Policy) -> bool:
    """Add `case` to the regressions file of `reference`, once; the file is sorted and indented the same on every
    run, so a kept case is one diff line group and never moves."""
    cases, record = kept(path, reference)
    record = record or {"schema": REGRESSIONS, "reference": reference, "policy": policy.record(), "cases": []}
    entry = {"args": case.args, **({"offsets": case.offsets} if case.offsets else {}),
             "why": f"told {implementation} from {reference}"}  # fmt: skip
    if any(c.key() == case.key() for c in cases):
        return False
    record["cases"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def validate(source: str, reference: str, implementation: str, policy: Policy | dict[str, Any] | None = None,
             cxx: str = "clang++", regressions: Path | None = None, libraries: tuple[str, ...] = (),
             smt_timeout_ms: int = 3000, objects: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """The validation record of one implementation: finite results and, apart from them, what Z3 established."""
    policy = policy if isinstance(policy, Policy) else Policy.of(policy)
    record: dict[str, Any] = {"schema": SCHEMA, "reference": reference, "implementation": implementation,
                              "policy": policy.record()}  # fmt: skip
    try:
        p, _, receipts = compile_program(source)
    except Diagnostic as e:
        return {**record, "status": "rejected", "diagnostic": e.data}
    identity = receipts.get(reference, {}).get("implementations", {}).get(implementation, {}).get("identity")
    record["identity"] = identity
    if identity is None:
        return {**record, "status": "invalid", "reason": f"{implementation} is not an implementation of {reference}."}
    if not shutil.which(cxx):
        return {**record, "status": "unknown", "reason": f"{cxx} is not installed."}
    fs = {f.name: f for f in p.functions}
    with tempfile.TemporaryDirectory(prefix="cairn-validate-") as tmp:
        try:
            s, base = subject(source, reference, implementation, cxx, Path(tmp), libraries, objects)
            found = boundaries.tiles(p, fs[implementation], receipts[implementation].get("plan"))
            cases, _ = kept(regressions, reference)
            fresh_cases = boundaries.generate(fs[reference], found, policy.domain, policy.budget, policy.seed)
        except Unsupported as e:
            return {**record, "status": "unknown", "reason": str(e)}
        record["tiles"] = {str(k): v for k, v in sorted(found.items())}
        record["finite"] = finite(s, [*cases, *fresh_cases], len(cases), policy, regressions, implementation)
        record["smt"] = smt(base, reference, implementation, fs[implementation], policy, smt_timeout_ms)
    record["status"] = record["finite"]["status"]
    return record


def finite(s: Subject, cases: list[Case], held: int, policy: Policy, regressions: Path | None,
           implementation: str, vacuous: bool = False) -> dict[str, Any]:  # fmt: skip
    """Run every case until one fails, shrink that one and keep it. Passing needs the implementation itself to have
    run on some case, unless `vacuous` (a replay of kept cases, where the dispatch is what they test)."""
    calls = Calls()
    try:
        out: dict[str, Any] = {"claim": FINITE, "kept_cases": held, "cases": 0, "implementation_ran": 0,
                               "dispatch_ran": 0}  # fmt: skip
        undecided = []
        for i, case in enumerate(cases):
            result = s.run(calls, case, policy)
            out["cases"] += 1
            out["dispatch_ran"] += 1
            out["implementation_ran"] += result["applies"]
            if result["agrees"] is None:
                undecided.append({"case": i, "why": case.why})
            elif not result["agrees"]:
                small, outcome, probes = shrink(s, calls, case, policy)
                out["status"] = "failed"
                out["failed"] = {"inputs": small.args, **({"offsets": small.offsets} if small.offsets else {}),
                                 "found_as": case.why, "shrunk_in": probes,
                                 **{k: outcome[k] for k in ("reference", "implementation", "dispatch") if k in outcome}}  # fmt: skip
                if regressions is not None:
                    out["kept"] = str(regressions) if keep(regressions, s.name, small, implementation, policy) else (
                        f"{regressions} (already held this case)")  # fmt: skip
                return out
        if undecided:
            return {**out, "status": "unknown", "timed_out": undecided[:8]}
        if not out["implementation_ran"] and not vacuous:
            return {**out, "status": "unknown", "reason": "No case met the implementation's condition, so it never "
                    "ran; admit more inputs or widen the condition."}  # fmt: skip
        return {**out, "status": "passed"}
    finally:
        calls.close()


def smt(base: str, reference: str, implementation: str, impl: Function, policy: Policy,
        timeout_ms: int) -> dict[str, Any]:  # fmt: skip
    """What Z3 says of the implementation against the reference where its condition holds, apart from any test:
    the reference's body replaced by the implementation's, compared in the modeled fragment."""
    from ..agent.projection import format_expr
    from ..compiler.implementations import fixed
    from .diff import isolated
    from .scalar_semantics import equivalent
    from .scalar_values import MAX_UNROLL

    parsed = Parser(base).parse()
    ref, mine = next(f for f in parsed.functions if f.name == reference), written_as(parsed, impl)
    candidate = base[: ref.body_start] + body_of(base, mine, impl) + base[ref.end :]
    where = [format_expr(fixed(impl.implements.when))] if impl.implements and impl.implements.when is not None else []
    views = {t.extent for _, t in ref.params if t.extent}
    for name in (n for n, t in ref.params if n in views and t.name == "usize"):
        lo, hi = policy.domain.get("extents", {}).get(name, [0, policy.domain.get("largest_extent", 4096)])
        where.append(f"{name} >= {int(lo)} && {name} <= {int(hi)}")
    assume = " && ".join(f"({w})" for w in where) or "true"

    def ask(condition: str) -> dict[str, Any]:
        return isolated(lambda: equivalent(base, candidate, reference, assume=condition, allow_reference_traps=True,
                                           timeout_ms=timeout_ms), 3 * timeout_ms / 1000 + 5)  # fmt: skip

    counts = [n for n, t in ref.params if t.name == "usize" and t.mode == "value"]
    largest = policy.domain.get("largest_extent", 4096)
    widest = max([largest, *(hi for _, hi in policy.domain.get("extents", {}).values())])
    if counts and widest > MAX_UNROLL:  # a loop over an extent past the unrolling budget is never decided whole
        bounded = " && ".join([f"({assume})", *(f"{n} <= {MAX_UNROLL}" for n in counts)])
        return summary(ask(bounded), bounded, bounded=True)
    return summary(ask(assume), assume)


def summary(r: dict[str, Any], where: str, bounded: bool = False) -> dict[str, Any]:
    status = r.get("status") or r.get("class", "unknown")
    out = {"status": status, "where": where}
    if bounded:
        out["bounded"] = "only where every usize parameter is within the unrolling bound; beyond it nothing is decided"
    if status == "counterexample":
        out |= {k: r[k] for k in ("counterexample", "expected", "actual") if k in r}
    elif status != "smt-equivalent":
        out["reason"] = r.get("reason") or r.get("diagnostic", {}).get("message") or status
    return out


def replay(source: str, record: dict[str, Any], cxx: str = "clang++", libraries: tuple[str, ...] = ()) -> dict:
    """A project's kept regressions, as `cairn test` runs them: every implementation of the reference on every kept
    case, against the reference; generated cases are `cairn validate`'s."""
    reference = record.get("reference", "")
    try:
        receipts = compile_program(source)[2]
    except Diagnostic as e:
        return {"status": "rejected", "diagnostic": e.data}
    implementations = sorted(receipts.get(reference, {}).get("implementations", {}))
    policy = Policy.of({**record.get("policy", {}), "budget": 1})
    cases = [Case(c["args"], c.get("offsets", {}), c.get("why", "")) for c in record.get("cases", [])]
    results = {}
    for implementation in implementations:
        with tempfile.TemporaryDirectory(prefix="cairn-replay-") as tmp:
            try:
                s, _ = subject(source, reference, implementation, cxx, Path(tmp), libraries)
            except (Unsupported, ValueError, RuntimeError) as e:
                results[implementation] = {"status": "unknown", "reason": str(e)}
                continue
            results[implementation] = finite(s, cases, len(cases), policy, None, implementation, vacuous=True)
    ok = all(r["status"] == "passed" for r in results.values())
    status = "passed-finite-tests" if ok else "unknown" if all(r["status"] != "failed" for r in results.values()) else (
        "failed-tests")  # fmt: skip
    return {"status": status, "reference": reference, "cases": len(cases), "implementations": results,
            "claim": FINITE}  # fmt: skip


def local(name: str | None) -> str | None:
    """A function's name without its module; an instance keeps its values."""
    from ..compiler.implementations import local as named

    return named(name) if name else None


def validate_project(project: Any, symbol: str, policy: dict[str, Any] | None = None, cxx: str = "clang++",
                     regressions: Path | None = None, history: Path | None = None) -> dict[str, Any]:  # fmt: skip
    """`cairn validate --symbol g`: g against the function it implements, with the policy given, else the one the
    project's regressions file of that function pinned when it kept its first case, else the defaults. A failing case
    is kept in that file, `regressions/<reference>.json` unless named, which `cairn test` replays once the manifest
    lists it under tests."""
    from ..projects import foreign
    from ..projects.project import ProjectError

    receipts = compile_program(project.source)[2]
    found = sorted(n for n, r in receipts.items() if "implements" in r and symbol in (n, n.rsplit(".", 1)[-1]))
    instances = sorted(n for n, r in receipts.items() if symbol in (r.get("instance_of"), local(r.get("instance_of"))))
    if not found and instances:  # when and tiles change with the values, so each instance is validated on its own
        raise ProjectError(f"{symbol} is validated one instance at a time: name one of "
                           f"{', '.join(local(n) for n in instances)}.")  # fmt: skip
    if len(found) != 1:
        raise ProjectError(f"{symbol} names {'no' if not found else 'more than one'} implementation; write "
                           "fn g(...) implements f ... and name g.")  # fmt: skip
    name, reference = found[0], receipts[found[0]]["implements"]
    path = regressions or project.root / "regressions" / f"{reference}.json"
    _, pinned = kept(path, reference)
    chosen = policy if policy is not None else (pinned or {}).get("policy")
    with tempfile.TemporaryDirectory(prefix="cairn-vendored-") as vendored:  # a foreign implementation's C++
        objects = tuple(foreign.host_objects(project, Path(vendored), cxx)) if project.foreign else ()
        record = validate(project.source, reference, name, chosen, cxx, path, project.libraries, objects=objects)
    relative = path.resolve().relative_to(project.root.resolve()).as_posix() if path.resolve().is_relative_to(
        project.root.resolve()) else str(path)  # fmt: skip
    record["regressions"] = {"file": relative, "exists": path.is_file(),
                             "replayed_by_cairn_test": relative in project.contracts}  # fmt: skip
    if "kept" in record.get("finite", {}):
        record["finite"]["kept"] = record["finite"]["kept"].replace(str(path), relative)
    if history is not None and "finite" in record:
        record["history"] = remembered(history, project.source, reference, name, record)
    return record


def remembered(where: Path, source: str, reference: str, implementation: str, record: dict[str, Any]) -> str:
    """A validation kept in the candidate history (agent/history.py) under the implementation's identity: a validation
    record when it passed, a failure record when it did not; the record's id."""
    from ..agent import history
    from ..agent.implementations import pinned, remember

    ref = next(f for f in Parser(source).parse().functions if f.name == reference)
    finite = record["finite"]
    entry = {"identity": record["identity"], "implementation": implementation, "finite": finite["status"],
             "smt": record["smt"]["status"], **pinned(source[ref.start : ref.end], record["policy"])}  # fmt: skip
    if record["status"] == "passed":
        entry["status"] = "validated"
    else:
        entry |= {"status": "refused", "code": "E-VALIDATION", "why": finite.get("reason") or finite["status"],
                  "inputs": finite.get("failed", {}).get("inputs")}  # fmt: skip
    return remember(where, history.as_written(source, reference), reference, entry)["id"]


def evaluate(source: str, contract: dict[str, Any], cxx: str = "clang++", libraries: tuple[str, ...] = ()) -> dict:
    """One file of a manifest's tests: kept regressions are replayed through the validator, a task contract runs
    through verify/testing.py."""
    if isinstance(contract, dict) and contract.get("schema") == REGRESSIONS:
        return replay(source, contract, cxx, libraries)
    from .testing import evaluate as task

    return task(source, contract, cxx, libraries)
