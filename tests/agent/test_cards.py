import ast
import pathlib
import re

from cairn.agent.agent_tools import EditSession
from cairn.agent.teaching import CARDS, CODES, TOOL_CARDS, card_of, every_card, select_cards


def test_spacing_does_not_hide_memory():
    cards = select_cards("fn f(){buffer\nx:u64[3]=zeroed;}")
    assert "memory" in cards and "views" in cards


def test_comments_do_not_add_features():
    assert "memory" not in select_cards("fn f(){} // buffer stack compact")


def test_spacing_does_not_hide_compact():
    assert "compact" in select_cards("fn f(){let n=compact\nout for i in n where true yield 0;}")


def test_sum_card_without_match():
    packet = EditSession("enum R {V(u64); E;} fn f()->R=R.E;", "f").packet()
    assert "sums" in packet["rule_cards"]


def test_memory_source_and_cards_are_consistent():
    packet = EditSession("fn f()->usize{buffer x:u64[0]=zeroed;return len(x);}", "f").packet()
    assert "memory" in packet["rule_cards"]
    assert "No inheritance" in packet["rule_cards"]["base"]


# Coverage: every code the compiler, the hosts and the command line can emit has the one card that states its rule.

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "src" / "cairn"
CODE = re.compile(r"E-[A-Z0-9]+(?:-[A-Z0-9]+)*")
NOT_EMITTED = {
    "E-LINEAR": "traits.py matches refusals by this prefix",
    "E-MOVE": "traits.py matches refusals by this prefix",
    "E-UNKNOWN": "the terminal's word for a record that carries no code",
}


def emitted() -> dict[str, str]:
    """Each code written as a whole string in the package, or opening a library recipe's `require` message, with a
    file that writes it."""
    found = {}
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and CODE.fullmatch(node.value):
                found.setdefault(node.value, path.relative_to(SOURCE).as_posix())
    for path in sorted(SOURCE.rglob("*.cairn")):
        for code in re.findall(r'"(E-[A-Z0-9-]+): ', path.read_text(encoding="utf-8")):
            found.setdefault(code, path.relative_to(SOURCE).as_posix())
    return {code: where for code, where in found.items() if code not in NOT_EMITTED}


def test_every_code_the_package_emits_has_a_card():
    unowned = {code: where for code, where in emitted().items() if card_of(code) is None}
    assert not unowned, f"give each code the card that states its rule: {unowned}"


def test_each_code_has_exactly_one_card_and_names_nothing_else():
    owned = [code for codes in CODES.values() for code in codes.split()]
    assert len(owned) == len(set(owned)), sorted({c for c in owned if owned.count(c) > 1})
    assert set(CODES) == set(every_card()) and not set(CARDS) & set(TOOL_CARDS)
    assert set(owned) <= set(emitted()), sorted(set(owned) - set(emitted()))  # no card claims a code nothing emits


def test_a_code_a_card_mentions_is_one_some_card_owns():
    for name, text in every_card().items():
        assert all(card_of(code) for code in CODE.findall(text)), (
            name,
            [c for c in CODE.findall(text) if not card_of(c)],
        )


def test_a_code_no_card_owns_has_no_card():
    assert card_of("E-LEASED") == "tasks" and card_of("E-SESSION") == "hosts" and card_of("E-INTERNAL") == "limits"
    assert card_of("E-MINE") is None and card_of(None) is None  # a recipe's require may choose its own code


def test_where_an_agent_meets_a_limit_its_card_keeps_the_design_through_a_foreign_implementation():
    for name in ("parallel", "cooperative", "implementations", "assembly"):
        assert "foreign implementation" in CARDS[name] and "the foreign card" in CARDS[name], name
