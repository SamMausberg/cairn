#!/usr/bin/env python3
"""Write a synthetic CAIRN project of a chosen size, for measuring how the tools scale.

The project is `modules` modules spread over at most 64 source files (the manifest's limit). `big.core` holds a
record, a sum and a generic function that every module imports; module k also imports modules k - 1, k // 2 and
k // 3, so the import graph is a DAG with long and short edges. Each module declares a record, a sum and seven
functions: a loop, a fold, a host `parallel` region, a `match`, a record read, two tasks over disjoint parts, and
calls into what it imports. `main` in the root module calls the last few modules. Everything is deterministic.

    python3 bench/scale/generate.py results/scale/p10k --modules 170
"""

from __future__ import annotations

import argparse
from pathlib import Path

CORE = """module big.core;

pub struct Pair { a:u64; b:u64; }
pub enum Ev { Add(u64); Sub(u64); Stop; }

pub fn pick[T:copy](a:T, b:T, first:bool) -> T {
  if first { return a; }
  return b;
}

pub fn mix(p:Pair) -> u64 = add_wrap(p.a, p.b) ^ SALT;

pub const SALT:u64 = 17;
"""


def imports_of(k: int) -> list[int]:
    """The earlier modules module k imports: its predecessor, and two long edges back."""
    return sorted({j for j in (k - 1, k // 2, k // 3) if j >= 1 and j != k})


def module(k: int) -> str:
    deps = imports_of(k)
    lines = [f"module big.m{k};", "import big.core (Pair, Ev);", *(f"import big.m{j};" for j in deps), ""]
    lines += [
        f"pub struct Rec{k} {{ a:u64; b:u64; tag:u32; }}",
        f"pub enum Op{k} {{ Put(u64); Drop; }}",
        "",
        f"pub fn fill_{k}(n:usize, out:rw<u64>[n], start:u64) {{",
        "  for i in 0..n { out[i] = add_wrap(start, u64(i)); }",
        "}",
        "",
        f"pub fn fold_{k}(n:usize, xs:ro<u64>[n]) -> u64 {{",
        "  let mut acc:u64 = 0;",
        f"  for i in 0..n {{ acc = add_wrap(acc, xs[i] ^ {k}); }}",
        "  return acc;",
        "}",
        "",
        f"pub fn scale_{k}(n:usize, out:rw<u64>[n], xs:ro<u64>[n]) {{",
        f"  parallel i in n {{ out[i] = mul_wrap(xs[i], {k % 7 + 2}); }}",
        "}",
        "",
        f"pub fn step_{k}(e:Ev, v:u64) -> u64 {{",
        "  match e {",
        "    Add(x) => return add_wrap(v, x);",
        "    Sub(x) => return sub_wrap(v, x);",
        "    Stop => return v;",
        "  }",
        "}",
        "",
        f"pub fn apply_{k}(o:Op{k}, r:Rec{k}) -> u64 {{",
        "  match o {",
        "    Put(x) => return add_wrap(x, add_wrap(r.a, r.b) ^ u64(r.tag));",
        "    Drop => return r.a;",
        "  }",
        "}",
        "",
        f"pub fn run_{k}(n:usize, data:rw<u64>[n]) -> u64 {{",
        "  let mid = n / 2;",
        f"  let t = spawn fill_{k}(data[0..mid], {k});",
        f"  fill_{k}(data[mid..n], {k + 1});",
        "  wait(t);",
        f"  let mut s = fold_{k}(data);",
        *(f"  s = add_wrap(s, m{j}.fold_{j}(data));" for j in deps),
        "  let p = Pair(s, core.pick[u64](s, 3, s > 3));",
        f"  let r = Rec{k}(s, core.mix(p), u32(s & 255));",
        f"  return add_wrap(apply_{k}(Put(s), r), step_{k}(Add(s), {k}));",
        "}",
        "",
    ]
    return "\n".join(lines)


def main_module(modules: int) -> str:
    tops = list(range(max(1, modules - 3), modules + 1))
    lines = ["module big;", *(f"import big.m{k};" for k in tops), "", "pub fn main() -> i32 {"]
    lines += ["  let mut data = Buf[u64](4096);", "  let mut total:u64 = 0;"]
    for k in tops:  # A call that writes is bound on its own before its result is used (E-EFFECT-ORDER).
        lines += [f"  let r{k} = m{k}.run_{k}(data);", f"  total = add_wrap(total, r{k});"]
    lines += ['  println("total = ", total);', "  return 0;", "}", ""]
    return "\n".join(lines)


def write(root: Path, modules: int, files: int = 64, kind: str = "library") -> dict:
    """The project under `root`: cairn.toml, src/core.cairn, the modules in up to `files - 2` files, src/main.cairn.
    A library build compiles every module; an executable only what `main` reaches, here a few modules.
    Returns what was written: the module count, the file names, the line count, and which module is a leaf."""
    root.mkdir(parents=True, exist_ok=True)
    src = root / "src"
    src.mkdir(exist_ok=True)
    buckets = max(1, min(files - 2, modules))
    per = -(-modules // buckets)
    names = ["src/core.cairn"]
    (src / "core.cairn").write_text(CORE, encoding="utf-8")
    written = [CORE]
    for b in range(buckets):
        body = "\n".join(module(k) for k in range(b * per + 1, min(modules, (b + 1) * per) + 1))
        if not body:
            continue
        name = f"src/part{b:02d}.cairn"
        (root / name).write_text(body, encoding="utf-8")
        names.append(name)
        written.append(body)
    tail = main_module(modules)
    (src / "main.cairn").write_text(tail, encoding="utf-8")
    names.append("src/main.cairn")
    written.append(tail)
    manifest = "\n".join(
        ["[project]", 'name = "big"', "sources = [" + ", ".join(f'"{n}"' for n in names) + "]", "",
         "[build]", f'kind = "{kind}"', ""]
    )  # fmt: skip
    (root / "cairn.toml").write_text(manifest, encoding="utf-8")
    lines = sum(text.count("\n") + 1 for text in written)
    leaf = next(name for name in reversed(names[1:-1]))  # the last part holds the modules nothing else imports
    return {"modules": modules, "files": names, "lines": lines, "bytes": sum(len(t.encode()) for t in written),
            "leaf_file": leaf, "core_file": "src/core.cairn"}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="The directory to write the project into.")
    ap.add_argument("--modules", type=int, required=True, help="How many generated modules besides core and main.")
    ap.add_argument("--files", type=int, default=64, help="Source files at most, core and main included.")
    ap.add_argument("--kind", choices=["library", "exe"], default="library", help="What `cairn build` makes.")
    args = ap.parse_args()
    print(write(args.root, args.modules, args.files, args.kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
