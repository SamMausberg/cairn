"""The counter a cooperative region's finish counts its blocks in on the device, which no call allocates
(runtime/cairn_coop.hpp): a word of a table in the module's global memory, claimed for each launch under a tag no
other launch has, one word for each stream that runs such launches and for each stream of each capture sequence.

So a device library function with a finish has an enqueued entry, cq_NAME, and a call of it makes no stream, event or
allocation and waits for nothing, on a fresh thread and while a capture is on, as the CUDA-call-counting stand-in
(tests/runtime/gpu_host.hpp) shows. The stand-in claims each launch's word as the device launch does and counts the
blocks in it one after another: launches on one stream share a word, two streams never do, a capture sequence keeps
words of its own, and the context's own lane has one. The registry gives up the word of the stream that claimed one
longest ago only when none is free, never a captured stream's, and traps when every word is captured. The count itself,
run by host threads under the thread sanitizer, lets exactly one block of each launch finish, after every other
block's writes, and traps on a word another launch holds. The CUDA build compiles for sm_120 and is not run here.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.compiler.lower.header import header
from emitted import assembled, hosted_library, printed, sanitized

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/cairn/runtime"

LIBRARY = """
// The sum of x in one launch: each block adds a grid-stride share into partial[b], and the block that ends last adds
// the partials.
pub fn total(n:usize, x:ro<u64>[n]@device, g:usize, partial:rw<u64>[g]@device, out:rw<u64>[1]@device) {
  blocks b in g threads t in 64 {
    shared warps:u64[2];
    let mut sum:u64 = 0;
    let mut i = b * 64 + t;
    while i < n {
      sum = add_wrap(sum, x[i]);
      i += g * 64;
    }
    let w = reduce add_wrap warp yield sum;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t == 0 { partial[b] = add_wrap(warps[0], warps[1]); }
  } then threads t in 64 {
    shared warps:u64[2];
    let mut sum:u64 = 0;
    let mut k = t;
    while k < g {
      sum = add_wrap(sum, partial[k]);
      k += 64;
    }
    let w = reduce add_wrap warp yield sum;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t == 0 { out[0] = add_wrap(warps[0], warps[1]); }
  }
}
"""

CALLER = r"""
#include <cstdio>
#include <cstdint>
#include <vector>
#include "coop_host.hpp"
#include "lib.h"

static cr::gpu::HostStream a{0}, b{0};  // two streams a C host owns
static std::vector<std::uint64_t> x(100000), partial(70);
static std::uint64_t out = 0, want = 0;

static void call(const char* what, bool enqueued, cr::gpu::HostStream* on, std::size_t g) {
  out = 7;
  if(enqueued) cq_total(on, x.size(), x.data(), g, partial.data(), &out);
  else cf_total(x.size(), x.data(), g, partial.data(), &out);
  const auto& c = cr::gpu::counted;
  std::printf("{\"what\": \"%s\", \"streams\": %zu, \"events\": %zu, \"allocations\": %zu, \"scratch\": %zu, "
              "\"frees\": %zu, \"stream_waits\": %zu, \"event_waits\": %zu, \"refused\": %zu, \"slot\": %u, "
              "\"tag\": %llu, \"right\": %d}\n", what, c.streams.load(), c.events.load(), c.allocations.load(),
              c.scratch_allocations.load(), c.frees.load(), c.stream_waits.load(), c.event_waits.load(),
              c.refused.load(), cr::coop::finished.slot, (unsigned long long)cr::coop::finished.tag,
              out == (g ? want : 0));
}

int main() {
  for(std::size_t i = 0; i < x.size(); ++i) want += x[i] = i * 2654435761ull;
  auto& counted = cr::gpu::counted;
  counted.capturing = true;  // the thread's first CAIRN calls, captured: its context is made here too
  counted.capture = 11;
  call("captured on a", true, &a, 70);
  call("captured on a again", true, &a, 3);
  counted.capture = 12;
  call("second capture on a", true, &a, 70);
  call("second capture on b", true, &b, 7);  // b joined the same capture
  counted.capturing = false;
  call("enqueued on a", true, &a, 70);
  call("enqueued on b", true, &b, 1);
  call("enqueued on a again", true, &a, 0);  // no blocks: the finish runs once and adds nothing
  call("enqueued on null", true, nullptr, 5);  // the legacy default stream
  call("checked", false, nullptr, 70);  // on the thread's own lane
  call("checked again", false, nullptr, 2);
  return 0;
}
"""

# The registry with three words, and the count run by host threads.
UNITS = r"""
#include <atomic>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>
#include "cairn_coop.hpp"

