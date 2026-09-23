"""The plan-only edit: an agent changes how one function's regions are scheduled, and nothing else.

A body edit (`agent_tools.py`) may rewrite a function within its signature and ceiling. A plan edit is narrower and
needs no equivalence check at all: the host pins the whole program, the numerical contract, every interface and every
effect row, and a reply names plan items and values, never source text, so no other change can ride along. Only the
items the checker declares for the kinds of region the function has are open (`concurrency.PLAN_ITEMS`). The host
writes the plan into the source, rechecks the whole linked program, requires every function's receipt to be what it
was apart from this function's plan, and that plan to be the one the reply set, and admits the candidate with what
`cairn predict` says it changes. A reply to a session whose source has moved on, including one already answered, is
stale and changes nothing.

A session opens on a function of any module of a project, named by its qualified name (`lib.spread`). The plan is
written after the function's declaration under the name its own module gives it, and any plan the checker resolved
to the function before is removed, wherever it was written (`perf/plan_source.py`).

    host = PlanHost()
    packet = host.open(source, "spread", sizes=[{"n": 1e7}])
    host.respond({"protocol": "cairn.plan/1", "session": packet["session"], "items": {"grain": 1, "lanes": 8}})
"""

from __future__ import annotations

from typing import Any

from ..compiler.cairnc import compile_source, fail
from ..compiler.concurrency import PLAN_ITEMS, POWERS
from ..perf.plan_source import Placement, text, written
from ..perf.tune import now, regions
from ..projects.project import Project
from .agent_tools import digest, load_json_strict, shaped, stable_json
from .projection import signature

PROTOCOL = "cairn.plan/1"
PINNED = ("every function's body, signature, effect row and guards", "the numerical contract", "every other plan")


class PlanSession:
    """One function of one source, open to a new plan and to nothing else. The source is a program's text, or a
    loaded project, whose files then say where the plan goes."""

    def __init__(self, source: str | Project, symbol: str, sizes: list[dict[str, float]] | None = None,
                 generation: int = 0):  # fmt: skip
        from ..compiler.cairnc import compile_program
        from ..perf.work import count

        self.project = source if isinstance(source, Project) else None
        self.source = source.source if isinstance(source, Project) else source
        self.symbol, self.sizes = symbol, sizes or []
        p, checker, _ = compile_program(self.source)
        self.placement = Placement(self.source, symbol)  # the qualified name, in whichever module declares it
        self.f = self.placement.f
        cost = count(p, checker, {self.f.name})[self.f.name]
        kinds = {r.kind for r in cost.regions} & {"host", "device"}
        if not kinds:
            fail("E-PLAN", f"{symbol} has no parallel region, so a plan has nothing to schedule.")
        several = regions(p, self.f.name) > 1  # fuse joins two regions or more
        self.open = {k: v for k, v in PLAN_ITEMS.items() if v[0] in kinds or (v[0] == "either" and several)}
        self.current = written(now(cost))
        self.receipt = compile_source(self.source)[1]["functions"]
        self.generation = generation  # how many replies this function's plan has taken: a spent session is stale
        self.digest = digest(stable_json([PROTOCOL, digest(self.source), symbol, generation]))

    def where(self) -> dict[str, Any]:
        """The module the plan is written in and, for a project, the file and line of the declaration it follows."""
        place: dict[str, Any] = {"module": self.f.module or "(root)"}
        if self.project is not None:
            file, line = self.project.site(self.f.line)
            place |= {"file": file, "after_line": line + self.source.count("\n", self.f.start, self.f.end)}
        return place

    def packet(self) -> dict[str, Any]:
        from ..perf.report import report

        items = {k: {"region": region, "least": least, "most": most, **({"multiple_of": step} if step > 1 else {}),
                     **({"power_of_two": True} if k in POWERS else {})}
                 for k, (region, least, most, step) in self.open.items()}  # fmt: skip
        packet = {
            "protocol": PROTOCOL,
            "session": self.digest,
            "symbol": self.symbol,
            "signature": signature(self.f),
            "effects": self.receipt[self.f.name]["effects"],
            "current": dict(self.current),
            "items": items,
            "written_in": self.where(),
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
        candidate = self.placement.apply(plan)
        receipt = compile_source(candidate)[1]["functions"]  # the whole linked program, checked again
        scheduled = {"plan", "fused"}  # what a plan sets, and the regions its fuse joined
        mine = {k: v for k, v in receipt.get(self.f.name, {}).items() if k not in scheduled}
        others = {n: r for n, r in receipt.items() if n != self.f.name}
        if (mine != {k: v for k, v in self.receipt[self.f.name].items() if k not in scheduled}
                or others != {n: r for n, r in self.receipt.items() if n != self.f.name}
                or receipt[self.f.name].get("plan", {}) != dict(plan)):  # fmt: skip
            fail("E-PLAN", "The candidate changed more than this function's plan.")  # unreachable: a reply is items
        admitted = {
            "protocol": PROTOCOL,
            "status": "admitted",
            "symbol": self.symbol,
            "plan": text(self.placement.name, plan) or f"(no plan for {self.symbol})",
            "written_in": self.where(),
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

    def open(self, source: str | Project, symbol: str, sizes: list[dict[str, float]] | None = None) -> dict[str, Any]:
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
