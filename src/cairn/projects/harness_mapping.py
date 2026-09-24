"""`harness.toml`: which argument of a benchmark feeds which parameter of a CAIRN function, with its dtype and shape,
and how every other extent follows from the axes the shapes name. It is data, checked against the function's
signature before anything is written, and never a script.

```toml
[axes]                        # fixed axes: an integer, or an expression over other axes
hidden_size = 4096

[extents]                     # each usize parameter no argument feeds, as an expression over axes
n = "batch_size * hidden_size"

[[argument]]                  # what the benchmark passes, in its order
name = "hidden_states"
parameter = "x"
dtype = "bfloat16"
shape = ["batch_size", "hidden_size"]

[[argument]]
name = "output"
parameter = "out"
dtype = "bfloat16"
shape = ["batch_size", "hidden_size"]
output = true                 # a destination the benchmark allocated

[[result]]                    # a tensor the adapter allocates and returns
```

An argument without `shape` is a Python number. Every axis a shape names takes its value from `[axes]` or from the
first tensor argument whose shape names it, and every other dimension naming it must agree at the call.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..compiler.tree import BITS, STORAGE, Function, Type, is_view
from .target import refuse

# The element types a tensor may carry into CAIRN: the torch dtype, and at::ScalarType's name for it.
DTYPES = {"f32": ("float32", "kFloat"), "f64": ("float64", "kDouble"), "f16": ("float16", "kHalf"),
          "bf16": ("bfloat16", "kBFloat16"), "f8e4m3": ("float8_e4m3fn", "kFloat8_e4m3fn"),
          "f8e5m2": ("float8_e5m2", "kFloat8_e5m2"), "bool": ("bool", "kBool"), "i8": ("int8", "kChar"),
          "i16": ("int16", "kShort"), "i32": ("int32", "kInt"), "i64": ("int64", "kLong"), "u8": ("uint8", "kByte"),
          "u16": ("uint16", "kUInt16"), "u32": ("uint32", "kUInt32"), "u64": ("uint64", "kUInt64")}  # fmt: skip
SPELLED = {name: cairn for cairn, (name, _) in DTYPES.items()} | {c: c for c in DTYPES} | {"usize": "usize"}
SPELLED |= {"float": "f32", "double": "f64", "half": "f16", "float8_e4m3": "f8e4m3", "int": "i64", "long": "i64"}
# Formats a benchmark names that CAIRN has no type for, refused by name rather than read as bytes of another type.
LACKING = {
    "float4_e2m1": "NVFP4's 4-bit float",
    "float4_e2m1fn_x2": "NVFP4's two 4-bit floats packed in a byte",
    "float8_e8m0fnu": "the 8-bit exponent-only scale of the MX formats",
    "float8_e4m3fnuz": "AMD's E4M3 with one zero and no infinity",
    "float8_e5m2fnuz": "AMD's E5M2 with one zero and no infinity",
    "complex64": "a complex number",
    "complex128": "a complex number",
}
PLACES = {"host", "pinned", "device"}  # a torch tensor lives on the CPU, in pinned CPU memory, or on a CUDA device
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
TOKEN = re.compile(r"\s*(?:(\d+)|([A-Za-z_][A-Za-z0-9_]*)|(.))")
KEYS = {
    "argument": {"name", "parameter", "dtype", "shape", "output"},
    "result": {"name", "parameter", "dtype", "shape"},
}
BENCHMARK = {"definition", "author", "leaderboard", "gpu", "problem"}


def mapping_error(message: str) -> Exception:
    return refuse("E-HARNESS-MAPPING", message)


def dtype(spelled: Any, where: str) -> str:
    """The CAIRN element type a benchmark's dtype names: `bfloat16`, `torch.bfloat16` and `bf16` are one type."""
    text = str(spelled).removeprefix("torch.")
    if text in LACKING:
        raise refuse("E-HARNESS-DTYPE", f"{where} is {text}, {LACKING[text]}, which CAIRN has no type for.")
    if text not in SPELLED:
        raise refuse("E-HARNESS-DTYPE", f"{where} has dtype {spelled!r}; a tensor carries one of "
                     f"{', '.join(n for n, _ in DTYPES.values())} into CAIRN.")  # fmt: skip
    return SPELLED[text]