using cr::coop::Finishes;

// Launches of 1 to 64 blocks, one thread each, on one word after another and on two words at once: each block
// publishes a value, then arrives; exactly one arrives last, and it reads every value.
static int counts() {
  static unsigned long long words[2] = {};
  int wrong = 0;
  std::atomic<std::uint64_t> tag{0};
  auto launches = [&](unsigned long long* word, unsigned seed) {
    for(unsigned k = 0; k < 60; ++k) {
      const unsigned grid = 1 + (seed * 7919u + k * 104729u) % 64;
      const std::uint64_t mine = ++tag;
      std::vector<std::uint64_t> values(grid);
      std::atomic<unsigned> last{0};
      std::atomic<std::uint64_t> seen{0};
      std::vector<std::thread> blocks;
      for(unsigned b = 0; b < grid; ++b)
        blocks.emplace_back([&, b] {
          values[b] = b + 1;  // a plain write, published by the arrival
          if(cr::coop::arrive(word, mine, grid)) {
            ++last;
            std::uint64_t sum = 0;
            for(std::uint64_t v : values) sum += v;  // a plain read of every block's write
            seen = sum;
            cr::coop::depart(word);
          }
        });
      for(auto& t : blocks) t.join();
      if(last != 1 || seen != std::uint64_t(grid) * (grid + 1) / 2 || *word != 0) ++wrong;
    }
  };
  std::thread one(launches, words, 1u), two(launches, words + 1, 2u);
  one.join();
  two.join();
  return wrong;
}

