"""std.text's byte classes and std.core's integer limits, as programs built under both compilers and held to Python's
own definitions of each class.
"""

import pytest

from emitted import native

BOTH = ["clang++", "g++"]

# Every byte's classes as a bit each, then the byte to_lower and to_upper give, one byte to a line.
CLASSES = """
import std.text as text;

fn bit(on:bool, k:usize) -> u64 {
  if on { return shl_wrap(u64(1), k); }
  return 0;
}

fn main() -> i32 {
  for i in 0..256 {
    let c = u8(i);
    let classes = bit(text.is_digit(c), 0) | bit(text.is_upper(c), 1) | bit(text.is_lower(c), 2)
      | bit(text.is_alpha(c), 3) | bit(text.is_alnum(c), 4) | bit(text.is_space(c), 5);
    println(i, ' ', classes, ' ', text.to_lower(c), ' ', text.to_upper(c));
  }
  return 0;
}
"""

LIMITS = """
import std.core (U8_MAX, U16_MAX, U32_MAX, U64_MAX, USIZE_MAX, I8_MIN, I8_MAX, I16_MIN, I16_MAX, I32_MIN, I32_MAX,
  I64_MIN, I64_MAX);

fn main() -> i32 {
  if add_wrap(U8_MAX, 1) != 0 || add_wrap(U16_MAX, 1) != 0 || add_wrap(U32_MAX, 1) != 0 { return 1; }
  if add_wrap(U64_MAX, 1) != 0 || add_wrap(USIZE_MAX, 1) != 0 || u64(USIZE_MAX) != U64_MAX { return 2; }
  if i16(I8_MIN) - 1 != -129 || i16(I8_MAX) + 1 != 128 { return 3; }
  if i32(I16_MIN) - 1 != -32769 || i32(I16_MAX) + 1 != 32768 { return 4; }
  if i64(I32_MIN) - 1 != -2147483649 || i64(I32_MAX) + 1 != 2147483648 { return 5; }
  if I64_MIN != -9223372036854775808 || u64(I64_MAX) + 1 != 9223372036854775808 { return 6; }
  let total:u64 = 18446744073709551610;
  if total > core.U64_MAX - 6 { return 0; }        // the test an add needs before it traps
  return 7;
}
"""


def expected(i: int) -> str:
    b = bytes([i])
    flags = [b.isdigit(), b.isupper(), b.islower(), b.isalpha(), b.isalnum(), b.isspace()]
    return f"{i} {sum(1 << k for k, on in enumerate(flags) if on)} {b.lower()[0]} {b.upper()[0]}"


@pytest.mark.parametrize("cxx", BOTH)
def test_every_byte_is_classed_as_ascii_says(tmp_path, cxx):
    done = native(tmp_path, CLASSES, cxx)
    assert done.stdout.splitlines() == [expected(i) for i in range(256)]


@pytest.mark.parametrize("cxx", BOTH)
def test_each_integer_limit_is_the_last_value_before_its_type_wraps(tmp_path, cxx):
    native(tmp_path, LIMITS, cxx)
