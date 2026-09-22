"""A loop's cost read from the compiler's own output, without running it.

`loops` compiles a program to assembly with the build's flags, finds each function's innermost loops, and asks
llvm-mca how many cycles one pass of each takes on this machine's core model. clang's optimization record says how
many elements a pass covers: its vectorization width times its interleave count, or its unroll factor. Cycles over
elements, at the clock the calibration measured, is a loop's cost per element. Absent llvm-mca the answer is None
and the model keeps its counted price. Device kernels are read the same way from ptxas and cuobjdump in `device`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..compiler.cairnc import RUNTIME_FILES, compile_source
from ..projects.toolchain import find, flags

MCA_PATHS = ("llvm-mca", "/opt/llvm-21.1.8/bin/llvm-mca")
LABEL = re.compile(r"^(\.LBB\d+_\d+):")
JUMP = re.compile(r"^\s+j\w+\s+(\.LBB\d+_\d+)")
FUNCTION = re.compile(r"^([A-Za-z_][\w.$]*):")
END = re.compile(r"^\.Lfunc_end\d+:")
LOC = re.compile(r"^\s+\.loc\s+(\d+)\s+(\d+)")
VECTOR = re.compile(r"%zmm|%ymm|%xmm")
FILE = re.compile(r'^\s+\.file\s+(\d+)\s+(?:"[^"]*"\s+)?"([^"]+)"')
WIDE = {"%zmm": 64, "%ymm": 32, "%xmm": 16}
SUFFIX = {"q": 8, "l": 4, "w": 2, "b": 1}
REMARK_PASSES = "loop-vectorize|loop-unroll"


def mca() -> str | None:
    for candidate in MCA_PATHS:
        if found := shutil.which(candidate) or (Path(candidate).is_file() and candidate):
            return str(found)
    return None


def compiled(source: str, cxx: str, arch: str | None, directory: Path) -> tuple[str, str]:
    """(assembly, optimization record) of `source` built with the build's flags, lines placed in `program.cairn`."""
    cpp, _ = compile_source(source, "program.cairn")
    (directory / "program.cpp").write_text(cpp, encoding="utf-8")
    for name, text in RUNTIME_FILES.items():
        (directory / name).write_text(text, encoding="utf-8")
    base = [f for f in flags(arch, "library") if f not in {"-shared", "-fPIC"}]
    record = directory / "program.yaml"
    command = [find(cxx), *base, "-S", "-gline-tables-only", "-fno-asynchronous-unwind-tables",
               "-fsave-optimization-record", f"-foptimization-record-passes={REMARK_PASSES}",
               f"-foptimization-record-file={record}", str(directory / "program.cpp"), "-o", str(directory / "p.s")]  # fmt: skip
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
    return (directory / "p.s").read_text(encoding="utf-8"), record.read_text(
        encoding="utf-8"
    ) if record.exists() else ""


