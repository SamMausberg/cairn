"""What `cairn export --harness` writes around an export: the C++ binding that takes torch tensors and calls the CAIRN
library's checked entry, and the file each benchmark reads, SOL-ExecBench's `solution.json`, GPU MODE's
`submission.py` or KernelBench's `ModelNew`.

The binding refuses a call before any pointer crosses: a tensor's dtype, device, contiguity, dimensions and element
count against the mapping and the CAIRN signature, a written tensor that overlaps another, a number that does not fit
its parameter. Each refusal is a Python exception naming the argument. Device work runs on the caller's current torch
stream. The Python files embed the export's sources verbatim, one line per line, so a reader and a static checker
see the C++ as it is, and hash each before building it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..compiler.tree import CPP, Type, is_view
from ..version import VERSION
from .harness_mapping import DTYPES, Mapping, Tensor, names, shown

FLOATS = {"f32", "f64"}
SCALAR_IN = {"f32": "double", "f64": "double", "bool": "bool"}  # what pybind11 hands over; every integer is int64_t
OPS = {"+": "add", "-": "sub", "*": "mul", "/": "quotient"}
PLACE_CHECK = {
    "device": ("{t}.is_cuda()", "device memory"),
    "host": ("{t}.device().is_cpu()", "host memory"),
    "pinned": ("{t}.device().is_cpu() && {t}.is_pinned()", "pinned host memory"),
}

HELPERS = """\
namespace cairn_harness {

// Checked size_t arithmetic for the extents: a step that overflows, goes below zero or divides by zero refuses the
// call, so an extent never wraps.
inline std::size_t add(std::size_t a, std::size_t b, const char* what) {
  std::size_t r = 0;
  TORCH_CHECK(!__builtin_add_overflow(a, b, &r), what, " overflows size_t");
  return r;
}
inline std::size_t sub(std::size_t a, std::size_t b, const char* what) {
  TORCH_CHECK(a >= b, what, " is below zero");
  return a - b;
}
inline std::size_t mul(std::size_t a, std::size_t b, const char* what) {
  std::size_t r = 0;
  TORCH_CHECK(!__builtin_mul_overflow(a, b, &r), what, " overflows size_t");
  return r;
}
inline std::size_t quotient(std::size_t a, std::size_t b, const char* what) {
  TORCH_CHECK(b != 0, what, " divides by zero");
  return a / b;
}
inline std::size_t size(int64_t v) {
  TORCH_CHECK(v >= 0, "a size below zero");
  return static_cast<std::size_t>(v);
}
inline int64_t dimension(std::size_t v, const char* what) {
  TORCH_CHECK(v <= static_cast<std::size_t>(std::numeric_limits<int64_t>::max()), what, " is past int64");
  return static_cast<int64_t>(v);
}

// Whether the bytes of two tensors overlap.
inline bool overlap(const torch::Tensor& a, const torch::Tensor& b) {
  if (a.numel() == 0 || b.numel() == 0) return false;
  const auto x = reinterpret_cast<std::uintptr_t>(a.data_ptr());
  const auto y = reinterpret_cast<std::uintptr_t>(b.data_ptr());
  return x < y + b.nbytes() && y < x + a.nbytes();
}

// A Python integer as the CAIRN integer parameter it feeds, or a refusal when it does not fit.
template <class T> T integer(int64_t v, const char* what) {
  if constexpr (std::is_signed_v<T>) {
    TORCH_CHECK(v >= static_cast<int64_t>(std::numeric_limits<T>::min()) &&
                v <= static_cast<int64_t>(std::numeric_limits<T>::max()), what, " = ", v, " does not fit");
  } else {
    TORCH_CHECK(v >= 0 && static_cast<std::uint64_t>(v) <= std::numeric_limits<T>::max(), what, " = ", v,
                " does not fit");
  }
  return static_cast<T>(v);
}

}  // namespace cairn_harness
"""


@dataclass(frozen=True)
class Library:
    """The CAIRN library a binding calls: its header, its C entry, and how device work reaches the caller's stream."""

    name: str  # the library's name, which names its header
    entry: str  # cf_NAME, the checked entry
    stream: str | None  # cairn_NAME_device_stream, for a library that runs device work
    no_wait: str | None  # cq_NAME, the entry that enqueues on a stream and returns without waiting, when declared
    cuda: bool
    ctypes: dict[str, str]  # each parameter's C type, as the header declares it, of its value or its element
    why_waits: str = ""  # why the header gives the function no enqueued entry (E-ENQUEUE), when it says

    @property
    def header(self) -> str:
        return self.name + ".h"

    @property
    def no_wait_name(self) -> str:
        return self.entry.replace("cf_", "cq_", 1)


