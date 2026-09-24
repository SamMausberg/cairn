"""The machine: `mmio_read`, `mmio_write`, `asm` and typed assembly, their rules beside their lowering.

Each reaches past the checker, so each is legal only inside `unsafe { }` and names what it does in the row: `mmio`
for a register access, `asm` for a string of instructions whose effects the checker cannot see.

Typed assembly states what the string form cannot: its target, its operands and its effects.

    asm ptx sm_75 "mul.hi.u32 %0, %1, %2;" (out hi:u32, a, b);
    asm x86_64 "bswapq %0" (out r:u64 = x);
    asm x86_64 "mulq %2" (out lo:u64 = a, b) clobbers(rdx);
    asm ptx sm_75 "ld.global.nc.u32 %0, [%1];" (out v:u32, xs) effects(read:xs);

- The target is `ptx`, which runs only in device code (a device lane or a `kernel fn`) and names the architecture
  it needs, or `x86_64` or `aarch64`, which run only in host code. Checking says nothing of the machine: a build
  refuses host assembly of another family and PTX its device target does not satisfy (`unbuildable`), and the
  device pass tests the architecture again.
- Operands are numbered as written, outputs first, and the template names each one at least once, `%N`, and no
  other (`%%` is a literal percent sign). Host templates may put one letter between them, `%k0` or `%w0`.
- Each operand's constraint follows from its type (CONSTRAINTS); a type with none, `bool` everywhere and `u8` in
  PTX, is refused. An output `out name:T` binds a fresh immutable local; `out name:T = e` starts it at `e`.
  `clobbers(...)` names the host registers written besides the outputs, from a closed table (REGISTERS).
- A view or a local array given as an input passes its address. The statement must declare `read:x` or
  `write:x` for it, which lends it for the statement and puts the access in the row; any declared effect makes
  the lowering `volatile` with a `"memory"` clobber, and so does having no output. `volatile` written after
  `asm` keeps one whose outputs depend on more than its inputs, such as a clock.
- The declared effects are trusted as written: nothing checks that the instructions do only that. The row gets
  them and `asm:TARGET`, and the receipt lists each statement under `assembly`.
- In device code a statement may declare reads and writes and `fence`, nothing else. What it reaches through an
  address obeys the lane rule as a whole-array access does: an array any lane writes cannot be reached this way.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .scope import Binding
from .tree import BITS, HOST_VISIBLE, UNSIGNED, USIZE, VOID, Expr, Function, Stmt, Type, fail, is_view, nested

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

NAMES = {"mmio_read", "mmio_write", "asm"}
HOSTS = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}  # platform.machine()
FAMILIES = {"x86_64": "x86-64", "aarch64": "armv8-a"}  # each host target's family, as projects/toolchain.py names it
# The register class each scalar travels in, per target: NVIDIA's inline PTX has no 8-bit class and no predicate
# operand, so u8, i8 and bool have no constraint there; bool has none anywhere, since an output could hold 2.
PTX = {**dict.fromkeys(("u16", "i16"), "h"), **dict.fromkeys(("u32", "i32"), "r"), "f32": "f", "f64": "d"}
CONSTRAINTS = {
    "ptx": {**PTX, **dict.fromkeys(("u64", "i64", "usize"), "l")},
    "x86_64": {**dict.fromkeys(BITS, "r"), "f32": "x", "f64": "x"},
    "aarch64": {**dict.fromkeys(BITS, "r"), "f32": "w", "f64": "w"},
}
ADDRESS = {"ptx": "l", "x86_64": "r", "aarch64": "r"}  # the register an array's address travels in
# The registers a host statement may say it writes besides its outputs. Not the stack or frame pointer, and not
# AArch64's x18, which the platform reserves; PTX registers are virtual and never clobbered.
GENERAL = {"rax", "rbx", "rcx", "rdx", "rsi", "rdi", *(f"r{i}" for i in range(8, 16))}
REGISTERS = {
    "ptx": set(),
    "x86_64": GENERAL | {f"xmm{i}" for i in range(16)},
    "aarch64": {*(f"x{i}" for i in range(29) if i != 18), *(f"v{i}" for i in range(32))},
}
DECLARABLE = {"fence", "barrier", "atomic", "io", "mmio"}  # besides read:x and write:x of an address operand
IN_DEVICE = {"fence"}  # besides reads and writes, what typed PTX may declare in a device lane or a kernel
CAPABILITY = re.compile(r"sm_([1-9][0-9]{1,2})([af]?)\Z")
LIMIT = 30  # operands GCC and nvcc accept in one statement


def check_machine(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """mmio_read mmio_write asm: target access is audited, never silently safe."""
    n = e.val
    if not c.unsafe_depth:
        fail("E-UNSAFE", f"{n} touches the machine directly; use it inside an unsafe block.", e)
    c.effect("asm" if n == "asm" else "mmio")
    if n == "asm":
        if len(args) != 1 or args[0].tag != "str":
            fail("E-ARITY", "asm takes one string literal of target instructions.", e)
        return VOID
    ty = c.resolve(targs[0], e) if len(targs) == 1 else expected
    if ty is None or ty.name not in UNSIGNED:
        fail("E-INFER", f"Write {n}[u8|u16|u32|u64] with the register width.", e)
    count = 1 + (n == "mmio_write")
    if len(args) != count:
        fail("E-ARITY", f"{n} takes an address" + (" and a value." if n == "mmio_write" else "."), e)
    c.expr(args[0], USIZE)
    if n == "mmio_write":
        c.expr(args[1], ty)
    e.ref = ("builtin", ty)
    return ty if n == "mmio_read" else VOID


def lower_machine(g: Emitter, e: Expr) -> str:
    if e.val == "asm":
        return f"__asm__({g.quoted(e.args[0].val)})"
    register = f"*reinterpret_cast<volatile {g.type(e.ref[1])}*>({g.expr(e.args[0])})"  # One access, exact width.
    return f"static_cast<{g.type(e.ty)}>({register})" if e.val == "mmio_read" else f"({register} = {g.expr(e.args[1])})"


@dataclass
class Operand:
    """One operand as the checker typed it: `out`, `inout`, `in` or `address`, its type and its constraint."""

    kind: str
    ty: Type
    letter: str
    name: str = ""  # the local an output binds


def host() -> str:
    return HOSTS.get(platform.machine(), platform.machine())


def satisfies(target: str, capability: str) -> bool:
    """Whether code built for the device architecture `target` (sm_120, sm_90a) may run PTX that needs `capability`:
    a plain sm_N runs on N and later; sm_Na only on sm_Na; sm_Nf on the architectures of N's family from N on."""
    have, need = CAPABILITY.fullmatch(target), CAPABILITY.fullmatch(capability)
    if have is None or need is None:
        return False
    (h, hk), (n, nk) = (int(have[1]), have[2]), (int(need[1]), need[2])
    if nk == "a":
        return (h, hk) == (n, "a")
    if nk == "f":
        return hk in {"a", "f"} and h // 10 == n // 10 and h >= n
    return h >= n


