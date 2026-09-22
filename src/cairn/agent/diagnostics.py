"""What a model reads back when the compiler or the host refuses a reply.

A refusal carries the compiler's diagnostic, where it sits in the reply the model wrote, and the smallest fix
the host can state without guessing: a close name for an unknown one, the construct behind an effect the
ceiling does not allow, the request that discloses a callee. A code whose message already says how to repair
it carries no hint, so nothing is said twice.
"""

from __future__ import annotations

import difflib
from typing import Any

from ..compiler.builtins import TABLE as BUILTINS
from ..compiler.cairnc import Diagnostic

HINTS = {
    "E-MATCH-BINDING": "Bind one fresh immutable value only in an arm whose variant declares a payload.",
    "E-LOOP-CONTROL": "break and continue need an enclosing for or while loop.",
    "E-TYPE-MISMATCH": "Use the expected type; an explicit conversion may trap. Do not change a signature to hide it.",
    "E-IMMUTABLE": "Parameters and let bindings are immutable: copy it into a let mut local and change that.",
    "E-WRITE-LEASE": "This place is not writable here. Do not turn ro into rw: the host owns that contract.",
    "E-SHADOW": "Choose a fresh descriptive name; nothing may shadow another name.",
    "E-COLLECT-CAPACITY": "The collector's extent must be exactly the output's capacity.",
    "E-RETURN": "End every path with a return; there is no implicit tail return.",
    "E-PARSE": "Use braces, semicolons and CAIRN's grammar, not Rust's or Python's.",
    "E-SESSION": "Refresh the packet from the host; never guess a digest or a handle.",
    "E-EFFECT-EXPANSION": "Change the implementation, not the ceiling: the host owns it.",
    "E-PRESERVE": "Keep what the function does; a witness, when the refusal has one, is an input where it differs.",
    "E-SYMBOL": "Name a function or type exactly as a packet or a body shows it.",
    "E-MOVED": "Use it before it moves, move it once, or lend it (ro<T>, rw<T>) instead of passing it by value.",
    "E-LEASED": "Touch it after the wait, or lend each task a part the other does not touch.",
    "E-ALIAS": "Pass parts that visibly meet at one boundary, such as x[0..m] and x[m..n], or read through ro.",
    "E-LINEAR-LEAK": "Pass it to the function that consumes it, or defer that call, on every path.",
    "E-SIGNATURE": "Keep the parameters, return type and ceiling exactly as written; change only the body.",
    "E-DECLARATION": "Write only the one function's body; add or remove no declaration.",
    "E-IMPORT": "Only the project's modules and std.* can be imported.",
    "E-UNBOUND": "Use a name from available_names, or declare it before this use.",
    "E-STACK-LIMIT": "Declare less stack storage, or a buffer if the ceiling allows alloc. Do not hide the cost.",
}
CAUSES = {  # The constructs that bring an effect into a row, for a refusal that names effects.
    "alloc": "a Buf, a buffer or growing a Vec",
    "free": "an owner released at a scope's end or overwritten",
    "zero_init": "zeroed storage",
    "stack_storage": "a stack declaration",
    "local_read": "reading the function's own storage",
    "local_write": "writing the function's own storage",
    "trap": "checked arithmetic, an index, a conversion or a part",
    "diverge": "a call back into the call graph",
    "spawn": "spawn",
    "join": "wait",
    "atomic": "an atomic operation",
    "lock": "entering a mutex",
    "indirect_call": "a call through a function value",
    "dispatch": "a call through dyn",
    "io": "a foreign call declared io",
    "mmio": "mmio_read or mmio_write",
    "asm": "asm",
    "gpu_alloc": "device scratch",
    "gpu_free": "device scratch released",
    "ffi_precondition": "a view parameter's entry guard",
}
CAUSE_FAMILIES = {"read:": "reading ", "write:": "writing ", "ffi:": "the foreign call ", "par:": "a parallel region on ",
                  "transfer:": "a transfer ", "lane:": "lanes calling "}  # fmt: skip


def cause(effect: str) -> str:
    if effect in CAUSES:
        return CAUSES[effect]
    family = next((f for f in CAUSE_FAMILIES if effect.startswith(f)), "")
    return CAUSE_FAMILIES[family] + effect[len(family) :] if family else effect


def close(name: str, names: Any) -> list[str]:
    return difflib.get_close_matches(name, sorted(set(names)), n=2, cutoff=0.6)


