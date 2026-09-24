"""`cairn new DIR --from-sol-execbench definition.json`: a library project for one SOL-ExecBench problem.

It writes what the problem's files already say, so an agent starts from the benchmark's own contract: the reference
function's signature, with a `usize` per variable axis, a constant per fixed one, a view per tensor on the device and
a value per scalar; `harness.toml`, which maps the definition's inputs and outputs onto that signature in the order the
evaluator passes them; `policy.json`, the validation policy with the tightest tolerance any workload states; and the
problem's files, copied. The reference's body is the agent's to write, beside the PyTorch reference it prints as a
comment. What CAIRN's policy cannot state of the benchmark's check is said in `harness.toml` and in the answer.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..compiler.lexing import RESERVED
from .harness_mapping import DTYPES, LACKING, SPELLED
from .new import guide
from .project import ProjectError
from .target import parse

DEFAULTS = {"max_atol": 1e-2, "max_rtol": 1e-2, "required_matched_ratio": 0.99, "max_error_cap": None,
            "allow_negative_inf": False}  # fmt: skip  # SOL-ExecBench's own defaults (docs/workload.md)
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def refused(message: str) -> ProjectError:
    return ProjectError(f"This definition cannot become a CAIRN project as it stands: {message}")


def name(text: str) -> str:
    """A CAIRN name for a definition's axis or tensor: the same word, or with a trailing underscore when CAIRN
    reserves it."""
    if not IDENTIFIER.fullmatch(text):
        raise refused(f"{text!r} is not an identifier.")
    return text + "_" if text in RESERVED else text


def element(spelled: str, what: str) -> str:
    if spelled in LACKING:
        raise refused(f"{what} is {spelled}, {LACKING[spelled]}, which CAIRN has no type for.")
    if spelled not in SPELLED or SPELLED[spelled] not in DTYPES:
        raise refused(f"{what} has dtype {spelled!r}.")
    return SPELLED[spelled]


def tolerance(workloads: list[dict[str, Any]]) -> tuple[dict[str, float], list[str], dict[str, Any]]:
    """The validation tolerance, the tightest any workload states, what the benchmark checks that it cannot say,
    and where it came from."""
    stated = [dict(DEFAULTS, **(w.get("tolerance") or {})) for w in workloads] or [dict(DEFAULTS)]
    absolute = min(float(t["max_atol"]) for t in stated)
    relative = min(float(t["max_rtol"]) for t in stated)
    ratio = max(float(t["required_matched_ratio"]) for t in stated)
    caps = sorted({float(t["max_error_cap"]) for t in stated if t.get("max_error_cap") is not None})
    notes = [
        f"required_matched_ratio {ratio}: CAIRN's validation requires every element to agree, which is stricter",
        "SOL-ExecBench fails any NaN or infinity in the output or the reference, and an all-zero output where the "
        "reference is not zero; CAIRN agrees a NaN with a NaN and an infinity with itself",
        "SOL-ExecBench compares both in f32; CAIRN widens both exactly to f64",
    ]
    if caps:
        notes.append(f"max_error_cap {caps[0]}: CAIRN's policy has no cap on the largest error")
    if any(t.get("allow_negative_inf") for t in stated):
        notes.append("allow_negative_inf: CAIRN already agrees -inf with -inf")
    source = {"workloads": len(workloads), "stated": sum(1 for w in workloads if w.get("tolerance")),
              "defaults": "max_atol 0.01, max_rtol 0.01, required_matched_ratio 0.99"}  # fmt: skip
    return {"absolute": absolute, "relative": relative}, notes, source


def axis_expression(text: str, where: str) -> str:
    """A definition's `expr` axis in harness.toml's arithmetic: `//` is size_t division there; `%` and `**` are not
    offered."""
    spelled = text.replace("//", "/")
    if re.search(r"[^\sA-Za-z0-9_+\-*/()]", spelled):
        raise refused(f"{where} = {text!r} uses an operator harness.toml does not offer (+, -, *, //, parentheses).")
    return spelled


def load_problem(definition: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        spec = json.loads(definition.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise ProjectError(f"{definition} is not a SOL-ExecBench definition.json: {error}") from error
    if not isinstance(spec, dict) or not {"name", "axes", "inputs", "outputs"} <= set(spec):
        raise ProjectError(f"{definition} holds no name, axes, inputs and outputs: it is not a definition.json.")
    workload_file = definition.with_name("workload.jsonl")
    workloads = []
    if workload_file.is_file():
        workloads = [
            json.loads(line) for line in workload_file.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    return spec, workloads


def create(destination: Path, definition: Path, device_target: str | None = None) -> dict[str, Any]:
    """Write the project; nothing is written when the definition cannot become one."""
    spec, workloads = load_problem(definition)
    target = parse(device_target or "sm_100a").name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", destination.name):
        raise ProjectError("Choose an ASCII project name of 1..64 characters.")
    function = name(re.sub(r"\W", "_", str(spec.get("op_type") or "reference")).lower() or "reference")
    axes = dict(spec["axes"])
    constants: list[str] = []
    fixed: dict[str, int | str] = {}  # the mapping's [axes]: a fixed value, or an expression over other axes
    extents: dict[str, str] = {}
    params: list[str] = []
    arguments: list[dict[str, Any]] = []
    tensors = [(n, t, False) for n, t in spec["inputs"].items()] + [(n, t, True) for n, t in spec["outputs"].items()]
    used = [d for _, t, _ in tensors for d in t.get("shape") or []]
    for axis, v in axes.items():
        kind = v.get("type")
        if kind == "const":
            constants.append(f"const {name(axis).upper()}:usize = {int(v['value'])};")
            fixed[axis] = int(v["value"])
        elif kind == "expr" and axis in used:  # an axis no shape names checks nothing
            fixed[axis] = axis_expression(str(v["expression"]), f"axes.{axis}")
        if kind in {"var", "expr"} and axis in used:
            params.append(f"{name(axis)}:usize")
            extents[name(axis)] = axis
    lengths: dict[tuple, str] = {}
    views = []
    for tensor, t, output in tensors:
        shape = t.get("shape")
        cairn = element(t["dtype"], tensor) if shape is not None else None
        entry = {"name": tensor, "parameter": name(tensor), "dtype": t["dtype"]}
        if shape is None:
            kind = SPELLED.get(t["dtype"])
            if kind not in {"f32", "f64", "bool", "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}:
                raise refused(f"the scalar {tensor} has dtype {t['dtype']}; a Python number arrives as a float, an "
                              "int or a bool.")  # fmt: skip
            views.append(f"{name(tensor)}:{kind}")
        else:
            entry["shape"] = list(shape)
            if len(shape) == 0:
                extent = "1"
            elif len(shape) == 1:
                extent = name(shape[0]).upper() if axes[shape[0]].get("type") == "const" else name(shape[0])
            elif all(axes[d].get("type") == "const" for d in shape):
                extent = f"{name(tensor).upper()}_LEN"
                constants.append(f"const {extent}:usize = {' * '.join(name(d).upper() for d in shape)};")
            else:
                if tuple(shape) not in lengths:
                    lengths[tuple(shape)] = f"{name(tensor)}_len"
                    params.append(f"{lengths[tuple(shape)]}:usize")
                    extents[lengths[tuple(shape)]] = " * ".join(shape)
                extent = lengths[tuple(shape)]
            views.append(f"{name(tensor)}:{'rw' if output else 'ro'}<{cairn}>[{extent}]@device")
        if output:
            entry["output"] = True
        arguments.append(entry)
    notes_tolerance, notes, source = tolerance(workloads)
    source_text = source_file(spec, function, constants, params + views)
    manifest = (f'[project]\nname = "{destination.name}"\nsources = ["src/{function}.cairn"]\n\n'
                f'[build]\nkind = "library"\ndevice_target = "{target}"\n')  # fmt: skip
    from ..compiler.cairnc import compile_source

    compile_source(source_text)  # the skeleton is accepted before anything is written, or the refusal says why
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "src").mkdir()
    (destination / "problem").mkdir()
    (destination / "cairn.toml").write_text(manifest, encoding="utf-8")
    (destination / "src" / f"{function}.cairn").write_text(source_text, encoding="utf-8")
    shutil.copyfile(definition, destination / "problem" / "definition.json")
    if definition.with_name("workload.jsonl").is_file():
        shutil.copyfile(definition.with_name("workload.jsonl"), destination / "problem" / "workload.jsonl")
    (destination / "harness.toml").write_text(mapping_file(spec, fixed, extents, arguments, notes), encoding="utf-8")
    policy = {"tolerance": notes_tolerance}
    (destination / "policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    (destination / ".gitignore").write_text("build/\n")
    guide(destination)
    return {"status": "created", "project": str(destination.resolve()), "definition": spec["name"],
            "function": function, "device_target": target, "tolerance": notes_tolerance, "tolerance_source": source,
            "not_expressed": notes, "constraints_not_checked": list(spec.get("constraints") or []),
            "network_access": False}  # fmt: skip


def source_file(spec: dict[str, Any], function: str, constants: list[str], params: list[str]) -> str:
    """The reference's signature, an empty body for the agent to write, and the PyTorch reference as a comment."""
    reference = [f"// {line}".rstrip() for line in str(spec.get("reference", "")).splitlines()]
    about = str(spec.get("description") or spec["name"]).replace("\n", " ")
    return "\n".join([
        f"// {spec['name']}: {about}",
        "// The benchmark's PyTorch reference, which this function computes in CAIRN:",
        *reference,
        *constants,
        "",
        "// The reference. Write its body; implementations that `implements` it are validated against it.",
        f"pub fn {function}({', '.join(params)}) {{",
        "}",
        "",
    ])  # fmt: skip


def mapping_file(spec: dict[str, Any], fixed: dict[str, Any], extents: dict[str, str], arguments: list[dict],
                 notes: list[str]) -> str:  # fmt: skip
    """harness.toml for the definition, in the order the evaluator passes its arguments: inputs, then outputs."""
    lines = [f"# How SOL-ExecBench's {spec['name']} calls the CAIRN function, for cairn export --harness.",
             "# policy.json holds the tolerance validation uses; the benchmark also checks what it cannot say:",
             *(f"#   {note}" for note in notes), "", "[benchmark]", f"definition = {json.dumps(spec['name'])}",
             'problem = "problem"', ""]  # fmt: skip
    if fixed:
        lines += ["[axes]", *(f"{a} = {json.dumps(v)}" for a, v in fixed.items()), ""]
    if extents:
        lines += ["[extents]", *(f"{p} = {json.dumps(e)}" for p, e in extents.items()), ""]
    for entry in arguments:
        lines += ["[[argument]]", *(f"{k} = {json.dumps(v)}" for k, v in entry.items()), ""]
    return "\n".join(lines)