def requirement(capability: str) -> str:
    """The preprocessor condition under which a device pass has what `capability` names (the rule of `satisfies`)."""
    need = CAPABILITY.fullmatch(capability)
    assert need is not None
    arch, kind = int(need[1]) * 10, need[2]
    if kind == "a":
        return f"defined(__CUDA_ARCH_SPECIFIC__) && __CUDA_ARCH_SPECIFIC__ == {arch}"
    if kind == "f":
        family = "__CUDA_ARCH_FAMILY_SPECIFIC__"
        return f"defined({family}) && {family} >= {arch} && {family} < {arch // 100 * 100 + 100}"
    return f"__CUDA_ARCH__ >= {arch}"


def references(s: Stmt, count: int) -> None:
    """Every operand is named in the template, `%N` or on a host `%kN`, and nothing else is."""
    a, used, text, i = s.assembly, set(), s.assembly.template, 0
    while (i := text.find("%", i)) >= 0:
        if text.startswith("%%", i):
            i += 2
            continue
        m = re.compile(r"([a-zA-Z]?)([0-9]+)").match(text, i + 1)
        if m is None:
            fail("E-ASM-OPERANDS", "A template names an operand as %0, %1, ...; write %% for a literal %.", s)
        if m[1] and a.target == "ptx":
            fail("E-ASM-OPERANDS", f"PTX takes no operand modifier: write %{m[2]}, not %{m[0]}.", s)
        used.add(int(m[2]))
        i = m.end()
    if extra := sorted(n for n in used if n >= count):
        fail("E-ASM-OPERANDS", f"The template names %{extra[0]}, and this asm has {count} operand"
             f"{'s' * (count != 1)}, %0 to %{count - 1}." if count else f"The template names %{extra[0]}, and this "
             "asm has no operands.", s)  # fmt: skip
    if unused := sorted(set(range(count)) - used):
        fail("E-ASM-OPERANDS", f"Operand %{unused[0]} is never named in the template.", s)


