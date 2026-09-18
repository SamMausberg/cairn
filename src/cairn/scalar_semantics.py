"""Exact-width, loop-free scalar semantics and solver-backed equivalence.

Supported: bool, fixed integers, locals, assignments, if/else, early returns,
acyclic scalar calls, checked/wrapping arithmetic and checked integer casts.
Excluded: memory, floating point, loops, recursion, static families, records.

This translator and Z3 are trusted. No result is a Lean-kernel proof or a
verification of the C++ backend. Unsupported syntax returns unknown.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cairnc import INT, SIGNED, WIDTH, Diagnostic, Expr, Function, Parser, Stmt, Type, compile_program
from .smt_bridge import Solver, SolverUnavailable

PROFILE = "cairn-scalar-bv/1"
MAX_PATHS = 256
MAX_TERMS = 12000
MAX_SOURCE_BYTES = 64000


class Unsupported(Exception):
    pass


class ConcreteTrap(Exception):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def bounds(ty: str) -> tuple[int, int]:
    w = WIDTH[ty]
    return (-(1 << (w - 1)), (1 << (w - 1)) - 1) if ty in SIGNED else (0, (1 << w) - 1)


def constant(value: int, ty: str) -> str:
    return f"(_ bv{value % (1 << WIDTH[ty])} {WIDTH[ty]})"


def sort(ty: str) -> str:
    return "Bool" if ty == "bool" else f"(_ BitVec {WIDTH[ty]})"


def conj(*parts: str) -> str:
    if "false" in parts:
        return "false"
    xs = list(dict.fromkeys(x for x in parts if x != "true"))
    return "true" if not xs else xs[0] if len(xs) == 1 else "(and " + " ".join(xs) + ")"


def disj(*parts: str) -> str:
    if "true" in parts:
        return "true"
    xs = list(dict.fromkeys(x for x in parts if x != "false"))
    return "false" if not xs else xs[0] if len(xs) == 1 else "(or " + " ".join(xs) + ")"


def neg(p: str) -> str:
    return {"true": "false", "false": "true"}.get(p, f"(not {p})")


def same(a: str, b: str) -> str:
    return "true" if a == b else f"(= {a} {b})"


def ite(c: str, a: str, b: str) -> str:
    if a == b:
        return a
    if c == "true":
        return a
    if c == "false":
        return b
    return f"(ite {c} {a} {b})"


def extend(value: str, source_width: int, dest_width: int, signed: bool) -> str:
    if source_width == dest_width:
        return value
    if source_width > dest_width:
        return f"((_ extract {dest_width - 1} 0) {value})"
    return f"((_ {'sign_extend' if signed else 'zero_extend'} {dest_width - source_width}) {value})"


@dataclass(frozen=True)
class Term:
    ty: str
    value: str
    defined: str = "true"


class Formula:
    def __init__(self, params: list[tuple[str, Type]]):
        self.declarations = []
        self.definitions = []
        self.cache = {}
        self.inputs = {}
        self.variables = {}
        for i, (name, ty) in enumerate(params):
            if ty.mode != "value" or ty.name not in INT | {"bool"}:
                raise Unsupported("Only Boolean and fixed-width integer scalar parameters are supported.")
            n = f"arg_{i}"
            self.declarations.append(f"(declare-const {n} {sort(ty.name)})")
            self.inputs[name] = Term(ty.name, n)
            self.variables[n] = ty.name

    def bind(self, expression: str, ty: str) -> str:
        if expression in {"true", "false"}:
            return expression
        key = (sort(ty), expression)
        if key in self.cache:
            return self.cache[key]
        if len(self.definitions) >= MAX_TERMS:
            raise Unsupported("Symbolic term budget exceeded.")
        n = f"term_{len(self.definitions)}"
        self.definitions.append(f"(define-fun {n} () {sort(ty)} {expression})")
        self.cache[key] = n
        return n

    def term(self, ty: str, value: str, defined: str = "true") -> Term:
        return Term(ty, self.bind(value, ty), self.bind(defined, "bool"))

    def text(self, assertion: str) -> str:
        return "\n".join(["(set-logic QF_BV)", *self.declarations, *self.definitions, f"(assert {assertion})"]) + "\n"


class Symbolic:
    def __init__(self, formula: Formula, functions: dict[str, Function]):
        self.q = formula
        self.functions = functions
        self.visits = 0

    def tick(self):
        self.visits += 1
        if self.visits > MAX_TERMS:
            raise Unsupported("Symbolic evaluation budget exceeded.")

    def expr(self, e: Expr, env: dict[str, Term], stack: tuple[str, ...]) -> Term:
        self.tick()
        if e.ty is None or e.ty.mode != "value" or e.ty.name not in INT | {"bool"}:
            raise Unsupported("Expression is outside the scalar integer/Boolean fragment.")
        ty = e.ty.name
        if e.tag == "name" and isinstance(e.ref, int | Expr):  # A static natural or a module constant.
            e = Expr("int", str(e.ref), ty=e.ty) if isinstance(e.ref, int) else e.ref
        if e.tag == "name":
            return env[e.val]
        if e.tag == "int":
            return Term(ty, constant(int(e.val), ty))
        if e.tag == "bool":
            return Term("bool", e.val)
        if e.tag == "unary":
            a = self.expr(e.args[0], env, stack)
            if e.val == "!":
                return self.q.term("bool", neg(a.value), a.defined)
            if e.val == "~":
                return self.q.term(ty, f"(bvnot {a.value})", a.defined)
            if e.val == "-" and ty in SIGNED:
                ok = neg(same(a.value, constant(bounds(ty)[0], ty)))
                return self.q.term(ty, f"(bvneg {a.value})", conj(a.defined, ok))
            raise Unsupported("Unsupported unary operator.")
        if e.tag == "binary":
            a = self.expr(e.args[0], env, stack)
            b = self.expr(e.args[1], env, stack)
            op = e.val
            both = conj(a.defined, b.defined)
            if op == "&&":
                return self.q.term("bool", conj(a.value, b.value), conj(a.defined, ite(a.value, b.defined, "true")))
            if op == "||":
                return self.q.term("bool", disj(a.value, b.value), conj(a.defined, ite(a.value, "true", b.defined)))
            if op in {"==", "!="}:
                v = same(a.value, b.value)
                return self.q.term("bool", v if op == "==" else neg(v), both)
            if op in {"<", "<=", ">", ">="}:
                if a.ty == "bool":
                    values = {
                        "<": conj(neg(a.value), b.value),
                        "<=": disj(neg(a.value), b.value),
                        ">": conj(a.value, neg(b.value)),
                        ">=": disj(a.value, neg(b.value)),
                    }
                    v = values[op]
                else:
                    suffix = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge"}[op]
                    v = f"(bv{'s' if a.ty in SIGNED else 'u'}{suffix} {a.value} {b.value})"
                return self.q.term("bool", v, both)
            if op in {"&", "|", "^"}:
                fn = {"&": "bvand", "|": "bvor", "^": "bvxor"}[op]
                return self.q.term(ty, f"({fn} {a.value} {b.value})", both)
            if op in {"+", "-", "*"}:
                return self.arithmetic(ty, op, a, b, checked=True)
            if op in {"/", "%"}:
                ok = neg(same(b.value, constant(0, ty)))
                if ty in SIGNED:
                    ok = conj(
                        ok, neg(conj(same(a.value, constant(bounds(ty)[0], ty)), same(b.value, constant(-1, ty))))
                    )
                fn = ("bvsdiv" if ty in SIGNED else "bvudiv") if op == "/" else ("bvsrem" if ty in SIGNED else "bvurem")
                return self.q.term(ty, f"({fn} {a.value} {b.value})", conj(both, ok))
            raise Unsupported("Unsupported binary operator.")
        if e.tag == "call":
            args = [self.expr(a, env, stack) for a in e.args]
            ok = conj(*(a.defined for a in args))
            n = e.val
            if n in INT:
                a = args[0]
                ws = WIDTH[a.ty]
                wt = WIDTH[n]
                common = max(ws, wt) + 1
                v = extend(a.value, ws, wt, a.ty in SIGNED)
                before = extend(a.value, ws, common, a.ty in SIGNED)
                after = extend(v, wt, common, n in SIGNED)
                return self.q.term(n, v, conj(ok, same(before, after)))
            if n in {"add_wrap", "sub_wrap", "mul_wrap"}:
                return self.arithmetic(ty, {"add_wrap": "+", "sub_wrap": "-", "mul_wrap": "*"}[n], *args, checked=False)
            if n in {"shl_wrap", "shr"}:
                a, b = args
                width = WIDTH[ty]
                limit = f"(bvult {b.value} {constant(width, b.ty)})"
                shift = extend(b.value, WIDTH[b.ty], width, False)
                return self.q.term(
                    ty, f"({'bvshl' if n == 'shl_wrap' else 'bvlshr'} {a.value} {shift})", conj(ok, limit)
                )
            if n in {"min", "max"}:
                a, b = args
                cmp = f"(bv{'s' if ty in SIGNED else 'u'}le {a.value} {b.value})"
                return self.q.term(ty, ite(cmp, a.value, b.value) if n == "min" else ite(cmp, b.value, a.value), ok)
            n = e.ref.name if isinstance(e.ref, Function) else n  # The callee the checker resolved.
            if n in self.functions:
                value = self.invoke(n, [Term(a.ty, a.value) for a in args], stack)
                return self.q.term(value.ty, value.value, conj(ok, value.defined))
            raise Unsupported("Call is outside the supported scalar fragment.")
        raise Unsupported("Memory, records and other expression forms are not modeled.")

    def arithmetic(self, ty: str, op: str, a: Term, b: Term, checked: bool) -> Term:
        fn = {"+": "bvadd", "-": "bvsub", "*": "bvmul"}[op]
        value = f"({fn} {a.value} {b.value})"
        ok = conj(a.defined, b.defined)
        if checked:
            w = WIDTH[ty]
            signed = ty in SIGNED
            wide = f"({fn} {extend(a.value, w, 2 * w, signed)} {extend(b.value, w, 2 * w, signed)})"
            ok = conj(ok, same(wide, extend(value, w, 2 * w, signed)))
        return self.q.term(ty, value, ok)

    def invoke(self, name: str, args: list[Term], stack: tuple[str, ...] = ()) -> Term:
        if name in stack:
            raise Unsupported("Recursive functions are not modeled.")
        if len(stack) > 24:
            raise Unsupported("Call-depth limit exceeded.")
        f = self.functions[name]
        if f.static or f.ret.mode != "value" or f.ret.name not in INT | {"bool"}:
            raise Unsupported("Return types/static parameters are outside the scalar fragment.")
        if any(t.mode == "rw" or t.extent or t.name not in INT | {"bool"} for _, t in f.params):
            raise Unsupported("Callee has a nonscalar signature.")  # A read-only scalar borrow reads as its value.
        env = {n: a for (n, _), a in zip(f.params, args, strict=True)}
        returns = []
        remaining = self.block(f.body, [("true", env)], (*stack, name), returns)
        if remaining:
            raise Unsupported("A scalar function fell through without returning.")
        default = "false" if f.ret.name == "bool" else constant(0, f.ret.name)
        value = default
        for path, v in reversed(returns):
            value = ite(path, v.value, value)
        defined = disj(*(path for path, _ in returns))
        return self.q.term(f.ret.name, value, defined)

    def block(self, body: list[Stmt], states: list, stack: tuple[str, ...], returns: list) -> list:
        initial = set(states[0][1]) if states else set()
        for s in body:
            self.tick()
            if len(states) + len(returns) > MAX_PATHS:
                raise Unsupported("Path budget exceeded.")
            next_states = []
            for path, env in states:
                env = dict(env)
                if s.tag in {"let", "reg"}:
                    v = self.expr(s.exprs[0], env, stack)
                    env[s.name] = Term(v.ty, v.value)
                    next_states.append((conj(path, v.defined), env))
                elif s.tag == "assign" and s.exprs[0].tag == "name":
                    v = self.expr(s.exprs[1], env, stack)
                    env[s.exprs[0].val] = Term(v.ty, v.value)
                    next_states.append((conj(path, v.defined), env))
                elif s.tag == "return" and len(s.exprs) == 1:
                    v = self.expr(s.exprs[0], env, stack)
                    returns.append((conj(path, v.defined), v))
                elif s.tag == "if":
                    c = self.expr(s.exprs[0], env, stack)
                    p = conj(path, c.defined)
                    next_states.extend(self.block(s.body, [(conj(p, c.value), dict(env))], stack, returns))
                    next_states.extend(self.block(s.other, [(conj(p, neg(c.value)), dict(env))], stack, returns))
                else:
                    raise Unsupported(
                        "Only scalar locals, assignment, if/else and value returns are modeled; no loops."
                    )
            states = next_states
        return [(p, {n: v for n, v in env.items() if n in initial}) for p, env in states]


class Concrete:
    """Independent operational arithmetic, used to replay SMT counterexamples.

    Uses Python integer operations and explicit representability tests, not the
    SMT formulas. Shares the parser and type annotations with the compiler.
    """

    def __init__(self, functions: dict[str, Function]):
        self.functions = functions

    def checked(self, value, ty):
        lo, hi = bounds(ty)
        if not lo <= value <= hi:
            raise ConcreteTrap("integer-overflow-or-conversion")
        return value

    def expr(self, e, env, stack):
        if e.tag == "name" and isinstance(e.ref, int | Expr):
            e = Expr("int", str(e.ref), ty=e.ty) if isinstance(e.ref, int) else e.ref
        if e.tag == "name":
            return env[e.val]
        if e.tag == "int":
            return int(e.val)
        if e.tag == "bool":
            return e.val == "true"
        ty = e.ty.name
        if e.tag == "unary":
            a = self.expr(e.args[0], env, stack)
            if e.val == "!":
                return not a
            if e.val == "~":
                return (~a) & ((1 << WIDTH[ty]) - 1)
            if e.val == "-":
                return self.checked(-a, ty)
        if e.tag == "binary":
            a = self.expr(e.args[0], env, stack)
            op = e.val
            if op == "&&":
                return a and self.expr(e.args[1], env, stack)
            if op == "||":
                return a or self.expr(e.args[1], env, stack)
            b = self.expr(e.args[1], env, stack)
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
            if op == "<":
                return a < b
            if op == "<=":
                return a <= b
            if op == ">":
                return a > b
            if op == ">=":
                return a >= b
            if op == "&":
                return a & b
            if op == "|":
                return a | b
            if op == "^":
                return a ^ b
            if op == "+":
                return self.checked(a + b, ty)
            if op == "-":
                return self.checked(a - b, ty)
            if op == "*":
                return self.checked(a * b, ty)
            if op in {"/", "%"}:
                if b == 0 or (ty in SIGNED and a == bounds(ty)[0] and b == -1):
                    raise ConcreteTrap("invalid-division")
                q = abs(a) // abs(b)
                if (a < 0) != (b < 0):
                    q = -q
                return q if op == "/" else a - q * b
        if e.tag == "call":
            xs = [self.expr(a, env, stack) for a in e.args]
            n = e.val
            if n in INT:
                return self.checked(xs[0], n)
            if n in {"min", "max"}:
                return (min if n == "min" else max)(*xs)
            if n in {"add_wrap", "sub_wrap", "mul_wrap"}:
                a, b = xs
                v = a + b if n == "add_wrap" else a - b if n == "sub_wrap" else a * b
                return v % (1 << WIDTH[ty])
            if n in {"shl_wrap", "shr"}:
                a, b = xs
                if not 0 <= b < WIDTH[ty]:
                    raise ConcreteTrap("invalid-shift")
                return ((a << b) % (1 << WIDTH[ty])) if n == "shl_wrap" else (a >> b)
            n = e.ref.name if isinstance(e.ref, Function) else n
            if n in self.functions:
                return self.invoke(n, xs, stack)
        raise Unsupported("Concrete replay encountered unsupported expression.")

    def invoke(self, name, args, stack=()):
        if name in stack:
            raise Unsupported("Concrete replay does not recurse.")
        f = self.functions[name]
        env = {n: a for (n, _), a in zip(f.params, args, strict=True)}
        ret, value = self.block(f.body, env, (*stack, name))
        if not ret:
            raise Unsupported("Concrete scalar function did not return.")
        return value

    def block(self, body, env, stack):
        old = set(env)
        for s in body:
            if s.tag in {"let", "reg"}:
                env[s.name] = self.expr(s.exprs[0], env, stack)
            elif s.tag == "assign" and s.exprs[0].tag == "name":
                env[s.exprs[0].val] = self.expr(s.exprs[1], env, stack)
            elif s.tag == "return":
                return True, self.expr(s.exprs[0], env, stack)
            elif s.tag == "if":
                branch = s.body if self.expr(s.exprs[0], env, stack) else s.other
                returned, value = self.block(branch, env, stack)
                if returned:
                    return True, value
            else:
                raise Unsupported("Concrete replay does not execute this statement.")
        for k in set(env) - old:
            del env[k]
        return False, None

    def outcome(self, name, args: dict[str, Any]):
        f = self.functions[name]
        if set(args) != {n for n, _ in f.params}:
            raise ValueError("Wrong scalar argument names.")
        for n, t in f.params:
            if t.name == "bool":
                if type(args[n]) is not bool:
                    raise ValueError("Boolean input required.")
            elif type(args[n]) is not int or not bounds(t.name)[0] <= args[n] <= bounds(t.name)[1]:
                raise ValueError("Integer input outside declared range.")
        try:
            return {"defined": True, "return": self.invoke(name, [args[n] for n, _ in f.params])}
        except ConcreteTrap as e:
            return {"defined": False, "trap": str(e)}


def prepared(source: str):
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise Unsupported("Scalar source limit exceeded.")
    program, _, _ = compile_program(source)  # Linked, monomorphized and typed: instances are plain functions.
    return {f.name: f for f in program.functions}


def outcome_key(outcome):
    return (outcome["defined"], outcome.get("return") if outcome["defined"] else None)


def implementation_hash():
    parent = Path(__file__).parent
    return hashlib.sha256(
        b"".join(
            (parent / n).read_bytes()
            for n in [
                "scalar_semantics.py",
                "smt_bridge.py",
                "cairnc.py",
                "syntax.py",
                "checking.py",
                "expansion.py",
                "codegen.py",
                "modules.py",
                "version.py",
            ]
        )
    ).hexdigest()


def equivalent(
    reference: str,
    candidate: str,
    symbol: str,
    *,
    assume: str = "true",
    allow_reference_traps: bool = False,
    timeout_ms: int = 3000,
    query_log: list | None = None,
) -> dict[str, Any]:
    """Check a fixed reference contract, not a model-editable expected result.

    The default requires reference totality over a nonempty, total domain.
    Explicit allow_reference_traps compares return versus abort observations.
    Counterexamples are independently replayed before being exposed.
    """
    if not all(isinstance(x, str) for x in (reference, candidate, symbol, assume)):
        return {
            "protocol": "cairn.semantic/1",
            "status": "invalid-contract",
            "reason": "Sources, symbol and precondition must be strings.",
            "lean_verified": False,
            "native_verified": False,
        }
    common = {
        "protocol": "cairn.semantic/1",
        "profile": PROFILE,
        "reference_sha256": sha(reference),
        "candidate_sha256": sha(candidate),
        "symbol": symbol,
        "assume": assume,
        "allow_reference_traps": allow_reference_traps,
        "implementation_sha256": implementation_hash(),
        "trust": ["CAIRN parser/typechecker", "scalar SMT translation", "Z3 solver"],
        "lean_verified": False,
        "native_verified": False,
    }
    if type(allow_reference_traps) is not bool:
        return {**common, "status": "invalid-contract", "reason": "Trap policy must be Boolean."}
    try:
        precondition_parser = Parser(assume)
        precondition_parser.expr()
        precondition_parser.need("<eof>")
        refs = prepared(reference)
        cands = prepared(candidate)
        if symbol not in refs or symbol not in cands:
            raise Unsupported("Selected scalar symbol not found.")
        rf = refs[symbol]
        cf = cands[symbol]
        if rf.params != cf.params or rf.ret != cf.ret:
            return {
                **common,
                "status": "invalid-contract",
                "reason": "Candidate signature differs from fixed reference.",
            }
        q = Formula(rf.params)
        left = Symbolic(q, refs).invoke(symbol, list(q.inputs.values()))
        right = Symbolic(q, cands).invoke(symbol, list(q.inputs.values()))
        domain = Term("bool", "true")
        if assume != "true":
            dn = "cairn_domain"
            while dn in refs:
                dn += "x"
            sig = ", ".join(n + ":" + t.display() for n, t in rf.params)
            ds = reference + f"\nfn {dn}({sig})->bool {{return ({assume});}}"
            domains = prepared(ds)
            domain = Symbolic(q, domains).invoke(dn, list(q.inputs.values()))
        input_names = list(q.inputs)
        query_summaries = []
        with Solver(timeout_ms) as solver:

            def run(stage, assertion):
                text = q.text(assertion)
                result = solver.check(text, q.variables)
                query_summaries.append({"stage": stage, **result})
                if query_log is not None:
                    query_log.append({"stage": stage, "smt2": text + "(check-sat)\n", "result": result})
                return result

            def finish(status, **fields):
                return {
                    **common,
                    "status": status,
                    "solver_version": solver.version,
                    "queries": query_summaries,
                    **fields,
                }

            def inputs(result):
                return {n: result["values"][f"arg_{i}"] for i, n in enumerate(input_names)}

            if domain.defined != "true":
                r = run("domain-totality", neg(domain.defined))
                if r["status"] == "sat":
                    return finish("invalid-domain", reason="Precondition may trap.", counterexample=inputs(r))
                if r["status"] != "unsat":
                    return finish("unknown", reason="Domain totality was not established.")
            if domain.value != "true":
                r = run("domain-nonempty", domain.value)
                if r["status"] == "unsat":
                    return finish(
                        "invalid-domain", reason="Precondition admits no inputs; vacuous acceptance rejected."
                    )
                if r["status"] != "sat":
                    return finish("unknown", reason="Nonempty domain was not established.")
            if not allow_reference_traps:
                r = run("reference-totality", conj(domain.value, neg(left.defined)))
                if r["status"] == "sat":
                    return finish(
                        "invalid-reference", reason="Reference traps on an admitted input.", counterexample=inputs(r)
                    )
                if r["status"] != "unsat":
                    return finish("unknown", reason="Reference totality was not established.")
            mismatch = disj(
                neg(same(left.defined, right.defined)),
                conj(left.defined, right.defined, neg(same(left.value, right.value))),
            )
            r = run("equivalence", conj(domain.value, mismatch))
            if r["status"] == "unsat":
                return finish(
                    "smt-equivalent",
                    quantification="All declared-width scalar inputs satisfying the host precondition.",
                    observation="Return value, or one undifferentiated abort outcome; no memory/timing observation.",
                )
            if r["status"] != "sat":
                return finish("unknown", reason="Equivalence solver did not decide the obligation.")
            args = inputs(r)
            expected = Concrete(refs).outcome(symbol, args)
            actual = Concrete(cands).outcome(symbol, args)
            if assume != "true":
                observed = Concrete(domains).outcome(dn, args)
                if not observed["defined"] or not observed["return"]:
                    return finish("unknown", reason="Solver/concrete precondition disagreement.", counterexample=args)
            if outcome_key(expected) == outcome_key(actual):
                return finish(
                    "unknown",
                    reason="Solver/concrete replay disagreed; no semantic rejection is certified.",
                    counterexample=args,
                )
            return finish(
                "counterexample",
                counterexample=args,
                expected=expected,
                actual=actual,
                concrete_replay=True,
                native_replay="not-run",
            )
    except Diagnostic as e:
        return {**common, "status": "rejected", "diagnostic": e.data}
    except Unsupported as e:
        return {**common, "status": "unknown", "reason": str(e), "unsupported_profile": True}
    except (SolverUnavailable, OSError, ValueError, KeyError, RecursionError) as e:
        return {**common, "status": "unknown", "reason": str(e)}
