"""The plan-only edit: an agent changes how one function's regions are scheduled, and nothing else.

A body edit (`agent_tools.py`) may rewrite a function within its signature and ceiling. A plan edit is narrower and
needs no equivalence check at all: the host pins the whole program, the numerical contract, every interface and every
effect row, and a reply names plan items and values, never source text, so no other change can ride along. Only the
items the checker declares for the kinds of region the function has are open (`concurrency.PLAN_ITEMS`). The host
writes the plan into the source, rechecks the whole linked program, requires every function's receipt to be what it
was apart from its plan, and admits the candidate with what `cairn predict` says it changes. A reply to a session
whose source has moved on, including one already answered, is stale and changes nothing.

    host = PlanHost()
    packet = host.open(source, "spread", sizes=[{"n": 1e7}])
    host.respond({"protocol": "cairn.plan/1", "session": packet["session"], "items": {"grain": 1, "lanes": 8}})
"""

from __future__ import annotations

from typing import Any

from ..compiler.cairnc import compile_source, fail
from ..compiler.concurrency import PLAN_ITEMS
from ..perf.tune import now, replanned, text, written
from .agent_tools import digest, load_json_strict, shaped, stable_json
from .projection import local, signature

PROTOCOL = "cairn.plan/1"
PINNED = ("every function's body, signature, effect row and guards", "the numerical contract", "every other plan")


class PlanSession:
    """One function of one source, open to a new plan and to nothing else."""

    def __init__(self, source: str, symbol: str, sizes: list[dict[str, float]] | None = None, generation: int = 0):
        from ..compiler.cairnc import compile_program
        from ..perf.work import count

        self.source, self.symbol, self.sizes = source, symbol, sizes or []
        p, checker, _ = compile_program(source)
        found = [f for f in p.functions if symbol in (f.name, f.source_name) and not f.extern]
        if len(found) != 1:
            fail("E-SYMBOL", f"No function {symbol} to plan.")
        self.f = found[0]
        if self.f.module:  # A plan written after the source belongs to its last module, so sessions stay at the root.
            fail("E-EDIT-PROFILE", "A plan session opens on a function of the root module of one source.")
        cost = count(p, checker, {self.f.name})[self.f.name]
        kinds = {r.kind for r in cost.regions} & {"host", "device"}
        if not kinds:
            fail("E-PLAN", f"{symbol} has no parallel region, so a plan has nothing to schedule.")
        self.open = {k: v for k, v in PLAN_ITEMS.items() if v[0] in kinds}
        self.current = written(now(cost))
        self.receipt = compile_source(source)[1]["functions"]
        self.generation = generation  # how many replies this function's plan has taken: a spent session is stale
        self.digest = digest(stable_json([PROTOCOL, digest(source), symbol, generation]))

    def packet(self) -> dict[str, Any]:
        from ..perf.report import report

        items = {k: {"region": region, "least": least, "most": most, **({"multiple_of": step} if step > 1 else {})}
                 for k, (region, least, most, step) in self.open.items()}  # fmt: skip
        packet = {
            "protocol": PROTOCOL,
            "session": self.digest,
            "symbol": self.symbol,
            "signature": signature(self.f),
            "effects": self.receipt[self.f.name]["effects"],
            "current": dict(self.current),
            "items": items,
            "pinned": list(PINNED),
            "reply": {"protocol": PROTOCOL, "session": self.digest, "items": dict.fromkeys(self.open, 0)},
            "note": "A reply sets plan items and nothing else; 0 or a missing item leaves the runtime's choice.",
        }
        if self.sizes:
            packet["predicted"] = report(self.source, self.sizes, {self.f.name})["functions"][self.f.name][
                "predictions"
            ]
        return packet

    def check(self, reply: dict[str, Any]) -> dict[str, Any]:
        from ..perf.report import delta

        shaped(reply, PROTOCOL, {"protocol", "session", "items"})
        items = reply["items"]
        if not isinstance(items, dict) or not all(isinstance(k, str) and type(v) is int for k, v in items.items()):
            fail("E-REQUEST", "items is an object from a plan item to a whole number.")
        if extra := sorted(set(items) - set(self.open)):
            fail("E-PLAN", f"This session may set {', '.join(self.open)}; {', '.join(extra)} is not among them.")
        for k, v in items.items():  # 0 leaves the runtime's choice; anything else is held to the item's range
            _, least, most, step = self.open[k]
            if v and (not least <= v <= most or v % step):
                fail(
                    "E-PLAN",
                    f"{k} runs from {least} to {most}{f', a multiple of {step}' * (step > 1)}; {v} is outside.",
                )
        plan = written(items)
        candidate = replanned(self.source, local(self.symbol), text(local(self.symbol), plan))
        receipt = compile_source(candidate)[1]["functions"]  # the whole linked program, checked again
        unplanned = {n: {k: v for k, v in r.items() if k != "plan"} for n, r in receipt.items()}
        if unplanned != {n: {k: v for k, v in r.items() if k != "plan"} for n, r in self.receipt.items()}:
            fail("E-PLAN", "The candidate changed more than a plan.")  # unreachable while a reply is only items
        admitted = {
            "protocol": PROTOCOL,
            "status": "admitted",
            "symbol": self.symbol,
            "plan": text(local(self.symbol), plan) or f"(no plan for {self.symbol})",
            "candidate_sha256": digest(candidate),
            "rows_unchanged": True,
            "formal_status": "not-verified",
        }
        if self.sizes:
            admitted["predicted"] = delta(self.source, candidate, self.sizes, {self.f.name})["functions"][self.f.name]
        return {**admitted, "candidate": candidate}


class PlanHost:
    """The plan sessions of one conversation: each reply is checked against the source as it now stands."""

    def __init__(self) -> None:
        self.sessions: dict[str, PlanSession] = {}
        self.current: dict[str, str] = {}  # symbol -> the digest of its live session; any other is stale

    def open(self, source: str, symbol: str, sizes: list[dict[str, float]] | None = None) -> dict[str, Any]:
        session = PlanSession(source, symbol, sizes)
        self.sessions[session.digest] = session
        self.current[symbol] = session.digest
        return session.packet()

    def respond(self, reply: Any) -> dict[str, Any]:
        if isinstance(reply, str):
            reply = load_json_strict(reply)
        if not isinstance(reply, dict) or reply.get("protocol") != PROTOCOL:
            fail("E-REQUEST", f"A plan reply is a {PROTOCOL} object; a body or expression edit is not one.")
        session = self.sessions.get(reply.get("session", ""))
        if session is None:
            fail("E-SESSION", "No plan session has that digest.")
        if self.current.get(session.symbol) != session.digest:
            fail("E-SESSION", f"The source of {session.symbol} has moved on since this session opened; open another.")
        admitted = session.check(reply)
        successor = PlanSession(admitted.pop("candidate"), session.symbol, session.sizes, session.generation + 1)
        self.sessions[successor.digest] = successor
        self.current[session.symbol] = successor.digest
        return {**admitted, "next_session": successor.digest}

    def source(self, symbol: str) -> str:
        return self.sessions[self.current[symbol]].source