def expression(text: Any, where: str) -> tuple:
    """An extent as a tree over integers and axis names with `+`, `-`, `*`, `/` and parentheses, or E-HARNESS-MAPPING.
    The binding evaluates it in size_t and refuses a call where a step overflows, goes below zero or divides by
    zero, so an expression never wraps."""
    if isinstance(text, int) and not isinstance(text, bool) and text >= 0:
        return ("int", text)
    if not isinstance(text, str):
        raise mapping_error(f"{where} is an integer or an expression over axes, not {text!r}.")
    tokens = []
    for number, name, other in TOKEN.findall(text):
        if other and other not in "+-*/()":
            raise mapping_error(f"{where} = {text!r}: an extent uses +, -, *, / and parentheses, not {other!r}.")
        tokens.append(("int", int(number)) if number else ("axis", name) if name else ("op", other))
    at = 0

    def take(*ops: str) -> str | None:
        nonlocal at
        if at < len(tokens) and tokens[at][0] == "op" and tokens[at][1] in ops:
            at += 1
            return str(tokens[at - 1][1])
        return None

    def atom() -> tuple:
        nonlocal at
        if take("("):
            inner = total()
            if not take(")"):
                raise mapping_error(f"{where} = {text!r} opens a parenthesis it does not close.")
            return inner
        if at < len(tokens) and tokens[at][0] in {"int", "axis"}:
            at += 1
            return tokens[at - 1]
        raise mapping_error(f"{where} = {text!r} is not an expression over axes.")

    def product() -> tuple:
        tree = atom()
        while op := take("*", "/"):
            tree = (op, tree, atom())
        return tree

    def total() -> tuple:
        tree = product()
        while op := take("+", "-"):
            tree = (op, tree, product())
        return tree

    tree = total()
    if at != len(tokens):
        raise mapping_error(f"{where} = {text!r} is not an expression over axes.")
    return tree


def names(tree: tuple) -> list[str]:
    if tree[0] == "axis":
        return [tree[1]]
    return [] if tree[0] == "int" else [*names(tree[1]), *names(tree[2])]


def shown(tree: tuple) -> str:
    if tree[0] in {"int", "axis"}:
        return str(tree[1])
    left, right = shown(tree[1]), shown(tree[2])
    return f"({left} {tree[0]} {right})"


@dataclass(frozen=True)
class Tensor:
    """One benchmark argument, or one result the adapter allocates, and the CAIRN parameter it feeds."""

    name: str
    parameter: str
    dtype: str
    shape: tuple[str | int, ...] | None  # None: a Python number
    role: str  # input, output (a destination the benchmark passes) or result (the adapter allocates it)
    type: Type

    @property
    def scalar(self) -> bool:
        return self.shape is None

    def record(self) -> dict[str, Any]:
        return {"name": self.name, "parameter": self.parameter, "role": self.role, "dtype": self.dtype,
                "torch_dtype": DTYPES[self.dtype][0] if self.dtype in DTYPES else self.dtype,
                "shape": None if self.shape is None else list(self.shape), "cairn": self.type.display()}  # fmt: skip


@dataclass
class Mapping:
    """A checked mapping: the benchmark's arguments and the adapter's results, each fixed axis and each extent."""

    function: Function
    arguments: list[Tensor]
    results: list[Tensor]
    axes: dict[str, tuple] = field(default_factory=dict)  # fixed axes, as expression trees
    extents: dict[str, tuple] = field(default_factory=dict)  # usize parameters no argument feeds
    benchmark: dict[str, str] = field(default_factory=dict)

    @property
    def tensors(self) -> list[Tensor]:
        return [*self.arguments, *self.results]

    def axis_order(self) -> list[str]:
        """Every axis once, in the order the binding computes it: read from an argument, then fixed, then derived."""
        read = [d for t in self.arguments if not t.scalar for d in t.shape or () if isinstance(d, str)]
        read = [d for d in dict.fromkeys(read) if d not in self.axes]
        order, done = [], set(read)
        pending = dict(self.axes)
        while pending:
            ready = [a for a, tree in pending.items() if set(names(tree)) <= done]
            if not ready:
                raise mapping_error(f"The axes {', '.join(sorted(pending))} depend on each other or on an axis no "
                                    "argument's shape names.")  # fmt: skip
            for a in ready:
                order.append(a)
                done.add(a)
                del pending[a]
        return [*read, *order]

    def record(self) -> dict[str, Any]:
        return {"arguments": [t.record() for t in self.arguments], "results": [t.record() for t in self.results],
                "axes": {a: shown(t) for a, t in self.axes.items()},
                "extents": {e: shown(t) for e, t in self.extents.items()}, "benchmark": self.benchmark}  # fmt: skip