def c_type(t: Type, library: Library, parameter: str, single: bool) -> str:
    """The C++ type of a parameter's value or element: the header's, or, in one translation unit with the program,
    the program's own, which differ only for a storage float."""
    return CPP[t.name] if single else library.ctypes[parameter]


def expression(tree: tuple, value: dict[str, str]) -> str:
    if tree[0] == "int":
        return f"std::size_t{{{tree[1]}}}"
    if tree[0] == "axis":
        return value[tree[1]]
    said = json.dumps(shown(tree))
    return f"cairn_harness::{OPS[tree[0]]}({expression(tree[1], value)}, {expression(tree[2], value)}, {said})"


def checks(t: Tensor) -> list[str]:
    """What the binding asks of a tensor argument before it reads any of it: dtype, place, layout and rank."""
    check, where = PLACE_CHECK[t.type.place]
    cairn, rank = f"{t.parameter}:{t.type.display()}", len(t.shape or ())
    shape = ", ".join(map(str, t.shape or ()))
    return [
        f'  TORCH_CHECK(t_{t.name}.scalar_type() == at::{DTYPES[t.dtype][1]}, "{t.name} has dtype ", '
        f't_{t.name}.scalar_type(), "; CAIRN\'s {cairn} takes {DTYPES[t.dtype][0]}");',
        f'  TORCH_CHECK({check.format(t="t_" + t.name)}, "{t.name} is on ", t_{t.name}.device(), '
        f'"; CAIRN\'s {cairn} takes {where}");',
        f"  TORCH_CHECK(t_{t.name}.is_contiguous(), \"{t.name} is not contiguous; CAIRN's {cairn} reads one run of "
        'elements");',
        f'  TORCH_CHECK(t_{t.name}.dim() == {rank}, "{t.name} has ", t_{t.name}.dim(), " dimensions; the mapping '
        f'gives it {rank}: [{shape}]");',
    ]


def scalar(t: Tensor, library: Library, single: bool) -> str:
    """A Python number as the parameter it feeds: a float rounded once to f32, an integer refused where it does not
    fit."""
    cxx = c_type(t.type, library, t.parameter, single)
    if t.type.name in FLOATS | {"bool"}:
        taken = f"static_cast<float>(a_{t.name})" if t.type.name == "f32" else f"a_{t.name}"
        return f"  const {cxx} s_{t.name} = {taken};"
    return f'  const {cxx} s_{t.name} = cairn_harness::integer<{cxx}>(a_{t.name}, "{t.name}");'


def axes(mapping: Mapping) -> list[str]:
    """Each axis, read from the first tensor argument whose shape names it or computed from [axes], then every other
    dimension checked against it."""
    read: dict[str, tuple[str, int]] = {}
    for t in mapping.arguments:
        for k, d in enumerate(t.shape or ()):
            if isinstance(d, str) and d not in mapping.axes and d not in read:
                read[d] = (t.name, k)
    lines = []
    for axis in mapping.axis_order():
        if axis in read:
            lines.append(
                f"  const std::size_t x_{axis} = cairn_harness::size(t_{read[axis][0]}.size({read[axis][1]}));"
            )
        else:
            tree = mapping.axes[axis]
            lines.append(f"  const std::size_t x_{axis} = {expression(tree, {a: 'x_' + a for a in names(tree)})};")
    for t in mapping.arguments:
        for k, d in enumerate(t.shape or ()):
            if isinstance(d, str) and read.get(d) == (t.name, k):
                continue
            want, said = (f"x_{d}", f"the axis {d}") if isinstance(d, str) else (f"std::size_t{{{d}}}", "the mapping")
            lines.append(f'  TORCH_CHECK(cairn_harness::size(t_{t.name}.size({k})) == {want}, "{t.name}\'s dimension '
                         f'{k} is ", t_{t.name}.size({k}), "; {said} is ", {want});')  # fmt: skip
    return lines


