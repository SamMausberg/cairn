"""The tools `cairn mcp` serves, each a thin call into a host this package already has.

`check` is `cairn check`. `edit_open` and `edit_request` drive the guarded edit host (`agent_tools.EditHost`),
`plan_open` and `plan_reply` the plan host (`plans.PlanHost`), `implementation_open` and `implementation_submit` the
implementation host (`implementations.ImplementationHost`), and `state` is `cairn state`, with `symbol` one function's
investigation from its candidate history. No rule is decided here: every refusal is the host's own diagnostic.

A session opened on a path writes each change its host admits back to the files it came from (`write_back.py`),
and only while they still hold what the host judged the change against; a session opened on source text writes
nothing. A result is an error exactly when the command line would exit nonzero: a refused program, a refused request,
a stale write or an environment that cannot answer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, Parser, compile_source, fail
from ..projects.project import ProjectError
from .agent_tools import EditHost, load_json_strict
from .diagnostics import explain
from .implementations import ImplementationHost
from .plans import PlanHost
from .projection import local
from .write_back import Files

WHERE = {
    "path": {"type": "string", "description": "A .cairn file, a project directory or a manifest."},
    "source": {"type": "string", "description": "Program text instead of a path; nothing is written back."},
}
SYMBOL = {"type": "string", "description": "A function, with its module when it has one: lib.spread."}
TOOLS: list[dict[str, Any]] = [
    {
        "name": "check",
        "description": "Check a CAIRN program and return typed, or the refusal with its code, file, line and fix.",
        "inputSchema": {"type": "object", "properties": {**WHERE}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "edit_open",
        "description": "Open a guarded edit session on one function and return its packet; an admitted edit is "
        "written back to its file.",
        "inputSchema": {"type": "object", "properties": {
            **WHERE, "symbol": SYMBOL,
            "scope": {"enum": ["focused", "component"]},
            "site": {"type": "string", "description": "An expression site of the packet, such as x3."},
            "preserve": {"enum": ["equivalent", "identical"], "description": "Admit only an edit that keeps "
                         "the function's behaviour, or its code."},
        }, "required": ["symbol"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "edit_request",
        "description": "Send a cairn.edit/2 request (body, expr, expand, explain, predict, shot, state or delta) to "
        "an open edit session.",
        "inputSchema": {"type": "object", "properties": {"request": {
            "type": "object", "description": "As the packet's draft_protocol shows it."}}, "required": ["request"]},
    },
    {
        "name": "plan_open",
        "description": "Open a plan session on a function with parallel regions and return the plan items it may set.",
        "inputSchema": {"type": "object", "properties": {
            **WHERE, "symbol": SYMBOL,
            "sizes": {"type": "array", "items": {"type": "object"}, "description": 'Price at these sizes: [{"n": 1e7}].'},
        }, "required": ["symbol"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "plan_reply",
        "description": "Send a cairn.plan/1 reply; an admitted plan is written after the function's declaration.",
        "inputSchema": {"type": "object", "properties": {"reply": {
            "type": "object", "description": "The packet's reply, with items set."}}, "required": ["reply"]},
    },
    {
        "name": "implementation_open",
        "description": "Open an implementation session on a reference function, pinning its tolerance, test policy "
        "and permitted inputs.",
        "inputSchema": {"type": "object", "properties": {
            **WHERE, "reference": SYMBOL,
            "policy": {"type": "object", "description": "tolerance, domain, budget, seed, probes, seconds; a "
                       "project's regressions file pins its own."},
            "emulate": {"type": "string", "description": "A device target, as sm_120: validate device code on host "
                        "threads, judged against it. The evidence is finite-tested-emulated, never a device run."},
        }, "required": ["reference"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "implementation_submit",
        "description": "Submit an implementation of the session's reference; one that validates is written beside "
        "the reference.",
        "inputSchema": {"type": "object", "properties": {"request": {
            "type": "object", "description": "The packet's reply, with its source."}}, "required": ["request"]},
    },
    {
        "name": "state",
        "description": "Return the program's signatures and effect rows under a digest, what changed since an earlier "
        "digest, or with symbol one function's investigation.",
        "inputSchema": {"type": "object", "properties": {
            **WHERE, "symbol": SYMBOL,
            "since": {"type": "string", "description": "The digest of a state this server returned."},
        }},
        "annotations": {"readOnlyHint": True},
    },
]  # fmt: skip
SCHEMAS = {t["name"]: t["inputSchema"] for t in TOOLS}
ENVIRONMENT = (ProjectError, OSError, ValueError, RecursionError, subprocess.SubprocessError)


class Tools:
    """The hosts of one `cairn mcp` process, and the files each session opened on a path read."""

    def __init__(self, root: Path | None = None):
        self.root = root or Path.cwd()
        self.edits, self.plans, self.implementations = EditHost(), PlanHost(), ImplementationHost()
        self.files: dict[str, Files] = {}  # by edit handle, plan session digest and implementation handle
        self.states: dict[str, dict[str, Any]] = {}  # every state and investigation this server sent, by digest

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The result of one call of the tool `name`, one of TOOLS, and whether it is an error."""
        try:
            if unknown := set(arguments) - set(SCHEMAS[name]["properties"]):
                fail("E-REQUEST", f"{name} takes no {', '.join(sorted(unknown))}.")
            return getattr(self, name)(arguments)
        except Diagnostic as error:
            return explain(error), True
        except ENVIRONMENT as error:
            return {"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT", "message": str(error)}, True

    # Where a program comes from -------------------------------------------------------------------------------------

    def program(self, a: dict[str, Any]) -> tuple[str, Files | None]:
        """The combined source to judge, and for a path the files it came from."""
        if ("path" in a) == ("source" in a):
            fail("E-REQUEST", "Give exactly one of path and source.")
        if "source" in a:
            return text(a, "source"), None
        where = (self.root / text(a, "path")).resolve()
        if not where.is_relative_to(self.root.resolve()):
            fail("E-REQUEST", f"{a['path']} is outside {self.root}, the directory this server serves.")
        files = Files(where)
        return files.project.source, files

    # The tools ------------------------------------------------------------------------------------------------------

    def check(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        source, files = self.program(a)
        try:
            receipt = compile_source(source, every=True)[1]
        except Diagnostic as error:
            return refusal(error, source, files), True
        library = sum(1 for n in receipt["functions"] if n.startswith("std."))
        return {"status": "typed", "functions": receipt["function_count"], "library_functions": library,
                "formal_status": "not-verified"}, False  # fmt: skip

    def edit_open(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        source, files = self.program(a)
        contract = {"preserve": a["preserve"]} if a.get("preserve") else None
        try:
            packet = self.edits.open(source, text(a, "symbol"), contract, (), a.get("scope", "focused"), a.get("site"))
        except Diagnostic as error:
            return refusal(error, source, files), True
        if files is not None:
            self.files[packet["handle"]] = files
            packet["write_back"] = files.home(self.edits.sessions[packet["handle"]].f.line)
        return packet, False

    def edit_request(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request = document(a, "request")
        handle = request.get("handle")
        files = self.files.get(handle) if isinstance(handle, str) else None
        writes = files is not None and request.get("kind") in {"body", "expr"}
        if writes:
            files.current(self.edits.sessions[handle].source)  # stale: refused before the host judges anything
        before = len(self.edits.admitted.get(handle, [])) if isinstance(handle, str) else 0
        answer = self.edits.reply(json.dumps(request))
        admitted = self.edits.admitted.get(handle, []) if isinstance(handle, str) else []
        if len(admitted) == before:
            return answer, answer.get("status") == "rejected"
        if writes:
            try:
                answer["written"] = files.write(self.edits.sessions[handle].source, admitted[-1][0])
            except (Diagnostic, ProjectError):
                admitted.pop()  # the host keeps no change that did not reach the files
                raise
        return answer, False

    def plan_open(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        source, files = self.program(a)
        sizes = a.get("sizes") or None
        if sizes is not None and not (isinstance(sizes, list) and all(isinstance(s, dict) for s in sizes)):
            fail("E-REQUEST", 'sizes is a list of objects from an extent to a number, such as [{"n": 1e7}].')
        try:
            packet = self.plans.open(files.project if files else source, text(a, "symbol"), sizes)
        except Diagnostic as error:
            return refusal(error, source, files), True
        if files is not None:
            self.files[packet["session"]] = files
        return packet, False

    def plan_reply(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        reply = document(a, "reply")
        session = self.plans.sessions.get(reply.get("session")) if isinstance(reply.get("session"), str) else None
        files = self.files.get(reply["session"]) if session is not None else None
        if files is not None and session is not None:
            files.current(session.source)
        answer = self.plans.respond(reply)
        if files is not None and session is not None:
            try:
                answer["written"] = files.write(session.source, self.plans.sessions[answer["next_session"]].source)
            except (Diagnostic, ProjectError):
                self.plans.current[session.symbol] = session.digest  # the host keeps no plan the files did not take
                raise
            self.files[answer["next_session"]] = files
        return answer, False

    def implementation_open(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        from ..projects.target import parse
        from ..verify.validation import Policy, kept

        source, files = self.program(a)
        reference, policy, emulate = text(a, "reference"), a.get("policy"), a.get("emulate")
        if policy is not None and not isinstance(policy, dict):
            fail("E-REQUEST", "policy is an object: tolerance, domain, budget, seed, probes, seconds.")
        if emulate is not None and not isinstance(emulate, str):
            fail("E-REQUEST", "emulate names a device target, as sm_120.")
        self.implementations.regressions = None
        if files is not None:  # the project's regressions file of this reference, and the policy it pinned
            named = [f.name for f in Parser(source).parse().functions
                     if reference in (f.name, local(f.name)) and f.implements is None]  # fmt: skip
            path = files.project.root / "regressions" / f"{named[0] if len(named) == 1 else reference}.json"
            pinned = (kept(path, named[0])[1] or {}).get("policy") if len(named) == 1 else None
            if pinned is not None and policy is not None and Policy.of(policy).record() != Policy.of(pinned).record():
                fail("E-TEST-POLICY", f"{path.relative_to(files.project.root)} pins this reference's validation "
                     "policy; open the session without one.")  # fmt: skip
            policy = pinned if pinned is not None else policy
            self.implementations.regressions = path
        try:
            device = parse(emulate) if emulate is not None else None  # E-TARGET for another spelling
            packet = self.implementations.open(source, reference, policy, device)
        except Diagnostic as error:
            return refusal(error, source, files), True
        if files is not None:
            self.files[packet["handle"]] = files
            packet["write_back"] = files.home(self.implementations.sessions[packet["handle"]].f.line)
        return packet, False

    def implementation_submit(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request = document(a, "request")
        handle = request.get("handle")
        files = self.files.get(handle) if isinstance(handle, str) else None
        session = self.implementations.sessions[handle] if files is not None else None
        base = session.source if session is not None else None
        if files is not None and base is not None:
            files.current(base)
        self.implementations.records = files.project.root / ".cairn" / "history" if files is not None else None
        try:
            answer = self.implementations.respond(request)
        except Diagnostic as error:
            s = self.implementations.sessions.get(handle) if isinstance(handle, str) else None
            return explain(error, s.source if s else ""), True
        if files is not None and base is not None and session is not None:
            try:
                answer["written"] = files.write(base, self.implementations.sessions[handle].source)
            except (Diagnostic, ProjectError):
                self.implementations.sessions[handle] = session  # nor an implementation the files did not take
                raise
        return answer, False

    def state(self, a: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        from . import investigation
        from .state import delta, state

        source, files = self.program(a)
        since = a.get("since")
        earlier = self.states.get(since) if isinstance(since, str) else None
        if since is not None and earlier is None:
            fail("E-SESSION", "This server sent no state with that digest; ask for the state.")
        if a.get("symbol") is None:
            now = state(source, locate=files.project.locate if files else None)
            self.states[now["digest"]] = now
            return (delta(earlier, now) if earlier else now), now["status"] != "typed"
        from ..perf.resources import device_identity, host_target
        from ..projects.target import resolve
        from ..projects.toolchain import resolve_arch
        from .history import vendored

        if files is None:
            fail(
                "E-REQUEST", "An investigation reads the candidate history beside a project's manifest: give its path."
            )
        project = files.project
        device = resolve(None, project.device_target, required=False)
        targets = {"host": host_target(resolve_arch(project.arch), "clang++"), "device": device_identity(device)}
        history = project.root / ".cairn" / "history"
        packet = investigation.investigation(source, text(a, "symbol"), history, targets, vendored(project))
        self.states[packet["digest"]] = packet
        return (investigation.delta(earlier, packet) if earlier else packet), False


def text(a: dict[str, Any], key: str) -> str:
    if not isinstance(a.get(key), str) or not a[key]:
        fail("E-REQUEST", f"{key} is a nonempty string.")
    return a[key]


def document(a: dict[str, Any], key: str) -> dict[str, Any]:
    """A request object, or the same as JSON text, read strictly: a duplicate field is refused."""
    value = a.get(key)
    value = load_json_strict(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        fail("E-REQUEST", f"{key} is an object, as the packet shows it.")
    return value


def refusal(error: Diagnostic, source: str, files: Files | None) -> dict[str, Any]:
    """A refused program as the command line reports it: the diagnostic with its fix, at its file and line, and so
    each further refusal a check found."""
    located = files.project.locate(error) if files else error.data
    record = {**explain(error, source), **located}
    if further := error.data.get("further"):
        record["further"] = [{**explain(Diagnostic.of(d), source), **placed}
                             for d, placed in zip(further, located["further"], strict=True)]  # fmt: skip
    return record
