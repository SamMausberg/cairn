from cairn.agent.agent_tools import EditSession
from cairn.agent.teaching import select_cards


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
