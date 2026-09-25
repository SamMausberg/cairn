"""print, println, eprint, eprintln and format: what they write, what they cost, and what they refuse.

The integer and text expectations are Python's own formatting; the float ones are tests/oracles/printed.py, the
shortest round-trip decimal worked out with exact rationals. Every program runs natively under both compilers.
"""

import json
import math
import random
import shutil
import struct
import subprocess

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import ProjectError, load_project
from cairn.projects.toolchain import audit_effects
from cairn.verify.scalar.semantics import equivalent
from emitted import native, refused, watched
from oracles.printed import printed

COMPILERS = ["clang++", "g++"]

SCALARS = """
struct Line { head:u8; body:Vec[u8]; }

fn show(n:usize, bytes:ro<u8>[n], line:ro<Vec[u8]>) { print("[", bytes, "|", line, "]"); }

fn main() -> i32 {
  let a:i8 = -127 - 1;
  let b:i16 = 32767;
  let c:i32 = -2147483647 - 1;
  let d:i64 = -9223372036854775807 - 1;
  let e:u8 = 255;
  let f:u16 = 65535;
  let g:u32 = 4294967295;
  let h:u64 = 18446744073709551615;
  let k:usize = 0;
  println(a, " ", b, " ", c, " ", d);
  println(e, " ", f, " ", g, " ", h, " ", k);
  let small:u8 = 65;
  println(small, 'A', '\\n', true, false);
  let mut v = vec.new[u8]();
  v.push('h');
  v.push('i');
  let frame = "hello, world";
  let mut owned = Buf[u8](3);
  owned[0] = 'x';
  owned[1] = 'y';
  owned[2] = 'z';
  let mut inline = Array[u8, 2]();
  inline[0] = 'o';
  inline[1] = 'k';
  stack scratch:u8[2] = zeroed;
  scratch[0] = '<';
  scratch[1] = '>';
  let l = Line('#', v);
  println(frame[7..12], owned, inline, scratch, l.body, l.head);
  show(frame[0..5], l.body);
  println();
  eprint("err ", -1);
  eprintln(" end");
  print("no newline");
  return 0;
}
"""


def program(body: str) -> str:
    return "import std.vec (Vec);\n" + body


@pytest.mark.parametrize("cxx", COMPILERS)
def test_integers_bools_characters_and_text_print_as_python_writes_them(tmp_path, cxx):
    done = native(tmp_path, program(SCALARS), cxx)
    rows = [
        f"{-128} {32767} {-(2**31)} {-(2**63)}",
        f"{255} {65535} {2**32 - 1} {2**64 - 1} 0",
        "65A\ntruefalse",  # a u8 value prints its number; a character literal, its byte
        "worldxyzok<>hi35",
        "[hello|hi]",
    ]
    assert done.stdout == "\n".join(rows) + "\n" + "no newline"
    assert done.stderr == "err -1 end\n"


def patterns() -> tuple[list[int], list[int]]:
    rng = random.Random(11)
    whole = [
        struct.unpack("<Q", struct.pack("<d", float(rng.randint(1, 10 ** rng.randint(1, 24)))))[0] for _ in range(60)
    ]
    scaled = [struct.unpack("<Q", struct.pack("<d", rng.random() * 10 ** rng.randint(-40, 40)))[0] for _ in range(60)]
    edges = [0, 1 << 63, 0x7FF << 52, 0xFFF << 52, 0x7FF8 << 48, 0xFFF8 << 48, 1, (1 << 52) - 1, 1 << 52]
    edges += [0x7FEFFFFFFFFFFFFF, 0x3FB999999999999A, 0x40F86A0000000000, 0x4415AF1D78B58C40]  # 0.1, 1e5, 1e20
    double = edges + whole + scaled + [rng.getrandbits(64) for _ in range(300)]
    single = [0, 1 << 31, 0x7F800000, 0xFF800000, 0x7FC00000, 1, 0x007FFFFF, 0x00800000, 0x7F7FFFFF]
    return double, single + [rng.getrandbits(32) for _ in range(300)]


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_float_prints_in_the_shortest_form_that_reads_back(tmp_path, cxx):
    double, single = patterns()
    lines = [f"  println(from_bits[f64](0x{p:016x}));" for p in double]
    lines += [f"  println(from_bits[f32](0x{p:08x}));" for p in single]
    done = native(tmp_path, "fn main() -> i32 {\n" + "\n".join(lines) + "\n  return 0;\n}\n", cxx)
    want = [printed(struct.unpack("<d", struct.pack("<Q", p))[0]) for p in double]
    want += [printed(struct.unpack("<f", struct.pack("<I", p))[0], 32) for p in single]
    assert done.stdout.splitlines() == want
    assert want[:13] == ["0", "-0", "inf", "-inf", "nan", "-nan", "5e-324", "2.225073858507201e-308",
                         "2.2250738585072014e-308", "1.7976931348623157e+308", "0.1", "100000",
                         "100000000000000000000"]  # fmt: skip


