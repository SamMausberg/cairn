"""Small optional Z3 C-API bridge. No pip binding or network access is needed.

Z3 and the CAIRN-to-SMT translator are trusted. A solver result is not a Lean
proof. Missing libraries, solver timeouts and API errors return unknown.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
import hashlib
import threading
from typing import Any

P = C.c_void_p
U = C.c_uint
S = C.c_char_p
ERROR_CB = C.CFUNCTYPE(None, P, U)
SIGNATURES = {  # the C functions used, with their result and argument types
    "Z3_mk_config": (P, []),
    "Z3_set_param_value": (None, [P, S, S]),
    "Z3_del_config": (None, [P]),
    "Z3_mk_context": (P, [P]),
    "Z3_del_context": (None, [P]),
    "Z3_set_error_handler": (None, [P, ERROR_CB]),
    "Z3_get_error_msg": (S, [P, U]),
    "Z3_mk_solver": (P, [P]),
    "Z3_mk_solver_for_logic": (P, [P, P]),
    "Z3_solver_inc_ref": (None, [P, P]),
    "Z3_solver_dec_ref": (None, [P, P]),
    "Z3_solver_from_string": (None, [P, P, S]),
    "Z3_solver_check": (C.c_int, [P, P]),
    "Z3_solver_get_reason_unknown": (S, [P, P]),
    "Z3_solver_get_model": (P, [P, P]),
    "Z3_model_inc_ref": (None, [P, P]),
    "Z3_model_dec_ref": (None, [P, P]),
    "Z3_model_eval": (C.c_bool, [P, P, P, C.c_bool, C.POINTER(P)]),
    "Z3_mk_string_symbol": (P, [P, S]),
    "Z3_mk_bv_sort": (P, [P, U]),
    "Z3_mk_bool_sort": (P, [P]),
    "Z3_mk_const": (P, [P, P, P]),
    "Z3_get_bool_value": (C.c_int, [P, P]),
    "Z3_get_numeral_string": (S, [P, P]),
    "Z3_get_full_version": (S, []),
    "Z3_interrupt": (None, [P]),
}


class SolverUnavailable(RuntimeError):
    pass


class Solver:
    """One Z3 context. Its `timeout` parameter is what Z3 is asked to keep; a solver for a named logic can run past
    it, so a watchdog interrupts any check still running at `watchdog_ms` (twice the timeout, and half a second more,
    by default), and that check is unknown with the reason."""

    def __init__(self, timeout_ms: int = 3000, watchdog_ms: int | None = None):
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
            raise ValueError("Solver timeout must be 1..30000 milliseconds.")
        library = ctypes.util.find_library("z3")
        if not library:
            raise SolverUnavailable("A system libz3 library is required for semantic checking.")
        try:
            self.lib = C.CDLL(library)
            for name, (result, arguments) in SIGNATURES.items():
                function = getattr(self.lib, name)
                function.restype, function.argtypes = result, arguments
        except (AttributeError, OSError) as e:
            raise SolverUnavailable(str(e)) from e
        self.version = self.lib.Z3_get_full_version().decode()
        self.library = library
        self.timeout_ms = timeout_ms
        self.watchdog_ms = watchdog_ms if watchdog_ms is not None else 2 * timeout_ms + 500
        self.errors: list[str] = []
        self.ctx = None
        cfg = self.lib.Z3_mk_config()
        self.lib.Z3_set_param_value(cfg, b"timeout", str(timeout_ms).encode())
        self.ctx = self.lib.Z3_mk_context(cfg)
        self.lib.Z3_del_config(cfg)

        def on_error(ctx, code):
            message = self.lib.Z3_get_error_msg(ctx, code)
            self.errors.append(message.decode(errors="replace") if message else f"Z3 error {code}")

        self.callback = ERROR_CB(on_error)
        self.lib.Z3_set_error_handler(self.ctx, self.callback)

    def close(self):
        if self.ctx:
            self.lib.Z3_del_context(self.ctx)
            self.ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def check(self, text: str, variables: dict[str, str], logic: str | None = None) -> dict[str, Any]:
        """Check generated SMT assertions and read complete scalar assignments.

        Input SMT is host-generated, not an untrusted model command channel.
        Each call uses a fresh solver. Constants have the supplied stable names.
        A named logic picks the solver for that fragment; the default is general.
        """
        if not self.ctx:
            raise RuntimeError("Solver context is closed.")
        if len(text.encode()) > 8_000_000:
            return {"status": "unknown", "reason": "SMT text exceeds the 8 MB limit."}
        self.errors.clear()
        z = self.lib
        if logic is None:
            solver = z.Z3_mk_solver(self.ctx)
        else:
            solver = z.Z3_mk_solver_for_logic(self.ctx, z.Z3_mk_string_symbol(self.ctx, logic.encode()))
        z.Z3_solver_inc_ref(self.ctx, solver)
        model = None
        common = {
            "solver": "Z3",
            "version": self.version,
            "library": self.library,
            "logic": logic or "general",
            "timeout_ms": self.timeout_ms,
            "query_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

        def unknown(reason: str, **more) -> dict[str, Any]:
            return {**common, "status": "unknown", "reason": reason, **more}

        try:
            z.Z3_solver_from_string(self.ctx, solver, text.encode())
            if self.errors:
                return unknown("SMT parse/API error", errors=list(self.errors))
            fired = threading.Event()

            def interrupt():
                fired.set()
                z.Z3_interrupt(self.ctx)

            watchdog = threading.Timer(self.watchdog_ms / 1000, interrupt)
            watchdog.daemon = True
            watchdog.start()
            try:
                status = z.Z3_solver_check(self.ctx, solver)  # ctypes lets the watchdog run while Z3 works
            finally:
                watchdog.cancel()
                watchdog.join()  # no thread outlives the check, so a caller may fork afterwards
            if fired.is_set():
                return unknown(
                    f"Z3 ran past its {self.timeout_ms} ms timeout and was interrupted at {self.watchdog_ms} ms."
                )
            if self.errors:
                return unknown("SMT API error", errors=list(self.errors))
            if status == -1:
                return {**common, "status": "unsat"}
            if status == 0:
                return unknown(z.Z3_solver_get_reason_unknown(self.ctx, solver).decode())
            if status != 1:
                return unknown("Unexpected solver status.")
            model = z.Z3_solver_get_model(self.ctx, solver)
            z.Z3_model_inc_ref(self.ctx, model)
            values = {}
            for name, ty in variables.items():
                is_bool = ty == "bool"
                bits = 64 if ty == "usize" else (int(ty[1:]) if not is_bool else 1)
                sort = z.Z3_mk_bool_sort(self.ctx) if is_bool else z.Z3_mk_bv_sort(self.ctx, bits)
                symbol = z.Z3_mk_string_symbol(self.ctx, name.encode())
                term = z.Z3_mk_const(self.ctx, symbol, sort)
                result = P()
                if not z.Z3_model_eval(self.ctx, model, term, True, C.byref(result)):
                    return unknown("Model completion failed.")
                if is_bool:
                    val = z.Z3_get_bool_value(self.ctx, result)
                    if val not in {-1, 1}:
                        return unknown("Non-Boolean model value.")
                    values[name] = val == 1
                else:
                    raw = z.Z3_get_numeral_string(self.ctx, result)
                    value = int(raw.decode())
                    if ty.startswith("i") and value >= (1 << (bits - 1)):
                        value -= 1 << bits
                    values[name] = value
            if self.errors:
                return unknown("Model API error", errors=list(self.errors))
            return {**common, "status": "sat", "values": values}
        finally:
            if model:
                z.Z3_model_dec_ref(self.ctx, model)
            z.Z3_solver_dec_ref(self.ctx, solver)