def entry(mapping: Mapping, library: Library, value: dict[str, str], single: bool) -> list[str]:
    """The call itself, on the caller's current torch stream when the library runs device work."""
    fed = {t.parameter: t for t in mapping.tensors}
    arguments = []
    for parameter, declared in mapping.function.params:
        if parameter in value:
            arguments.append(value[parameter])
        elif fed[parameter].scalar:
            arguments.append(f"s_{fed[parameter].name}")
        else:
            const = "const " if declared.mode == "ro" else ""
            pointer = f"{const}{c_type(declared, library, parameter, single)}*"
            arguments.append(f"static_cast<{pointer}>(t_{fed[parameter].name}.data_ptr())")
    call, stream = ", ".join(arguments), "static_cast<void*>(at::cuda::getCurrentCUDAStream().stream())"
    if not library.cuda:
        return [f"  {library.entry}({call});"]
    if library.no_wait:
        return [f"  // {library.no_wait} enqueues on the current torch stream and returns without waiting.",
                f"  {library.no_wait}({stream}, {call});"]  # fmt: skip
    missing = library.no_wait_name
    return [f"  // The library declares no {missing}, so {library.entry} runs on the current torch stream, after what",
            "  // torch queued there, and returns once its own work there has finished.",
            f"  {library.stream}({stream});", f"  {library.entry}({call});", f"  {library.stream}(nullptr);"]  # fmt: skip


