"""Words as map keys: a Vec compares, orders and hashes as its elements, a map keyed by Vecs is looked up and filled
by a view that is copied only on insert, and `sorted` walks the keys in order. Programs are built under both
compilers with the address, leak and undefined-behaviour sanitizers and held to Python's own counting and sorting.
"""

import os
import random
import subprocess
from collections import Counter

import pytest
from test_std_api import examples

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, build

BOTH = ["clang++", "g++"]
ASAN = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}

# Every word of standard input sorted by Vec[u8]'s own order, then the words again folded to lower case and
# ranked by count, most first, ties by word; and a map of owners filled through entry.
RANKED = """
import std.core (Option, Result, Ord, Eq);
import std.io as io;
import std.map (Map);
import std.sort as sort;
import std.text as text;
import std.vec (Vec);

struct Tally { count:u64; word:Vec[u8]; }
derive ord for Tally;   // a Vec[u8] field orders as its bytes do
derive eq for Tally;

fn main() -> i32 {
  let mut input = vec.new[u8]();
  match io.read_stdin_to_end(input) { Ok(_) => {} Err(_) => return 1; }
  let mut words = vec.new[Vec[u8]]();
  let mut folded = map.new[Vec[u8], u64]();
  let mut w = text.cursor();
  while text.next_word(input, w) {
    let word = vec.from(input.data[w.lo..w.hi]);
    vec.push(words, word);
    for i in w.lo..w.hi { input.data[i] = text.to_lower(input.data[i]); }
    let at = map.entry_view(folded, input.data[w.lo..w.hi], 0);
    folded.vals[at] += 1;
  }
  sort.sort(words);
  for i in 0..words.len { println("sorted ", words.data[i]); }
  let mut ranked = vec.new[Tally]();
  let mut order = vec.new[usize]();
  map.sorted(folded, order);
  for s in order {
    let copy = vec.from(folded.keys[s]);
    vec.push(ranked, Tally(folded.vals[s], copy));
  }
  sort.sort_by(ranked, |a:ro<Tally>, b:ro<Tally>| -> bool {
    if a.count != b.count { return a.count > b.count; }
    return less(a.word, b.word);
  });
  for i in 0..ranked.len { println("ranked ", ranked.data[i].word, ' ', ranked.data[i].count); }

  let mut owners = map.new[u64, Vec[u8]]();
  for i in 0..100 {
    let fresh = vec.from(2, "ab");
    let at = map.entry(owners, u64(i % 7), fresh);   // a value not needed is released
    vec.push(owners.vals[at], u8(i));
  }
  let mut keys = vec.new[usize]();
  map.sorted(owners, keys);
  for s in keys { println("owner ", owners.keys[s], ' ', owners.vals[s].len); }
  match map.find_view(folded, 0, "") { Some(_) => return 2; None => {} }
  return 0;
}
"""


def expected(data: bytes) -> list[str]:
    words = data.split()
    lines = [f"sorted {w.decode('latin-1')}" for w in sorted(words)]
    counts = Counter(w.lower() for w in words)
    lines += [f"ranked {w.decode('latin-1')} {n}" for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    lines += [f"owner {k} {2 + len(range(k, 100, 7))}" for k in range(7)]
    return lines


@pytest.mark.parametrize("cxx", BOTH)
def test_words_count_and_sort_as_their_bytes(tmp_path, cxx):
    exe = build(tmp_path, compile_source(RANKED)[0], *SANITIZED, cxx=cxx)
    pick = random.Random(5)
    vocabulary = [b"a", b"A", b"ab", b"abc", b"b", b"Ba", b"zeta", b"\xe9t\xe9", b"a\x00b", b"", b"Zz"]
    inputs = [b"", b"one", b"b a B a", b"x\ty\nx  y\r\nZ"]
    inputs += [b" ".join(pick.choice(vocabulary) for _ in range(pick.randrange(2000))) for _ in range(5)]
    for data in inputs:
        done = subprocess.run([exe], input=data, capture_output=True, timeout=120, env=ASAN)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
        assert done.stdout.decode("latin-1").splitlines() == expected(data)


@pytest.mark.parametrize("cxx", BOTH)
def test_the_word_count_in_std_maps_comment_runs(tmp_path, cxx):
    [source] = examples()["map"]
    exe = build(tmp_path, compile_source(source)[0], *SANITIZED, cxx=cxx)
    data = b"the cat and the hat\nand the bat\n" * 1000
    done = subprocess.run([exe], input=data, capture_output=True, timeout=120, env=ASAN)
    assert done.returncode == 0, done.stderr[-2000:]
    counts = Counter(data.split())
    assert done.stdout.decode().splitlines() == [f"{w.decode()} {counts[w]}" for w in sorted(counts)]