def placed(c: Checker, s: Stmt) -> bool:
    """Whether the statement is device code, and that its target runs there."""
    a, device = s.assembly, c.device_depth > 0
    if a.target not in CONSTRAINTS:
        fail("E-ASM-TARGET", f"asm names its target: ptx, x86_64 or aarch64, not {a.target}.", s)
    if a.target == "ptx":
        if not device:
            fail("E-ASM-TARGET", "PTX runs on the device: write it in a kernel fn or a lane of a region that indexes an "
                 "@device view.", s)  # fmt: skip
        if not CAPABILITY.fullmatch(a.capability):
            fail("E-ASM-TARGET", "PTX names the architecture it needs after ptx, as sm_75, sm_90a or sm_100f.", s)
        return True
    if a.capability:
        fail("E-ASM-TARGET", f"{a.target} assembly runs on the host that builds it and names no architecture.", s)
    if device:
        fail("E-ASM-TARGET", f"{a.target} assembly runs on the host; device code takes asm ptx.", s)
    return False


def unbuildable(requires: list[str], family: str, device: str = "") -> str:
    """Why a program whose receipt requires these cannot be built on a host of `family` for the device target
    `device` (projects/target.py), or "" when it can."""
    wrong = [r[4:] for r in requires if r.startswith("asm:") and FAMILIES[r[4:]] != family]
    if wrong:
        return (
            f"This program holds {wrong[0]} assembly, which builds on an {FAMILIES[wrong[0]]} host; this is {family}."
        )
    short = [r[4:] for r in requires if r.startswith("ptx:") and not satisfies(device, r[4:])]
    return f"This program's PTX needs {short[0]}, and the device target is {device}." if short else ""


def s_asm(c: Checker, s: Stmt):
    """Typed assembly: its target, its operands and their constraints, the effects it declares."""
    a = s.assembly
    if not c.unsafe_depth:
        fail("E-UNSAFE", "asm touches the machine directly; use it inside an unsafe block.", s)
    device = placed(c, s)
    table, operands = CONSTRAINTS[a.target], []
    count = len(a.outputs) + len(s.exprs) - sum(started for _, _, started, _, _ in a.outputs)
    if count > LIMIT:
        fail("E-ASM-OPERANDS", f"An asm takes at most {LIMIT} operands; this one has {count}.", s)
    written = {x.partition(":")[2] for x in a.effects if x.startswith("write:")}
    read = {x.partition(":")[2] for x in a.effects if x.startswith("read:")}
    starts = iter(s.exprs)
    for name, declared, started, line, col in a.outputs:
        ty = c.resolve(declared, s)
        if ty.mode != "value" or ty.name not in table:
            refuse(a.target, ty, Expr("name", name, line=line, col=col))
        if started:
            start = next(starts)
            c.expect(c.expr(start, ty), ty, start)
        operands.append(Operand("inout" if started else "out", ty, table[ty.name], name))
    borrows: list[tuple[str, str]] = []
    addressed: set[str] = set()
    for e in starts:
        bound = c.env.get(e.val) if e.tag == "name" else None
        if bound is not None and (is_view(bound.ty) or bound.ty.name in {"Buf", "Array"}):
            operands.append(address(c, s, e, e.val in written, device, borrows))
            addressed.add(e.val)
            continue
        ty = c.expr(e)
        if ty.mode != "value" or ty.name not in table:
            refuse(a.target, ty, e)
        operands.append(Operand("in", ty, table[ty.name]))
    c.disjoint(borrows, s)
    references(s, count)
    for register in a.clobbers:
        if register not in REGISTERS[a.target] or a.clobbers.count(register) > 1:
            named = " ".join(sorted(REGISTERS[a.target], key=lambda r: (r.rstrip("0123456789"), len(r), r)))
            fail("E-ASM-CLOBBER", f"{a.target} names each register it clobbers once, from: {named or 'none'}; "
                 f"{register} is not one." if named else "PTX registers are virtual: a PTX asm clobbers none.", s)  # fmt: skip
    for effect in a.effects:
        kind, _, what = effect.partition(":")
        if kind in {"read", "write"} and what not in addressed:
            fail("E-ASM-EFFECT", f"{effect} names no address operand of this asm; pass {what} as an input.", s)
        if kind not in {"read", "write"} and effect not in DECLARABLE:
            fail("E-ASM-EFFECT", f"asm declares reads and writes of its address operands and "
                 f"{', '.join(sorted(DECLARABLE))}; {effect} is none of them.", s)  # fmt: skip
        if device and kind not in {"read", "write"} and effect not in IN_DEVICE:
            fail("E-ASM-LANE", f"Typed PTX in device code may declare reads, writes and fence; {effect} is not "
                 "lane-safe.", s)  # fmt: skip
    if undeclared := sorted(addressed - read - written):
        fail("E-ASM-EFFECT", f"{undeclared[0]} is passed by address: declare read:{undeclared[0]} or "
             f"write:{undeclared[0]}.", s)  # fmt: skip
    for effect in a.effects:
        c.effect(effect)
    c.effect("asm:" + a.target)
    c.counts["assembly"] = c.counts.get("assembly", 0) + 1
    for (name, _, _, line, col), operand in zip(a.outputs, operands, strict=False):
        c.bind(name, Binding(operand.ty), Expr("name", name, line=line, col=col))
    s.ref = operands