def binding(mapping: Mapping, library: Library, fmt: str, single: bool = False) -> str:
    """The C++ binding's source: `run(arguments...)`, returning the results it allocates. `single` writes it for one
    translation unit that holds the program first, where the program declares the entry and no header is read."""
    f = mapping.function
    devices = [t for t in mapping.tensors if not t.scalar and t.type.place == "device"]
    cuda = library.cuda or bool(devices)
    lines = [f"// Generated by {VERSION}: the PyTorch binding of the CAIRN function {f.name} for {fmt}. Do not edit;",
             "// edit harness.toml or the CAIRN source, and export again.", "#include <torch/extension.h>",
             *(["#include <ATen/cuda/CUDAContext.h>", "#include <c10/cuda/CUDAGuard.h>"] if cuda else []),
             "#include <cstddef>", "#include <cstdint>", "#include <limits>", "#include <type_traits>",
             *([] if single else [f'#include "{library.header}"']), "", HELPERS]  # fmt: skip
    body = [line for t in mapping.arguments if not t.scalar for line in checks(t)]
    body += [scalar(t, library, single) for t in mapping.arguments if t.scalar]
    value = {t.parameter: f"s_{t.name}" for t in mapping.arguments if t.scalar and t.type.name == "usize"}
    body += axes(mapping)
    for parameter, tree in mapping.extents.items():
        value[parameter] = f"p_{parameter}"
        body.append(f"  const std::size_t p_{parameter} = {expression(tree, {a: 'x_' + a for a in names(tree)})};")
    if cuda:
        given = [t for t in devices if t.role != "result"]
        where = f"t_{given[0].name}.device()" if given else "at::Device(at::kCUDA, c10::cuda::current_device())"
        body.append(f"  const at::Device device = {where};")
        body += [f'  TORCH_CHECK(t_{t.name}.device() == device, "{t.name} is on ", t_{t.name}.device(), "; the other '
                 'device tensors are on ", device);' for t in given[1:]]  # fmt: skip
        body.append("  const c10::cuda::CUDAGuard guard(device);")
    for t in mapping.results:
        dims = ", ".join(
            f'cairn_harness::dimension(x_{d}, "{d}")' if isinstance(d, str) else str(d) for d in t.shape or ()
        )
        on = "device" if t.type.place == "device" else "at::kCPU"
        pinned = ".pinned_memory(true)" if t.type.place == "pinned" else ""
        body.append(f"  torch::Tensor t_{t.name} = torch::empty({{{dims}}}, torch::TensorOptions()"
                    f".dtype(at::{DTYPES[t.dtype][1]}).device({on}){pinned});")  # fmt: skip
    for t in (t for t in mapping.tensors if not t.scalar):
        extent = t.type.extent
        if not is_view(t.type):
            body.append(f'  TORCH_CHECK(t_{t.name}.numel() == 1, "{t.name} holds ", t_{t.name}.numel(), " elements; '
                        f'CAIRN\'s {t.parameter} borrows one");')  # fmt: skip
            continue
        want = f"std::size_t{{{extent}}}" if extent.isdigit() else value[extent]
        body.append(f'  TORCH_CHECK(cairn_harness::size(t_{t.name}.numel()) == {want}, "{t.name} holds ", '
                    f't_{t.name}.numel(), " elements; CAIRN\'s {t.parameter} has {extent} = ", {want});')  # fmt: skip
    for w in (t for t in mapping.arguments if t.role == "output"):
        body += [f'  TORCH_CHECK(!cairn_harness::overlap(t_{w.name}, t_{o.name}), "{w.name} overlaps {o.name}; CAIRN '
                 f'writes {w.parameter} and no other argument may share its memory");'
                 for o in mapping.arguments if not o.scalar and o is not w]  # fmt: skip
    body += entry(mapping, library, value, single)
    results = mapping.results
    if len(results) == 1:
        body.append(f"  return t_{results[0].name};")
    elif results:
        body.append(f"  return {{{', '.join('t_' + t.name for t in results)}}};")
    returns = {0: "void", 1: "torch::Tensor"}.get(
        len(results), f"std::tuple<{', '.join(['torch::Tensor'] * len(results))}>"
    )
    params = [f"{SCALAR_IN.get(t.type.name, 'int64_t')} a_{t.name}" if t.scalar else f"torch::Tensor t_{t.name}"
              for t in mapping.arguments]  # fmt: skip
    named = "".join(f', pybind11::arg("{t.name}")' for t in mapping.arguments)
    lines += [f"{returns} run({', '.join(params)}) {{", *body, "}", "", "PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {",
              f'  m.def("run", &run, "The CAIRN function {f.name}, through its checked entry"{named});', "}", ""]  # fmt: skip
    return "\n".join(lines)


def solution(mapping: Mapping, sources: dict[str, str], identity: str, flags: dict[str, list[str]],
             hardware: list[str], symbol: str) -> str:  # fmt: skip
    """SOL-ExecBench's solution.json: the export's files and the binding as sources, main.cpp::run the entry."""
    bench = mapping.benchmark
    record = {
        "name": f"cairn_{symbol}_{identity[:8]}",
        "definition": bench["definition"],
        "author": bench.get("author", "cairn"),
        "description": f"The CAIRN function {mapping.function.name}, exported by {VERSION} (export {identity[:16]}); "
        "every source but main.cpp is the export's, byte for byte.",
        "spec": {
            "languages": ["cuda_cpp"],
            "target_hardware": hardware,
            "entry_point": "main.cpp::run",
            "dependencies": [],
            "destination_passing_style": True,
            "binding": "torch",
            "compile_options": flags,
        },
        "sources": [{"path": path, "content": text} for path, text in sources.items()],
    }
    return json.dumps(record, indent=2) + "\n"


def literal(text: str) -> str:
    """A Python literal of `text` that keeps it one line per line: a raw triple-quoted string when one can hold it."""
    for quote in ("'''", '"""'):
        if quote not in text and not text.endswith("\\") and "\r" not in text:
            return f"r{quote}{text}{quote}"
    return repr(text)