def read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise mapping_error(f"No mapping at {path}: write harness.toml beside the manifest, or name one with "
                            "--mapping.")  # fmt: skip
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeError) as error:
        raise mapping_error(f"{path} is not TOML: {error}.") from error


def tensor(entry: Any, kind: str, index: int, params: dict[str, Type]) -> Tensor:
    where = f"{kind}[{index}]"
    if not isinstance(entry, dict) or set(entry) - KEYS[kind] or not {"name", "parameter", "dtype"} <= set(entry):
        raise mapping_error(f"{where} holds name, parameter and dtype, and may hold {', '.join(sorted(KEYS[kind] - {'name', 'parameter', 'dtype'}))}.")  # fmt: skip
    name, parameter = entry["name"], entry["parameter"]
    if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(parameter, str):
        raise mapping_error(f"{where} names a Python identifier and a CAIRN parameter.")
    if parameter not in params:
        raise mapping_error(f"{where} ({name}) feeds {parameter!r}, which is not a parameter; the function takes "
                            f"{', '.join(params)}.")  # fmt: skip
    shape = entry.get("shape")
    if kind == "result" and shape is None:
        raise mapping_error(f"{where} ({name}) is a tensor the adapter allocates, so it needs a shape.")
    if shape is not None:
        if not isinstance(shape, list) or not all(
            (isinstance(d, str) and NAME.fullmatch(d)) or (isinstance(d, int) and not isinstance(d, bool) and d >= 0)
            for d in shape
        ):
            raise mapping_error(f"{where} ({name}) has a shape of axis names and integers, not {shape!r}.")
        shape = tuple(shape)
    output = entry.get("output", False)
    if not isinstance(output, bool) or (output and shape is None):
        raise mapping_error(f"{where} ({name}): output is true only for a tensor the benchmark passes to be written.")
    role = "result" if kind == "result" else "output" if output else "input"
    return Tensor(name, parameter, dtype(entry["dtype"], f"{where} ({name})"), shape, role, params[parameter])


def fits(t: Tensor) -> None:
    """E-HARNESS-DTYPE or E-HARNESS-MAPPING unless what the benchmark passes is what the parameter takes."""
    ty, said = t.type, f"{t.name} feeds {t.parameter}:{t.type.display()}"
    if t.scalar:
        if ty.mode != "value":
            raise mapping_error(f"{said}, a borrow, but the benchmark passes a number: give it a shape.")
        if ty.name in STORAGE:
            raise refuse("E-HARNESS-DTYPE", f"{said}: a number arrives as a Python float, and rounding it to "
                         f"{ty.name} would be a second rounding the benchmark does not make; take f32 and convert.")  # fmt: skip
        family = {"f32": "float", "f64": "float", "bool": "bool"}.get(ty.name, "int" if ty.name in BITS else "")
        passed = {"f32": "float", "f64": "float", "bool": "bool"}.get(t.dtype, "int")
        if not family:
            raise refuse("E-HARNESS-DTYPE", f"{said}: only scalars cross from Python by value.")
        if family != passed:
            raise refuse("E-HARNESS-DTYPE", f"{said}, but the benchmark passes a {t.dtype}.")
        return
    if ty.mode == "value":
        raise mapping_error(f"{said}, a value, but the benchmark passes a tensor: take a view of it.")
    if ty.name not in DTYPES:
        raise refuse("E-HARNESS-DTYPE", f"{said}: a tensor holds scalars, and {ty.name} has no torch dtype.")
    if t.dtype != ty.name:
        raise refuse("E-HARNESS-DTYPE", f"{said}, whose elements are {ty.name}, but the benchmark's are "
                     f"{DTYPES[t.dtype][0] if t.dtype in DTYPES else t.dtype}.")  # fmt: skip
    if ty.place not in PLACES:
        raise refuse("E-HARNESS-DTYPE", f"{said}: torch has no tensor in {ty.place} memory; take @device or @host.")
    if t.role == "input" and ty.mode == "rw":
        raise mapping_error(f"{said}, which CAIRN writes, but the benchmark passes it as an input: the adapter never "
                            "writes a tensor it was given to read. Mark it output, or take it ro.")  # fmt: skip
    if t.role != "input" and ty.mode == "ro":
        raise mapping_error(f"{said}, which CAIRN only reads, so nothing would write the benchmark's {t.name}.")


