"""What a model or a person reads back when the compiler, a host or `cairn` refuses.

Every refusal record names the rule card that owns its code (`teaching.card_of`) and carries the smallest fix the
compiler can state without guessing: a close name for an unknown one, the construct behind an effect the ceiling
does not allow, the request that discloses a callee, the conversion between two types. `taught` adds both to one
record, wherever it is printed. A code whose message already says how to repair it carries no hint, so nothing is
said twice, and a fix that speaks of a host's contract is given only inside a host. A host's refusal also says where
it sits in the reply the model wrote (`located`).
"""

from __future__ import annotations

import difflib
from typing import Any

from ..compiler.builtins import TABLE as BUILTINS
from ..compiler.cairnc import Diagnostic, Parser
from .teaching import TOOL_CARDS, card_of

HINTS = {
    "E-TYPE-MISMATCH": "Use the expected type; an explicit conversion may trap. Do not change a signature to hide it.",
    "E-IMMUTABLE": "Parameters and let bindings are immutable: copy it into a let mut local and change that.",
    "E-WRITE-LEASE": "Write through an rw borrow or a let mut local; never turn ro into rw to get past this.",
    "E-SHADOW": "Choose a fresh descriptive name.",
    "E-RETURN": "End every path with a return; there is no implicit tail return.",
    "E-PARSE": "Use braces, semicolons and CAIRN's grammar, not Rust's or Python's.",
    "E-SESSION": "Refresh the packet from the host; never guess a digest or a handle.",
    "E-EFFECT-EXPANSION": "Change the implementation, not the ceiling: the host owns it.",
    "E-PRESERVE": "Keep what the function does; a witness, when the refusal has one, is an input where it differs.",
    "E-SYMBOL": "Name a function or type exactly as a packet or a body shows it.",
    "E-MOVED": "Use it before it moves, move it once, or lend it (ro<T>, rw<T>) instead of passing it by value.",
    "E-LEASED": "Touch it after the wait, or lend each task a part the other does not touch.",
    "E-ALIAS": "Pass parts that visibly meet at one boundary, such as x[0..m] and x[m..n], or read through ro.",
    "E-COOP-GLOBAL": "Write each outside element from one thread, at an index built as block * width + thread.",
    "E-COOP-BARRIER": "Move the barrier out from under a condition on a thread name; the whole block must reach it.",
    "E-STAGE-UNREADY": "Wait for the stage before reading it, and read it before release.",
    "E-SIGNATURE": "Keep the parameters, return type and ceiling exactly as written; change only the body.",
    "E-DECLARATION": "Write only the one function's body; add or remove no declaration.",
    "E-UNBOUND": "Declare it before this use, or use a name in scope.",
    "E-STACK-LIMIT": "Declare less stack storage, or a buffer if the ceiling allows alloc. Do not hide the cost.",
    "E-REFERENCE": "The reference is pinned: write a new function that implements it.",
    "E-TOLERANCE": "The tolerance is the host's: bring the implementation's result closer to the reference's.",
    "E-TEST-POLICY": "The cases, the seed and the tests are the host's: submit the implementation and its helpers.",
    "E-DOMAIN": "The permitted inputs are the host's: narrow where the implementation applies with when instead.",
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


def fix(d: dict[str, Any], known: tuple[str, ...] = (), host: bool = True) -> str | None:
    """The deterministic hint for one diagnostic's data: computed where its data allows, else the static one. Outside
    a host (`host` false) nothing is said about a host's contract or its expand request."""
    code = d.get("code")
    if not host and card_of(code) in TOOL_CARDS:
        return None
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
        said = f"Did you mean {' or '.join(near)}?" if near else ""
        return (said + " Expand a function before calling it.").strip() if host else said or None
    if code in {"E-EFFECT-EXPANSION", "E-CALLER-EFFECT"} and d.get("added_effects"):
        brought = "; ".join(f"{e}: {cause(e)}" for e in d["added_effects"])
        return f"Remove what brings {brought}. The ceiling is the host's."
    if code == "E-PRESERVE" and isinstance(w := d.get("witness"), dict):
        at = ", ".join(f"{k} = {v}" for k, v in w["inputs"].items()) or "no input"
        seen = [f"returns {x['return']}" if x.get("defined") else "aborts" for x in (w["before"], w["after"])]
        return f"At {at} the function {seen[0]} and the edit {seen[1]}: keep that answer."
    if code == "E-IMPL-EFFECT" and d.get("added_effects"):
        brought = "; ".join(f"{e}: {cause(e)}" for e in d["added_effects"])
        return f"Remove what brings {brought}. The reference's ceiling is the host's."
    if code == "E-VALIDATION" and isinstance(failed := d.get("failed") or (d.get("finite") or {}).get("failed"), dict):
        at = ", ".join(f"{k} = {v}" for k, v in failed["inputs"].items()) or "no input"
        return (f"At {at} the reference {observed(failed['reference'])} and the implementation "
                f"{observed(failed.get('implementation', failed.get('dispatch', {})))}: fix the algorithm for every "
                "input its condition admits, not for that one.")  # fmt: skip
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


def observed(outcome: dict[str, Any]) -> str:
    """One call's outcome as a refusal says it: what it returned and left in each rw view, or how it stopped."""
    if outcome.get("outcome") != "return":
        return {"trap": "traps", "timeout": "runs past its limit"}.get(outcome.get("outcome", ""), "crashes")
    said = [f"returns {outcome['return']}"] if "return" in outcome else []
    said += [f"leaves {name} = {values}" for name, values in outcome.get("after", {}).items()]
    return " and ".join(said) or "returns"


def taught(d: dict[str, Any], known: tuple[str, ...] = (), host: bool = False) -> dict[str, Any]:
    """One refusal record with the card that owns its code and, where the compiler can state one without guessing,
    the smallest fix, and so each refusal under `further`. Every field it had keeps its meaning; `known` are names a
    close one may be taken from."""
    said = dict(d)
    if card := card_of(d.get("code")):
        said["card"] = card
    if hint := fix(d, known, host):
        said["repair_hint"] = hint
    if isinstance(d.get("further"), list):
        said["further"] = [taught(f, known, host) for f in d["further"]]
    return said


def declared(source: str) -> tuple[str, ...]:
    """The functions `source` declares, by full and local name, for a close name to come from; none if it does not
    parse."""
    try:
        functions = Parser(source).parse().functions
    except Diagnostic:
        return ()
    return tuple(sorted({n for f in functions for n in (f.name, f.name.rsplit(".", 1)[-1])}))


def explain(error: Diagnostic, source: str = "", known: tuple[str, ...] = (), host: bool = True) -> dict[str, Any]:
    """A diagnostic as a model reads it: the refusal, the line it names, its card and the fix, if one can be stated."""
    d = taught(error.data, known, host)
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