def address(c: Checker, s: Stmt, e: Expr, write: bool, device: bool, borrows: list) -> Operand:
    """An array passed by its address: lent for the statement, read or written as declared, where the code runs."""
    ty = c.view_argument(e)
    if write and not c.writable(e):
        fail("E-WRITE-LEASE", f"write:{e.val} needs an rw view or a mutable local array; {e.val} is read-only.", e)
    reach = {"device", "unified"} if device else HOST_VISIBLE
    if ty.place not in reach:
        fail("E-PLACEMENT", f"{e.val} is {ty.place} memory, which {'device' if device else 'host'} code cannot "
             "reach.", e)  # fmt: skip
    c.lend(e, "rw" if write else "ro", borrows, elements=True)
    return Operand("address", ty, ADDRESS[s.assembly.target], e.val)


def refuse(target: str, ty: Type, node: Any):
    kinds = " ".join(sorted(CONSTRAINTS[target]))
    fail("E-ASM-CONSTRAINT", f"{ty.display()} has no {target} register constraint; an operand is one of {kinds}, "
         "or a view or local array passed by its address.", node)  # fmt: skip


def lower_asm(g: Emitter, s: Stmt, es: list[str]):
    """Outputs declared where their names live on, each input evaluated once in order, then the statement. PTX sits
    under the device pass with its architecture checked there; the host pass of a device function never runs it."""
    a, ptx = s.assembly, s.assembly.target == "ptx"
    outs, ins, before, at = [], [], [], 0
    for op in s.ref:
        if op.kind == "out":
            g.put(f"{g.type(op.ty)} v_{op.name}{{}};")
            outs.append(f'"={"" if ptx else "&"}{op.letter}"(v_{op.name})')  # never an input's register on a host
            continue
        text, at = es[at], at + 1
        if op.kind == "inout":
            g.put(f"{g.type(op.ty)} v_{op.name} = {text};")
            outs.append(f'"+{op.letter}"(v_{op.name})')
            continue
        temporary = g.fresh("cr_asm_")[0]
        before.append(f"auto const {temporary} = {g.pointer(s.exprs[at - 1])[0] if op.kind == 'address' else text};")
        ins.append(f'"{op.letter}"({temporary})')
    clobbers = [f'"{r}"' for r in a.clobbers] + ([] if ptx else ['"cc"']) + (['"memory"'] if a.effects else [])
    volatile = " volatile" if a.volatile or a.effects or not a.outputs else ""
    parts = [g.quoted(a.template), ", ".join(outs), ", ".join(ins), ", ".join(clobbers)]
    while len(parts) > 1 and not parts[-1]:
        parts.pop()
    statement = f"__asm__{volatile}({' : '.join(parts)});"
    if not ptx:
        return g.puts(*before, statement)
    # Both passes evaluate every input, so a lane body holding the statement reads the same variables in the same
    # order on the host as on the device: nvcc launches an extended lambda's kernel only when its captures agree.
    wanted = g.quoted(f"The PTX {g.site(s.line)} needs {a.capability}; this device pass is for another architecture")
    held = [line.split()[2] for line in before] + [f"v_{op.name}" for op in s.ref if op.kind in {"out", "inout"}]

    def statements():
        g.puts(*before, "#if defined(__CUDA_ARCH__)", f"#if !({requirement(a.capability)})", f"#error {wanted}")
        g.puts("#endif", statement, "#else", *(f"static_cast<void>({name});" for name in held), "cr::trap();")
        g.put("#endif")

    g.nest("{", statements)


def records(f: Function) -> list[dict[str, Any]]:
    """What each typed assembly statement of `f` declares, for the receipt: trusted as written, never checked."""
    out = []

    def closures(e: Expr):
        if e.tag == "lambda" and isinstance(e.ref, Function):
            walk(e.ref.body)
        for x in e.args:
            closures(x)

    def walk(ss: list[Stmt]):
        for s in ss:
            if s.tag == "asm" and s.assembly is not None:
                a = s.assembly
                out.append({"line": s.line, "target": a.target, **({"needs": a.capability} if a.capability else {}),
                            "effects": list(a.effects), "volatile": bool(a.volatile or a.effects or not a.outputs),
                            "trust": "declared-not-checked"})  # fmt: skip
            for e in s.exprs:
                closures(e)
            walk(nested(s))

    walk(f.body)
    return out