def load(path: Path, function: Function, fmt: str) -> Mapping:
    """The mapping at `path`, checked against `function`'s signature and the format's calling convention."""
    data = read(path)
    unknown = set(data) - {"axes", "extents", "argument", "result", "benchmark"}
    if unknown:
        raise mapping_error(f"{path.name} holds axes, extents, argument, result and benchmark, not "
                            f"{', '.join(sorted(unknown))}.")  # fmt: skip
    params = dict(function.params)
    arguments = [tensor(e, "argument", i, params) for i, e in enumerate(data.get("argument", []))]
    results = [tensor(e, "result", i, params) for i, e in enumerate(data.get("result", []))]
    benchmark = data.get("benchmark", {})
    if (
        not isinstance(benchmark, dict)
        or set(benchmark) - BENCHMARK
        or not all(isinstance(v, str) for v in benchmark.values())
    ):
        raise mapping_error(f"[benchmark] holds strings among {', '.join(sorted(BENCHMARK))}.")
    axes = {a: expression(v, f"axes.{a}") for a, v in dict(data.get("axes", {})).items() if NAME.fullmatch(a)}
    extents = {e: expression(v, f"extents.{e}") for e, v in dict(data.get("extents", {})).items()}
    if len(axes) != len(data.get("axes", {})):
        raise mapping_error("An axis is named as a Python identifier.")
    mapping = Mapping(function, arguments, results, axes, extents, dict(benchmark))
    seen: dict[str, str] = {}
    for t in mapping.tensors:
        if t.name in seen.values():
            raise mapping_error(f"Two entries are named {t.name}.")
        if t.parameter in seen:
            raise mapping_error(f"{t.parameter} is fed twice: by {seen[t.parameter]} and by {t.name}.")
        seen[t.parameter] = t.name
        fits(t)
    for e in extents:
        if e not in params or params[e].mode != "value" or params[e].name != "usize":
            raise mapping_error(f"extents.{e} names no usize parameter of {function.name}.")
        if e in seen:
            raise mapping_error(f"{e} is fed twice: by {seen[e]} and by extents.{e}.")
        seen[e] = f"extents.{e}"
    if missing := [n for n in params if n not in seen]:
        raise mapping_error(f"Nothing feeds {', '.join(missing)} of {function.name}: name each in an argument, a "
                            "result or, for a usize, [extents].")  # fmt: skip
    known = set(mapping.axis_order())
    for where, tree in [
        *((f"extents.{e}", t) for e, t in extents.items()),
        *((f"axes.{a}", t) for a, t in axes.items()),
    ]:
        if unbound := [n for n in names(tree) if n not in known]:
            raise mapping_error(f"{where} uses {', '.join(unbound)}, which no argument's shape names and [axes] does "
                                "not fix.")  # fmt: skip
    for t in results:
        if unbound := [d for d in t.shape or () if isinstance(d, str) and d not in known]:
            raise mapping_error(f"result {t.name} has the axes {', '.join(unbound)}, which no argument's shape "
                                "names and [axes] does not fix.")  # fmt: skip
    for t in mapping.tensors:
        extent = t.type.extent
        if is_view(t.type) and not extent.isdigit() and extent not in seen:
            raise mapping_error(f"{t.parameter}'s extent {extent} is fed by nothing.")
    conventions(mapping, fmt)
    return mapping


def conventions(mapping: Mapping, fmt: str) -> None:
    """E-HARNESS-FORMAT unless the mapping calls the kernel as the format calls a submission."""
    outputs = [t for t in mapping.arguments if t.role == "output"]
    if fmt == "sol-execbench" and mapping.results:
        raise refuse("E-HARNESS-FORMAT", "SOL-ExecBench passes every output preallocated, after the inputs "
                     "(destination-passing style): list each as an [[argument]] with output = true, not a [[result]].")  # fmt: skip
    if fmt == "kernelbench" and outputs:
        raise refuse("E-HARNESS-FORMAT", "KernelBench's forward receives only the inputs get_inputs() makes: list "
                     "each output as a [[result]] the adapter allocates and returns.")  # fmt: skip
    if fmt in {"kernelbench", "gpumode"} and not (outputs or mapping.results):
        raise refuse("E-HARNESS-FORMAT", f"A {fmt} submission returns its output: mark an argument output = true or "
                     "add a [[result]].")  # fmt: skip
    if mapping.function.ret.name != "void":
        raise refuse("E-HARNESS-FORMAT", f"{mapping.function.name} returns {mapping.function.ret.display()} to the "
                     "host; a benchmark reads its outputs from tensors, so write the result into an rw view that an "
                     "output or a result feeds.")  # fmt: skip
