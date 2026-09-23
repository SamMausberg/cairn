"""The second adversarial review of what was added after 1.3: what it found, each fix pinned by the program that
showed it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_4/review/README.md` has the write-up. The first review's table is `test_review_1_4.py`.
"""

from cairn.verify.diff import single

# --- Fixed: an enum's definition is part of the code a diff compares ----------------------------------------------


def test_a_reordered_enum_is_not_identical_code_to_a_function_that_names_it():
    """`identical-code` covers a function and every type it names. A tag-only enum is emitted as `enum class ct_E`,
    which the definition table did not read, so reordering its variants left every function naming it identical."""
    old = "enum E { A; B; }\nfn f(x:u64) -> u64 { if pick(x) == E.A { return 1; } return 2; }\n"
    old += "fn pick(x:u64) -> E { if x > 3 { return E.A; } return E.B; }\n"
    new = old.replace("enum E { A; B; }", "enum E { B; A; }")
    assert single(old, new, "f")["class"] == "smt-equivalent"  # the same answer, from code that is not the same
    assert single(old, new, "pick")["class"] == "signature-changed"  # its result's type is defined differently
    assert single(old, old, "f")["class"] == "identical-code"