def test_the_oracle_lays_out_a_float_as_javascript_does():
    """A second, unrelated implementation of the layout rule: node's own Number.prototype.toString."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the exact-rational oracle alone holds the native output")
    rng = random.Random(11)
    xs = [struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0] for _ in range(4000)]
    xs = [x for x in xs if math.isfinite(x) and x != 0] + [
        1.0,
        1e5,
        1e20,
        1e21,
        1e-6,
        1e-7,
        0.1,
        123456789012345680000.0,
    ]
    script = "const xs = JSON.parse(require('fs').readFileSync(0, 'utf8')); console.log(JSON.stringify(xs.map(String)))"
    said = subprocess.run([node, "-e", script], input=json.dumps(xs), capture_output=True, text=True, check=True)
    assert [printed(x) for x in xs] == json.loads(said.stdout)


LONG = """
fn main() -> i32 {
  let mut v = vec.new[u8]();
  for i in 0..10000 { v.push(u8(i % 26) + 'a'); }
  let n = v.len;
  println(v.data[0..4095], 7);
  println(v.data[0..4096]);
  println(v.data[0..4097], 123456789, v.data[0..3]);
  let least:i64 = -9223372036854775807 - 1;
  println(18446744073709551615, v.data[0..4090], least, 1.5);
  println(v.data[0..n], true);
  return 0;
}
"""


@pytest.mark.parametrize("cxx", COMPILERS)
def test_text_longer_than_the_buffer_and_pieces_across_its_edge_come_out_whole(tmp_path, cxx):
    text = "".join(chr(ord("a") + i % 26) for i in range(10000))
    want = [text[:4095] + "7", text[:4096], text[:4097] + "123456789" + text[:3]]
    want += [str(2**64 - 1) + text[:4090] + str(-(2**63)) + "1.5", text + "true"]
    assert native(tmp_path, program(LONG), cxx).stdout == "\n".join(want) + "\n"


FORMAT = """
fn append(out:rw<Vec[u8]>, n:u64) { format(out, n, ","); }

fn main() -> i32 {
  let mut line = vec.new[u8]();
  let mut other = vec.with_capacity[u8](64);
  format(other, "id=", 7);
  for i in 0..300 { append(line, u64(i)); }
  format(line, other, ' ', -2, ' ', 0.25, ' ', false, "|");
  println(line);
  if len(line.data) < line.len { return 1; }
  println(other.len, " ", len(other.data));
  return 0;
}
"""


@pytest.mark.parametrize("cxx", COMPILERS)
def test_format_appends_to_a_vec_growing_it_as_push_does(tmp_path, cxx):
    done = native(tmp_path, program(FORMAT), cxx)
    head = "".join(f"{i}," for i in range(300)) + "id=7 -2 0.25 false|"
    assert done.stdout == f"{head}\n4 64\n"


@pytest.mark.parametrize("sanitizer", ["address", "undefined"])
def test_print_and_format_are_clean_under_the_sanitizers(tmp_path, sanitizer):
    cpp = compile_source(program(FORMAT + "\n" + LONG.replace("fn main", "fn second")))[0]
    done = watched(tmp_path, cpp, "clang++", sanitizer)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.startswith("0,1,2,") and "runtime error" not in done.stderr


TRAPS = """
fn first() -> u64 { assert(false, "first"); return 1; }
fn second() -> u64 { assert(false, "second"); return 2; }

fn main() -> i32 {
  let xs = Buf[u64](3);
  let k:usize = 3;
  println("before ", xs[k]);
  return 0;
}

