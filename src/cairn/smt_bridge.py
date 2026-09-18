"""Small optional Z3 C-API bridge. No pip binding or network access is needed.

Z3 and the CAIRN-to-SMT translator are trusted. A solver result is not a Lean
proof. Missing libraries, solver timeouts and API errors return unknown.
"""
from __future__ import annotations
import ctypes as C
import ctypes.util
import hashlib
from pathlib import Path
from typing import Any

P = C.c_void_p
U = C.c_uint
S = C.c_char_p
ERROR_CB = C.CFUNCTYPE(None, P, U)

class SolverUnavailable(RuntimeError):
    pass

class Solver:
    def __init__(self, timeout_ms: int = 3000):
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
            raise ValueError('Solver timeout must be 1..30000 milliseconds.')
        library = ctypes.util.find_library('z3')
        if not library:
            raise SolverUnavailable('A system libz3 library is required for semantic checking.')
        try:
            self.lib = C.CDLL(library)
            self._bind('Z3_mk_config', P, [])
            self._bind('Z3_set_param_value', None, [P,S,S])
            self._bind('Z3_del_config', None, [P])
            self._bind('Z3_mk_context', P, [P])
            self._bind('Z3_del_context', None, [P])
            self._bind('Z3_set_error_handler', None, [P,ERROR_CB])
            self._bind('Z3_get_error_msg', S, [P,U])
            self._bind('Z3_mk_solver', P, [P])
            self._bind('Z3_solver_inc_ref', None, [P,P])
            self._bind('Z3_solver_dec_ref', None, [P,P])
            self._bind('Z3_solver_from_string', None, [P,P,S])
            self._bind('Z3_solver_check', C.c_int, [P,P])
            self._bind('Z3_solver_get_reason_unknown', S, [P,P])
            self._bind('Z3_solver_get_model', P, [P,P])
            self._bind('Z3_model_inc_ref', None, [P,P])
            self._bind('Z3_model_dec_ref', None, [P,P])
            self._bind('Z3_model_eval', C.c_bool, [P,P,P,C.c_bool,C.POINTER(P)])
            self._bind('Z3_mk_string_symbol', P, [P,S])
            self._bind('Z3_mk_bv_sort', P, [P,U])
            self._bind('Z3_mk_bool_sort', P, [P])
            self._bind('Z3_mk_const', P, [P,P,P])
            self._bind('Z3_get_bool_value', C.c_int, [P,P])
            self._bind('Z3_get_numeral_string', S, [P,P])
            self._bind('Z3_get_full_version', S, [])
        except (AttributeError,OSError) as e:
            raise SolverUnavailable(str(e)) from e
        self.version = self.lib.Z3_get_full_version().decode()
        self.library = library
        self.timeout_ms = timeout_ms
        self.errors: list[str] = []
        self.ctx = None
        cfg = self.lib.Z3_mk_config()
        self.lib.Z3_set_param_value(cfg,b'timeout',str(timeout_ms).encode())
        self.ctx = self.lib.Z3_mk_context(cfg)
        self.lib.Z3_del_config(cfg)
        def on_error(ctx, code):
            message = self.lib.Z3_get_error_msg(ctx,code)
            self.errors.append(message.decode(errors='replace') if message else f'Z3 error {code}')
        self.callback = ERROR_CB(on_error)
        self.lib.Z3_set_error_handler(self.ctx,self.callback)

    def _bind(self, name, restype, argtypes):
        fn = getattr(self.lib,name)
        fn.restype = restype
        fn.argtypes = argtypes

    def close(self):
        if self.ctx:
            self.lib.Z3_del_context(self.ctx)
            self.ctx = None

    def __enter__(self): return self
    def __exit__(self,*unused): self.close()

    def check(self, text: str, variables: dict[str,str]) -> dict[str,Any]:
        """Check generated SMT assertions and read complete scalar assignments.

        Input SMT is host-generated, not an untrusted model command channel.
        Each call uses a fresh solver. Constants have the supplied stable names.
        """
        if not self.ctx: raise RuntimeError('Solver context is closed.')
        if len(text.encode()) > 8_000_000:
            return {'status':'unknown','reason':'SMT text exceeds the 8 MB limit.'}
        self.errors.clear()
        z = self.lib
        solver = z.Z3_mk_solver(self.ctx)
        z.Z3_solver_inc_ref(self.ctx,solver)
        model = None
        common = {'solver':'Z3','version':self.version,'library':self.library,
                  'timeout_ms':self.timeout_ms,
                  'query_sha256':hashlib.sha256(text.encode()).hexdigest()}
        try:
            z.Z3_solver_from_string(self.ctx,solver,text.encode())
            if self.errors:
                return {**common,'status':'unknown','reason':'SMT parse/API error','errors':list(self.errors)}
            status = z.Z3_solver_check(self.ctx,solver)
            if self.errors:
                return {**common,'status':'unknown','reason':'SMT API error','errors':list(self.errors)}
            if status == -1: return {**common,'status':'unsat'}
            if status == 0:
                return {**common,'status':'unknown','reason':z.Z3_solver_get_reason_unknown(self.ctx,solver).decode()}
            if status != 1: return {**common,'status':'unknown','reason':'Unexpected solver status.'}
            model = z.Z3_solver_get_model(self.ctx,solver)
            z.Z3_model_inc_ref(self.ctx,model)
            values = {}
            for name,ty in variables.items():
                is_bool = ty == 'bool'
                bits = 64 if ty == 'usize' else (int(ty[1:]) if not is_bool else 1)
                sort = z.Z3_mk_bool_sort(self.ctx) if is_bool else z.Z3_mk_bv_sort(self.ctx,bits)
                symbol = z.Z3_mk_string_symbol(self.ctx,name.encode())
                term = z.Z3_mk_const(self.ctx,symbol,sort)
                result = P()
                if not z.Z3_model_eval(self.ctx,model,term,True,C.byref(result)):
                    return {**common,'status':'unknown','reason':'Model completion failed.'}
                if is_bool:
                    val = z.Z3_get_bool_value(self.ctx,result)
                    if val not in {-1,1}: return {**common,'status':'unknown','reason':'Non-Boolean model value.'}
                    values[name] = val == 1
                else:
                    raw = z.Z3_get_numeral_string(self.ctx,result)
                    value = int(raw.decode())
                    if ty.startswith('i') and value >= (1 << (bits-1)): value -= 1 << bits
                    values[name] = value
            if self.errors: return {**common,'status':'unknown','reason':'Model API error','errors':list(self.errors)}
            return {**common,'status':'sat','values':values}
        finally:
            if model: z.Z3_model_dec_ref(self.ctx,model)
            z.Z3_solver_dec_ref(self.ctx,solver)