def functions(asm: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in asm.splitlines():
        if END.match(line):
            current = None
        elif (m := FUNCTION.match(line)) and not line.startswith(".L"):
            current = m.group(1)
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def innermost(body: list[str]) -> list[tuple[int, int]]:
    """(first, last) line of each loop no other loop sits inside: a backward jump to a label above it."""
    labels = {m.group(1): i for i, line in enumerate(body) if (m := LABEL.match(line))}
    spans = [(labels[m.group(1)], i) for i, line in enumerate(body)
             if (m := JUMP.match(line)) and labels.get(m.group(1), i + 1) <= i]  # fmt: skip
    return [s for s in spans if not any(o != s and s[0] <= o[0] and o[1] <= s[1] for o in spans)]


def instructions(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("\t") and not line.lstrip().startswith((".", "#"))]


def moved(line: str) -> tuple[int, int]:
    """(bytes loaded, bytes stored) by one AT&T instruction: memory is the operand written `disp(base, ...)`."""
    mnemonic, _, operands = line.split("#", 1)[0].strip().replace("\t", " ").partition(" ")
    parts = [o.strip() for o in re.split(r",(?![^(]*\))", operands.strip())] if operands.strip() else []
    memory = [i for i, o in enumerate(parts) if "(" in o and not o.startswith("%")]
    if not memory or mnemonic.startswith(("lea", "prefetch", "nop")):
        return 0, 0
    wide = [w for r, w in WIDE.items() if r in operands]
    size = max(wide) if wide else SUFFIX.get(mnemonic[-1], 8) if not mnemonic.startswith("v") else 8
    if mnemonic.startswith(("vmovss", "movss", "vbroadcastss")):
        size = 4 if not wide or mnemonic.endswith("ss") else size
    stored = memory[-1] == len(parts) - 1 and len(parts) > 1 and not mnemonic.startswith(("cmp", "test"))
    return (0, size) if stored else (size, 0)


def cycles(code: list[str], cpu: str = "native", iterations: int = 200) -> float | None:
    tool = mca()
    if not tool or not code:
        return None
    done = subprocess.run([tool, f"-mcpu={cpu}", f"-iterations={iterations}", "-timeline=false", "-resource-pressure=false",
                           "-instruction-info=false"], input="\n".join(code) + "\n", capture_output=True, text=True,
                          timeout=60)  # fmt: skip
    total = re.search(r"Total Cycles:\s+(\d+)", done.stdout)
    count = re.search(r"Iterations:\s+(\d+)", done.stdout)
    return int(total.group(1)) / int(count.group(1)) if done.returncode == 0 and total and count else None


def widths(record: str) -> dict[int, int]:
    """program.cairn line -> elements one pass of that line's loop covers, from the vectorizer and unroller."""
    out: dict[int, int] = {}
    for doc in re.split(r"^--- !", record, flags=re.M)[1:]:
        if not doc.startswith("Passed"):
            continue
        where = re.search(r"File:\s+'?program\.cairn'?,\s*Line:\s+(\d+)", doc)
        text = " ".join(re.findall(r"^\s+- \w+:\s+'?([^'\n]*)'?$", doc, re.M))
        width = re.search(r"vectorization width:\s*(\d+).*?interleaved count:\s*(\d+)", text)
        unroll = re.search(r"unrolled loop by a factor of\s*(\d+)", text)
        if where:
            line, factor = int(where.group(1)), 1
            if width:
                factor = int(width.group(1)) * int(width.group(2))
            elif unroll:
                factor = int(unroll.group(1))
            out[line] = max(out.get(line, 1), factor)
    return out


def owner(symbol: str, names: set[str]) -> str | None:
    for size, rest in re.findall(r"(\d+)(cf_\w+)", symbol) or [("", symbol)]:
        found = (rest[: int(size)] if size else rest).removeprefix("cf_")
        if found in names:
            return found
    return None


def loops(source: str, cxx: str = "clang++", arch: str | None = None, cpu: str = "native") -> dict[str, Any]:
    """Per CAIRN function, each innermost loop: its line, cycles per pass, elements per pass, whether it is vector."""
    if not mca() or Path(cxx).name.split("-")[0] != "clang++":
        return {"status": "not-run", "reason": "llvm-mca and clang++ are both needed to read a loop's cycles."}
    from ..compiler.cairnc import compile_program
    from ..compiler.codegen import mangle

    p, _, _ = compile_program(source)
    names = {mangle(f.name): f.name for f in p.functions}
    with tempfile.TemporaryDirectory(prefix="cairn-mca-") as scratch:
        asm, _ = compiled(source, cxx, arch, Path(scratch))
    files = {m.group(1) for line in asm.splitlines() if (m := FILE.match(line)) and m.group(2) == "program.cairn"}
    found: dict[str, list[dict[str, Any]]] = {}
    for symbol, body in functions(asm).items():
        mangled = owner(symbol, set(names))
        if mangled is None:
            continue
        for first, last in innermost(body):
            span = body[first : last + 1]
            lines = Counter(int(m.group(2)) for line in span if (m := LOC.match(line)) and m.group(1) in files
                            and m.group(2) != "0")  # fmt: skip
            code = instructions(span)
            traffic = [moved(line) for line in code]
            mix = Counter(line.split()[0] for line in code)
            found.setdefault(names[mangled], []).append({
                "line": lines.most_common(1)[0][0] if lines else 0, "cycles": cycles(code, cpu),
                "vector": any(VECTOR.search(line) for line in code), "instructions": len(code),
                "loaded": sum(a for a, _ in traffic), "stored": sum(b for _, b in traffic), "mix": dict(mix)})  # fmt: skip
    return {"status": "read", "cpu": cpu, "loops": found}


def loop_cycles(source: str, name: str, marker: str, cxx: str = "clang++", arch: str | None = None) -> float | None:
    """Cycles per element of `name`'s longest innermost loop, where each element runs one `marker` instruction."""
    read = loops(source, cxx, arch)
    chosen = [x for x in read.get("loops", {}).get(name, []) if x["cycles"] and x["mix"].get(marker)]
    if not chosen:
        return None
    main = max(chosen, key=lambda x: x["instructions"])
    return round(main["cycles"] / main["mix"][marker], 3)