def fix(d: dict[str, Any], known: tuple[str, ...] = ()) -> str | None:
    """The deterministic hint for one diagnostic's data: computed where its data allows, else the static one."""
    code = d.get("code")
    if code == "E-UNBOUND" and (near := close(d["message"].removeprefix("Unbound name ").rstrip("."),
                                              d.get("available_names", ()))):  # fmt: skip
        return f"Did you mean {' or '.join(near)}?"
    if code == "E-FIELD" and (near := close(d["message"].removeprefix("Unknown field ").rstrip("."),
                                            d.get("available_fields", ()))):  # fmt: skip
        return f"Did you mean {' or '.join(near)}?"
    if code == "E-ENUM-VARIANT" and (near := close(d["message"].rstrip(".").rsplit(".", 1)[-1],
                                                   d.get("available_variants", ()))):  # fmt: skip
        return f"Did you mean {' or '.join(near)}?"
    if code == "E-CALLEE" and d["message"].startswith("Unknown callable "):
        name = d["message"].removeprefix("Unknown callable ").split(";")[0]
        near = close(name, [*known, *BUILTINS])
        return (f"Did you mean {' or '.join(near)}? " if near else "") + "Expand a function before calling it."
    if code in {"E-EFFECT-EXPANSION", "E-CALLER-EFFECT"} and d.get("added_effects"):
        brought = "; ".join(f"{e}: {cause(e)}" for e in d["added_effects"])
        return f"Remove what brings {brought}. The ceiling is the host's."
    if code == "E-PRESERVE" and isinstance(w := d.get("witness"), dict):
        at = ", ".join(f"{k} = {v}" for k, v in w["inputs"].items()) or "no input"
        seen = [f"returns {x['return']}" if x.get("defined") else "aborts" for x in (w["before"], w["after"])]
        return f"At {at} the function {seen[0]} and the edit {seen[1]}: keep that answer."
    if code == "E-CONTEXT-CLOSURE" and d.get("symbols"):
        return f"Ask first: an expand request naming {', '.join(d['symbols'])}."
    if code == "E-TYPE-MISMATCH" and {"expected_type", "actual_type"} <= set(d):
        want, got = d["expected_type"], d["actual_type"]
        if want in NUMERIC and got in NUMERIC:
            return f"Convert explicitly, {want}(x), which traps outside {want}'s range, or compute in {want}."
        if want == "bool" and got in NUMERIC:
            return "Compare to make a bool: x != 0."
    return HINTS.get(code)


NUMERIC = {"u8", "u16", "u32", "u64", "usize", "i8", "i16", "i32", "i64", "f32", "f64"}


def explain(error: Diagnostic, source: str = "", known: tuple[str, ...] = ()) -> dict[str, Any]:
    """A diagnostic as a model reads it: the refusal, the line it names and the fix, if the host can state one."""
    d = dict(error.data)
    if hint := fix(d, known):
        d["repair_hint"] = hint
    d["automatic_edit"] = False
    line = d.get("line", 0)
    if "source_line" not in d and 1 <= line <= len(source.splitlines()):
        d["source_line"] = source.splitlines()[line - 1][:500]
    d["acceptance_boundary"] = "frontend only; not a semantic or machine proof"
    return d


def offset(text: str, line: int, column: int) -> int:
    lines = text.split("\n")
    return sum(len(x) + 1 for x in lines[: line - 1]) + column - 1 if 1 <= line <= len(lines) else -1


def located(error: Diagnostic, candidate: str, start: int, reply: str) -> Diagnostic:
    """Say where a refusal is in the reply the model wrote, rather than in the source it was spliced into.

    `candidate` holds `reply` at `start`. A position inside the reply becomes a line and column of the reply, with
    that line as `source_line`; a position elsewhere keeps the candidate's own line, since the whole module is
    rechecked and a caller can be what refuses.
    """
    d = error.data
    at = offset(candidate, d.get("line", 0), d.get("column", 0))
    if start <= at < start + len(reply):
        inner = at - start
        d["line"], d["column"] = reply.count("\n", 0, inner) + 1, inner - (reply.rfind("\n", 0, inner) + 1) + 1
        d["in"] = "reply"
        d["source_line"] = reply.split("\n")[d["line"] - 1][:500]
    elif at >= 0:
        d["source_line"] = candidate.split("\n")[d["line"] - 1][:500]
    return error