fn ordered() -> i32 {
  println(first(), second());
  return 0;
}
"""


def built(tmp_path, source: str, cxx: str) -> str:
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "program.cairn"
    path.write_text(source)
    record = build(load_project(path), kind="exe", cxx=cxx, timeout=180)
    assert record["status"] == "native-built", record.get("stderr", "")[:2000]
    return record["artifact"]


@pytest.mark.parametrize("cxx", COMPILERS)
def test_an_argument_whose_guard_fails_writes_nothing_and_arguments_run_left_to_right(tmp_path, cxx):
    artifact = built(tmp_path / "trap", TRAPS, cxx)
    done = subprocess.run([artifact], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and done.stdout == ""  # "before " is never written
    order = built(tmp_path / "order", TRAPS.replace("fn main", "fn unused").replace("fn ordered", "fn main"), cxx)
    done = subprocess.run([order], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and "first" in done.stderr and "second" not in done.stderr


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_write_the_kernel_refuses_ends_the_print_without_a_trap(tmp_path, cxx):
    artifact = built(tmp_path, 'fn main() -> i32 { println("lost", 1); eprintln("kept"); return 0; }\n', cxx)
    done = subprocess.run(f"'{artifact}' >&-", shell=True, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stderr == "kept\n"


def rows(source: str) -> dict:
    return {n: set(f["effects"]) for n, f in compile_source(source)[1]["functions"].items()}


def test_a_print_costs_io_and_the_write_and_format_costs_what_growing_a_vec_does():
    got = rows(program(SCALARS + FORMAT.replace("fn main", "fn formatting")))
    assert got["show"] == {"io", "ffi:write", "ffi_precondition", "read:bytes", "read:line", "trap"}
    assert not {"alloc", "free"} & rows("fn main() -> i32 { println(1, true, 'x', 0.5); return 0; }")["main"]
    assert {"alloc", "free", "trap", "write:out"} <= got["append"] and "io" not in got["append"]


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-PRINT-ARG", "fn f(n:usize, xs:ro<u64>[n]) { println(xs); }"),
        ("E-PRINT-ARG", "struct P { x:u64; }\nfn f(p:P) { println(p); }"),
        ("E-PRINT-ARG", "enum Op { Read; Write; }\nfn f(op:Op) { println(op); }"),
        ("E-PRINT-ARG", "fn f(h:f16) { println(h); }"),
        ("E-PRINT-ARG", "fn f() { let mut v = vec.new[u64](); println(v); }"),
        ("E-FORMAT-TARGET", "fn f() { let mut n:u64 = 0; format(n, 1); }"),
        ("E-FORMAT-TARGET", "fn f() { let mut v = vec.new[u64](); format(v, 1); }"),
        ("E-IMMUTABLE", "fn f() { let v = vec.new[u8](); format(v, 1); }"),
        ("E-ARITY", "fn f() { format(); }"),
        ("E-ARITY", "fn f() { println[u8](1); }"),
        ("E-ALIAS", "fn f() { let mut v = vec.new[u8](); format(v, v); }"),
        ("E-PLACEMENT", "fn f(n:usize, out:rw<f32>[n]@device) { parallel i in n { println(i); out[i] = 1.0; } }"),
        ("E-PARALLEL-CALL", "fn f(n:usize, out:rw<u64>[n]) { parallel i in n { println(i); out[i] = 1; } }"),
        ("E-PARALLEL-WRITE", "fn f(n:usize) { let mut v = vec.new[u8](); parallel i in n { format(v, i); } }"),
        (
            "E-EFFECT-ORDER",
            "extern fn getpid() -> i32 effects(io);\nfn pid() -> i32 { unsafe { return getpid(); } }\n"
            "fn f() { println(pid(), pid()); }",
        ),
    ],
)
def test_what_print_and_format_refuse(code, source):
    refused(code, program(source))


def test_a_program_s_own_print_wins_and_the_library_s_stays():
    own = "fn print(x:u64) -> u64 = x + 1;\nfn main() -> i32 { let y = print(1); return i32(y) - 2; }\n"
    assert "cr::out" not in compile_source(own)[0]
    library = 'import std.io;\nfn main() -> i32 { io.print("x"); return 0; }\n'
    assert "cr::out" not in compile_source(library)[0]


def test_the_projection_round_trips_and_a_print_is_never_smt_equivalent_to_silence():
    source = program(SCALARS)
    assert compile_source(canonical_source(source))[0] == compile_source(source)[0]
    quiet = "fn f(x:u64) -> u64 = x;\n"
    loud = 'fn f(x:u64) -> u64 { println("x = ", x); return x; }\n'
    assert equivalent(quiet, loud, "f")["status"] != "smt-equivalent"


def test_a_freestanding_image_refuses_a_print(tmp_path):
    functions = compile_source("fn main() -> i32 { println(1); return 0; }\n")[1]["functions"]
    with pytest.raises(ProjectError, match=r"'(ffi:write|io)'"):
        audit_effects(functions)
