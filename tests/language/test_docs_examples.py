"""Every CAIRN example in the documentation is checked: documentation that does not compile is a claim nobody checked.

A fenced block tagged `cairn` is a complete program and must be accepted. `cairn rejects E-CODE` must be refused
with exactly that diagnostic code. `cairn fragment` is an excerpt that is not compiled; use it sparingly. The README's
first kernel is also run, emulated on the host, and held to what its comment says it computes.
"""

import json
import pathlib
import re

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from emitted import refused

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCUMENTS = [
    "README.md",
    *sorted(
        str(p.relative_to(ROOT))
        for p in ROOT.glob("docs/**/*.md")
        if p.name != "std_api.md" and "history" not in p.parts and "std" not in p.parts  # the generated reference
    ),
]
BLOCKS = [
    (f"{name}:{text[: found.start()].count(chr(10)) + 1}", found.group(1).strip(), found.group(2))
    for name in DOCUMENTS
    for text in [(ROOT / name).read_text(encoding="utf-8")]
    for found in re.finditer(r"^```cairn([^\n]*)\n(.*?)^```", text, flags=re.S | re.M)
]


@pytest.mark.parametrize(("where", "tag", "source"), BLOCKS, ids=[where for where, _, _ in BLOCKS])
def test_an_example_is_what_the_compiler_says(where, tag, source):
    if tag == "fragment":
        return
    if tag.startswith("rejects "):
        refused(tag.split()[1], source)
    else:
        assert tag == "", f"{where}: unknown example tag {tag!r}"
        compile_source(source)


# The README's reverse_tiles run for each size in SIZES on x[i] = i + 1, so no element reads as a zero slot of the tile.
REVERSED = """
fn reversed(n:usize) {
  buffer h:f32[n] = zeroed;
  for i in 0..n { h[i] = f32(i + 1); }
  buffer x:f32[n]@device = zeroed;
  buffer out:f32[n]@device = zeroed;
  transfer(x, h);
  reverse_tiles((n + 255) / 256, n, out, x);
  transfer(h, out);
  for i in 0..n { print(h[i], " "); }
  println("");
}
"""
SIZES = (1, 255, 256, 257, 511, 512, 1000)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_readme_kernel_reverses_every_tile_the_last_one_too(tmp_path, capsys, cxx):
    """Every 256 elements of x reversed into out, and the elements after the last whole tile reversed among
    themselves, however many there are, as the kernel's comment says: `cairn run --emulate` under the address
    sanitizer, beside a reference written here. Nothing runs on a GPU."""
    kernel = next(source for where, _, source in BLOCKS if where.startswith("README.md:"))
    assert "fn reverse_tiles(" in kernel
    calls = "".join(f"  reversed({n});\n" for n in SIZES)
    path = tmp_path / "reversed.cairn"
    path.write_text(kernel + REVERSED + "fn main() -> i32 {\n" + calls + "  return 0;\n}\n", encoding="utf-8")
    argv = ["run", str(path), "--emulate", "--device-target", "sm_120", "--cxx", cxx, "--sanitize", "address"]
    assert main([*argv, "--out", str(tmp_path / "out"), "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["exit_code"] == 0 and "Sanitizer" not in record["stderr"], record["stderr"][-2000:]
    for n, line in zip(SIZES, record["stdout"].splitlines(), strict=True):
        tiles = [range(start, min(start + 256, n)) for start in range(0, n, 256)]
        assert [float(v) for v in line.split()] == [float(i + 1) for tile in tiles for i in reversed(tile)], n
