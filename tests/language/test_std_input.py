"""Reading a program's input: all of standard input in one call, the lines, words and fields of a view as bounds
into it, and fixed-point decimals. Each program is built once under each compiler with the address, leak and
undefined-behaviour sanitizers, and run over many inputs, which Python splits and parses as the reference.
"""

import os
import random
import subprocess
from decimal import Decimal

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, build

BOTH = ["clang++", "g++"]
ASAN = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}

# Standard input read twice, which the second time finds at its end and still open; then every line, word and comma
# field of it as `kind lo hi`, and the capacity the input arrived in.
PIECES = """
import std.core (Result);
import std.io as io;
import std.text as text;
import std.vec (Vec);

fn main() -> i32 {
  let mut input = vec.new[u8]();
  match io.read_stdin_to_end(input) { Ok(got) => { if got != input.len { return 1; } } Err(_) => return 2; }
  match io.read_stdin_to_end(input) { Ok(got) => { if got != 0 { return 3; } } Err(_) => return 4; }
  let mut line = text.cursor();
  while text.next_line(input, line) { println("line ", line.lo, ' ', line.hi); }
  let mut word = text.cursor();
  while text.next_word(input, word) { println("word ", word.lo, ' ', word.hi); }
  let mut field = text.cursor();
  while text.next_field(input, ',', field) { println("field ", field.lo, ' ', field.hi); }
  println("capacity ", vec.capacity(input) - input.len);
  return 0;
}
"""

# Each line of standard input as `places text`, parsed, one answer a line.
FIXED = """
import std.core (Result);
import std.io as io;
import std.text as text;
import std.vec (Vec);

fn main() -> i32 {
  let mut input = vec.new[u8]();
  match io.read_stdin_to_end(input) { Ok(_) => {} Err(_) => return 1; }
  let mut line = text.cursor();
  while text.next_line(input, line) {
    let space = line.lo + 1;
    let places = usize(input.data[line.lo] - '0');
    match text.parse_fixed(input.data[space + 1..line.hi], places) {
      Ok(v) => println("ok ", v);
      Err(why) => {
        match why {
          Empty => println("empty");
          Invalid(at) => println("invalid ", at);
          Overflow(at) => println("overflow ", at);
        }
      }
    }
  }
  return 0;
}
"""


def pieces(data: bytes) -> list[str]:
    """What PIECES prints for `data`, from Python's own splitting: lines as str.splitlines would give them without a
    bare '\\r' ending one, words as bytes.split(), fields as bytes.split(b',')."""
    out, at = [], 0
    while at < len(data):
        end = data.find(b"\n", at)
        stop = len(data) if end < 0 else end
        hi = stop - 1 if end >= 0 and stop > at and data[stop - 1] == 13 else stop
        out.append(f"line {at} {hi}")
        at = stop + 1
    at = 0
    for word in data.split():
        lo = data.index(word, at)
        out.append(f"word {lo} {lo + len(word)}")
        at = lo + len(word)
    at = 0
    for field in data.split(b","):
        out.append(f"field {at} {at + len(field)}")
        at += len(field) + 1
    return out


def fixed(text: str, places: int) -> str:
    """What FIXED prints for one field: the first fault from the left, else the value Python's Decimal scales it to."""
    point = text.find(".") if "." in text else len(text)
    if not text:
        return "empty"
    if point in (0, len(text) - 1):
        return f"invalid {point}"
    digits = 0
    for i, c in enumerate(text):
        if i != point and c not in "0123456789":
            return f"invalid {i}"
        digits = digits if i == point else digits * 10 + int(c)
        if digits >= 1 << 64:
            return f"overflow {i}"
    if len(text) - point - 1 > places:
        return f"invalid {point + 1 + places}"
    value = Decimal(text).scaleb(places)
    assert value == value.to_integral_value()
    return f"overflow {len(text) - 1}" if value >= 1 << 64 else f"ok {int(value)}"


INPUTS = [b"", b"\n", b"\n\n", b"a", b"a\n", b"a\r\n\r\nb", b"x\r", b" a  b\t\n", b",", b"a,,b\n", b"\r\n,\r",
          b"\x0b\x0cword\x00\x85 end", b"one two\nthree,four five\r\n"]  # fmt: skip


@pytest.mark.parametrize("cxx", BOTH)
def test_lines_words_and_fields_are_the_bounds_python_splits_at(tmp_path, cxx):
    exe = build(tmp_path, compile_source(PIECES)[0], *SANITIZED, cxx=cxx)
    pick = random.Random(11)
    shuffled = [bytes(pick.choice(b"ab ,\n\r\t") for _ in range(pick.randrange(40))) for _ in range(60)]
    for data in INPUTS + shuffled:
        done = subprocess.run([exe], input=data, capture_output=True, timeout=60, env=ASAN)
        assert done.returncode == 0, (data, done.returncode, done.stderr[-2000:])
        printed = done.stdout.decode().splitlines()
        assert printed[:-1] == pieces(data), data
    path = tmp_path / "input"  # a regular file on standard input is sized first: one allocation, 4096 spare
    path.write_bytes(b"12 -7\n" * 50000)
    with open(path, "rb") as source:
        done = subprocess.run([exe], stdin=source, capture_output=True, timeout=60, env=ASAN)
    assert done.returncode == 0 and done.stdout.decode().splitlines()[-1] == "capacity 4096", done.stderr[-2000:]


FIELDS = ["0", "7", "12", "12.5", "12.50", "12.505", "0.01", ".5", "5.", ".", "1.2.3", "1,5", "+3", " 3", "-3",
          "18446744073709551615", "18446744073709551616", "184467440737095516.15", "184467440737095516.16",
          "1844674407370955162", "99999999999999999999.9", "0.000", "3.14159"]  # fmt: skip


@pytest.mark.parametrize("cxx", BOTH)
def test_parse_fixed_scales_a_decimal_as_decimal_arithmetic_does(tmp_path, cxx):
    exe = build(tmp_path, compile_source(FIXED)[0], *SANITIZED, cxx=cxx)
    cases = [(places, text) for places in (0, 2, 3) for text in FIELDS] + [(1, ""), (1, "4")]
    stdin = "".join(f"{places} {text}\n" for places, text in cases).encode()
    done = subprocess.run([exe], input=stdin, capture_output=True, timeout=60, env=ASAN)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.decode().splitlines() == [fixed(text, places) for places, text in cases]