PYTHON = '''\
import hashlib
import os
import tempfile

{imports}from torch.utils.cpp_extension import load_inline

_PROGRAM = {program}

_BINDING = {binding}

_HEADERS = {{
{headers}
}}

_SHA256 = {digests}


def _build():
    """Hash every embedded source against the export's record, then build the program and its binding."""
    embedded = {{**_HEADERS, "{program_name}": _PROGRAM, "binding.cpp": _BINDING}}
    for name, text in embedded.items():
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != _SHA256[name]:
            raise RuntimeError(f"{{name}} is not the source this file was generated with; export it again")
    include = tempfile.mkdtemp(prefix="cairn-harness-")
    for name, text in _HEADERS.items():
        with open(os.path.join(include, name), "w", encoding="utf-8") as out:
            out.write(text)
    return load_inline(
        name="{module}",
        {sources}
        extra_cflags={cflags},
        extra_cuda_cflags={cuda_cflags},
        extra_ldflags={ld_flags},
        extra_include_paths=[include],
    )


_cairn = _build()
'''


def python(mapping: Mapping, files: dict[str, str], binding_text: str, program: str, flags: dict[str, list[str]],
           module: str, cuda: bool, imports: str) -> str:  # fmt: skip
    """The part every Python format shares: the embedded sources, their hashes and the load_inline build."""
    import hashlib

    headers = {n: t for n, t in files.items() if n != program}
    digests = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in
               {**headers, program: files[program], "binding.cpp": binding_text}.items()}  # fmt: skip
    if cuda:
        sources = "cpp_sources=[_BINDING],\n        cuda_sources=[_PROGRAM],"
    else:  # one translation unit: the program declares its entry, and the binding after it calls it
        sources = "cpp_sources=[_PROGRAM, _BINDING],"
    return PYTHON.format(
        imports=imports,
        program=literal(files[program]),
        binding=literal(binding_text),
        headers="\n".join(f"    {json.dumps(n)}: {literal(t)}," for n, t in headers.items()),
        digests=json.dumps(digests, indent=4),
        program_name=program,
        module=module,
        sources=sources,
        cflags=json.dumps(flags["cflags"]),
        cuda_cflags=json.dumps(flags["cuda_cflags"]),
        ld_flags=json.dumps(flags["ld_flags"]),
    )


def returned(mapping: Mapping) -> list[Tensor]:
    return [*(t for t in mapping.arguments if t.role == "output"), *mapping.results]


def gpumode(mapping: Mapping, shared: str, doc: str) -> str:
    """GPU MODE's submission.py: `custom_kernel(data)` unpacks the task's input tuple in the mapping's order."""
    bench, out = mapping.benchmark, returned(mapping)
    directives = [f"#!POPCORN {key} {bench[key]}" for key in ("leaderboard", "gpu") if key in bench]
    unpacked = ", ".join(t.name for t in mapping.arguments)
    call = f"_cairn.run({unpacked})"
    made = [t.name for t in mapping.results]
    lines = [*directives, f'"""{doc}"""', "", shared, "", "def custom_kernel(data):",
             f'    """The task\'s input tuple, ({unpacked}), through the CAIRN function {mapping.function.name}."""',
             f"    {unpacked}{',' if len(mapping.arguments) == 1 else ''} = data"]  # fmt: skip
    if made:
        lines.append(f"    {', '.join(made)} = {call}")
    else:
        lines.append(f"    {call}")
    lines.append(f"    return {', '.join(t.name for t in out)}" if len(out) > 1 else f"    return {out[0].name}")
    return "\n".join(lines) + "\n"


def kernelbench(mapping: Mapping, shared: str, doc: str) -> str:
    """KernelBench's ModelNew: `forward` takes the inputs get_inputs() makes and returns what the adapter allocates."""
    unpacked = ", ".join(t.name for t in mapping.arguments)
    lines = [f'"""{doc}"""', "", shared, "", "class ModelNew(nn.Module):",
             f'    """The CAIRN function {mapping.function.name} as the problem\'s Model."""', "",
             "    def __init__(self):", "        super().__init__()", "",
             f"    def forward(self, {unpacked}):", f"        return _cairn.run({unpacked})"]  # fmt: skip
    return "\n".join(lines) + "\n"


def signature(mapping: Mapping) -> str:
    f = mapping.function
    params = ", ".join(f"{n}:{t.display()}" for n, t in f.params)
    return f"{f.name}({params})" + ("" if f.ret.name == "void" else f" -> {f.ret.display()}")