int main(int argc, char** argv) {
  const char* what = argc > 1 ? argv[1] : "";
  if(!std::strcmp(what, "foreign")) {  // a launch arrives in a word another launch still holds
    static unsigned long long word = 0;
    cr::coop::arrive(&word, 5, 3);
    cr::coop::arrive(&word, 6, 3);
    return 0;
  }
  Finishes<3> f;
  if(!std::strcmp(what, "captured")) {  // every word belongs to a capture: a direct stream finds none
    f.claim(1, true, 9);
    f.claim(2, true, 9);
    f.claim(1, true, 10);
    f.claim(1, false, 0);
    return 0;
  }
  const struct { unsigned long long stream; bool captured; unsigned long long capture; } asked[] = {
    {101, false, 0}, {102, false, 0}, {103, false, 0}, {101, false, 0},  // three words, then 101's again
    {104, false, 0},                                                     // none free: 102 claimed longest ago
    {105, true, 9}, {105, true, 9},                                      // 103's, then kept by the capture
    {106, true, 9}, {101, false, 0},                                     // 101's; 101 then takes 104's
    {102, false, 0},                                                     // 101's, the only uncaptured word
  };
  std::printf("[");
  for(const auto& k : asked) {
    const cr::coop::Claim c = f.claim(k.stream, k.captured, k.capture);
    std::printf("[%u, %llu], ", c.slot, (unsigned long long)c.tag);
  }
  std::printf("%d]\n", counts());
  return 0;
}
"""


def steps(tmp_path: Path, cxx: str) -> list[dict]:
    """The library and its caller built with the sanitizers on the host stand-in and run; each step's counts."""
    return printed(hosted_library(tmp_path, LIBRARY, CALLER, cxx, *sanitized(cxx), "-pthread"))


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_an_enqueued_call_with_a_finish_makes_nothing_and_waits_for_nothing(tmp_path, cxx):
    rows = steps(tmp_path, cxx)
    assert all(r["right"] for r in rows), rows
    made = ("streams", "events", "allocations", "scratch", "frees", "stream_waits", "event_waits", "refused")
    enqueued = [r for r in rows if "checked" not in r["what"]]
    assert all(enqueued[-1][k] == 0 for k in made), enqueued[-1]  # captured and enqueued alike, from a fresh thread
    checked = rows[-2]  # the thread's own lane: made once, waited for once a call
    assert checked["streams"] == 1 and checked["stream_waits"] == 1 and checked["allocations"] == 0, checked
    assert rows[-1]["streams"] == 1 and rows[-1]["stream_waits"] == 2 and rows[-1]["scratch"] == 0, rows[-1]


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_launches_that_may_overlap_never_share_a_word(tmp_path, cxx):
    rows = {r["what"]: r for r in steps(tmp_path, cxx)}
    slot = {what: r["slot"] for what, r in rows.items()}
    assert slot["captured on a"] == slot["captured on a again"]  # one after another in the graph
    captured = {slot["captured on a"], slot["second capture on a"], slot["second capture on b"]}
    assert len(captured) == 3  # each capture sequence, and each stream in one, keeps a word of its own
    direct = {slot["enqueued on a"], slot["enqueued on b"], slot["enqueued on null"], slot["checked"]}
    assert len(direct) == 4 and not direct & captured  # a replayed graph never meets a direct launch
    assert slot["enqueued on a again"] == slot["enqueued on a"] and slot["checked again"] == slot["checked"]
    tags = [r["tag"] for r in rows.values()]
    assert tags == sorted(set(tags)), tags  # every launch its own tag


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_the_registry_gives_up_the_oldest_direct_word_and_the_count_finishes_once(tmp_path, cxx):
    if not shutil.which(cxx) or not shutil.which("setarch"):
        pytest.skip(f"needs {cxx} and setarch")
    (tmp_path / "units.cpp").write_text(UNITS)
    exe = tmp_path / "units"
    flags = ["-std=c++20", "-O1", "-g", "-fsanitize=thread"] if cxx == "clang++" else sanitized(cxx)
    line = [cxx, *flags, "-pthread", f"-I{RUNTIME}", str(tmp_path / "units.cpp"), "-o", str(exe)]
    done = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-4000:]
    ran = subprocess.run(["setarch", "-R", str(exe)], capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0 and "ThreadSanitizer" not in ran.stderr, ran.stderr[-3000:]
    *claims, wrong = json.loads(ran.stdout.replace(", ]", "]"))
    assert wrong == 0
    assert [slot for slot, _ in claims] == [0, 1, 2, 0, 1, 2, 2, 0, 1, 1]
    assert [tag for _, tag in claims] == list(range(1, 11))
    for case, said in (("foreign", ""), ("captured", "belongs to a captured graph")):
        ended = subprocess.run(["setarch", "-R", str(exe), case], capture_output=True, text=True, timeout=300)
        assert ended.returncode not in (0, 1) and said in ended.stderr, (case, ended.returncode, ended.stderr[-2000:])


def test_a_function_with_a_finish_has_an_enqueued_entry():
    declared, checks = header(LIBRARY, "lib", device=True)
    assert "void cq_total(void *stream, " in declared and " cq_total(void* stream, " in checks
    assert "E-ENQUEUE" not in declared
    receipt = compile_source(LIBRARY)[1]["functions"]["total"]
    assert not {"gpu_alloc", "gpu_free"} & set(receipt["effects"]) and "par:device" in receipt["effects"]


def test_the_counter_compiles_for_sm_120_to_a_claim_and_a_count_between_two_fences(tmp_path):
    """The library with its enqueued entry, built by nvcc for sm_120 and never run: the kernel claims its word with a
    64-bit compare-and-swap and counts with a 64-bit add, between the two device-wide fences, and nothing allocates."""
    cpp = compile_source(LIBRARY)[0] + "\n" + header(LIBRARY, "lib", device=True)[1]
    sass, _ = assembled(tmp_path, cpp)
    kernel = next(k for k in sass.split("Function :") if "blocks_then" in k.split("\n")[0])
    fences = [m.start() for m in re.finditer(r"MEMBAR\.SC\.GPU", kernel)]
    claim, count = kernel.find("ATOMG.E.CAS.64"), kernel.find("ATOMG.E.ADD.64")
    assert len(fences) == 2 and fences[0] < claim < count < fences[1], (fences, claim, count)
    assert not re.search(r"\b(LDL|STL)\b", sass)
    source = (tmp_path / "p.cpp").read_text()
    assert "cq_total" in source and "cudaMalloc" not in source.split("cq_total")[1]
