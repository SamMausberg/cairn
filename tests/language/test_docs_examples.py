"""Every CAIRN example in the documentation is checked: documentation that does not compile is a claim nobody checked.

A fenced block tagged `cairn` is a complete program and must be accepted. `cairn rejects E-CODE` must be refused
with exactly that diagnostic code. `cairn fragment` is an excerpt that is not compiled; use it sparingly.
"""

import pathlib
import re

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import refused

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCUMENTS = [
    "README.md",
    *sorted(
        str(p.relative_to(ROOT))
        for p in ROOT.glob("docs/**/*.md")
        if p.name != "std_api.md" and "history" not in p.parts
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
