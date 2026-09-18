"""Type, shape and conservative interprocedural effect checking."""
from __future__ import annotations
import copy
from dataclasses import dataclass
from .syntax import *

@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None

class Checker:
    def __init__(self, program: Program, capture_sites: bool = False):
        self.p = program
        self.capture_sites = capture_sites
        self.sites: list[dict[str, Any]] = []
        self.call_edges: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.fs = {f.name:f for f in program.functions}
        builtin_names = set(CPP) | {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr", "min", "max", "len"}
        for name in set(self.fs) | set(program.records) | set(program.enums) | set(program.sums):
            if name in builtin_names:
                fail("E-BUILTIN-NAME", f"Cannot redefine builtin {name}.")
        self.env: dict[str,Binding] = {}
        self.f: Function | None = None
        self.local_effects: dict[str,set[str]] = {}
        self.calls: dict[str,set[str]] = {}
        self.checks: dict[str,dict[str,int]] = {}
        self.effects: set[str] = set()
        self.callset: set[str] = set()
        self.counts: dict[str,int] = {}
        self.nodes = 0
        self.locals_by_function: dict[str, set[str]] = {}
        self.resources: dict[str, list[dict[str, Any]]] = {}
    def type_ok(self, ty: Type, node=None):
        if ty.name not in CPP and ty.name not in self.p.records and ty.name not in self.p.enums and ty.name not in self.p.sums:
            fail("E-TYPE", f"Unknown type {ty.name}.",node)
        if ty.mode != "value" and ty.name == "void": fail("E-TYPE", "A slice cannot contain void.",node)
        if ty.mode != "value" and ty.name in self.p.sums:
            fail("E-SUM-VIEW", "Borrowed arrays of tagged payloads are not in this ABI profile.",node)
    def effect(self, name: str): self.effects.add(name)
    def guard(self, kind: str):
        self.effects.add("trap")
        self.counts[kind] = self.counts.get(kind,0) + 1
    def expect(self, got: Type, want: Type, e: Expr):
        if got != want: fail("E-TYPE-MISMATCH", f"Expected {want.display()}, got {got.display()}.",e, expected_type=want.display(), actual_type=got.display())
    def check(self) -> dict[str,Any]:
        seen = set(CPP)
        for n, fields in self.p.records.items():
            if n in CPP: fail("E-TYPE-NAME", f"Cannot redefine builtin {n}.")
            if not fields: fail("E-RECORD", "Empty records are outside this ABI subset.")
            if len({x for x,_ in fields}) != len(fields): fail("E-DUPLICATE", f"Duplicate field in {n}.")
            for _,ty in fields:
                if ty.mode != "value" or ty.name not in seen or ty.name == "void":
                    fail("E-RECORD-TYPE", "Record fields must be previously declared value types, not views or void.")
            seen.add(n)
        for n, variants in self.p.enums.items():
            if n in CPP or not variants or len(set(variants)) != len(variants): fail("E-ENUM", f"Invalid enum {n}.")
        for n,variants in self.p.sums.items():
            if n in CPP or not variants or len({v for v,_ in variants})!=len(variants):
                fail("E-ENUM",f"Invalid sum {n}.")
            for v,ty in variants:
                if ty is not None and (ty.mode!="value" or ty.name not in NUMERIC|{"bool"}):
                    fail("E-SUM-PAYLOAD", "Payloads currently require scalar value types, never borrows.")
        for f in self.p.functions:
            self.f = f; self.env = {}; self.effects = set(); self.callset=set(); self.counts={}
            self.loop_depth = 0
            self.call_edges[f.name] = []
            self.locals_by_function[f.name] = set()
            self.resources[f.name] = []
            for n,ty in f.params:
                self.type_ok(ty,f)
                if n in self.env or ty.name == "void": fail("E-PARAM", f"Invalid or duplicate parameter {n}.",f)
                if ty.mode != "value":
                    if ty.extent.isdigit():
                        if int(ty.extent) > 2**63-1: fail("E-EXTENT", "Static extent exceeds bootstrap bound.",f)
                    elif ty.extent not in self.env or self.env[ty.extent].ty != Type("usize"):
                        fail("E-EXTENT", "Dynamic extent must name an earlier immutable usize parameter.",f)
                    self.effects.add("ffi_precondition")
                    self.guard("view_entry")
                self.env[n] = Binding(ty)
                if ty.name in self.p.enums and ty.mode == "value":
                    self.guard("enum_entry")
            if f.static:
                if f.static in self.env: fail("E-DUPLICATE", "Static binder shadows a parameter.",f)
                self.env[f.static] = Binding(Type("usize"),constant=f.binding)
            self.type_ok(f.ret,f)
            if f.ret.mode != "value": fail("E-ESCAPE", "Borrowed view returns are not in the native subset.",f)
            terminated = self.block(f.body)
            if f.ret.name != "void" and not terminated: fail("E-RETURN", f"Not all paths of {f.name} return.",f)
            self.local_effects[f.name] = set(self.effects)
            self.calls[f.name] = self.callset
            self.checks[f.name] = self.counts
        # Private local owners do not escape through interfaces. Their storage
        # and read/write effects remain visible without exporting local names.
        def exposed(function, effect):
            if effect.startswith(("read:", "write:")):
                kind, name = effect.split(":", 1)
                if name in self.locals_by_function[function]:
                    return "local_" + kind
            return effect
        self.local_effects = {f:{exposed(f,e) for e in es}
                              for f,es in self.local_effects.items()}
        # Footprints are instantiated at each call site, not copied under the
        # callee's parameter names. A recursive argument permutation can require
        # more iterations than the number of functions.
        effects = {n:set(es) for n,es in self.local_effects.items()}
        for n in effects:
            todo = list(self.calls[n]); visited = set()
            while todo:
                q = todo.pop()
                if q == n: effects[n].add("diverge"); break
                if q in visited: continue
                visited.add(q); todo.extend(self.calls[q])
        global_effects = {x for es in effects.values() for x in es if ":" not in x}
        bound = 1 + sum(2*len(f.params) + len(global_effects) for f in self.p.functions)
        for _ in range(bound):
            changed = False
            for n in effects:
                before = len(effects[n])
                for callee, mapping in self.call_edges[n]:
                    for effect in tuple(effects[callee]):
                        if effect.startswith(("read:", "write:")):
                            kind, formal = effect.split(":",1)
                            if formal not in mapping:
                                fail("E-INTERNAL", "Unmapped callee memory footprint.")
                            effects[n].add(exposed(n, kind + ":" + mapping[formal]))
                        else:
                            effects[n].add(effect)
                changed |= len(effects[n]) != before
            if not changed: break
        else:
            fail("E-EFFECT-LIMIT", "Effect fixed point exceeded its finite universe.")
        # Unspecified C++ argument/operand evaluation must not reorder observable writes.
        # Calls that can write are allowed only at a whole-expression root.
        # This deliberately rejects some well-defined programs rather than silently
        # choosing an order that differs from the documented native semantics.
        def audit_expr(e: Expr, root: bool = True):
            if e.tag == "call" and e.val in effects and not root:
                if any(x.startswith("write:") or x in {"alloc","free"} for x in effects[e.val]):
                    fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", e)
            for child in e.args: audit_expr(child, False)
        def audit_block(ss: list[Stmt]):
            for s in ss:
                for i,e in enumerate(s.exprs):
                    audit_expr(e, s.tag != "compact" and not (s.tag == "assign" and i == 0))
                audit_block(s.body); audit_block(s.other)
                for arm in s.arms: audit_block(arm.body)
        for f in self.p.functions: audit_block(f.body)
        return {n:{"effects":sorted(effects[n]),"calls":sorted(self.calls[n]),
                   "syntactic_check_sites":self.checks[n],"heap_allocations":sum(x["kind"]=="buffer" for x in self.resources[n]),
                   "allocation_count_kind":"syntactic-sites-not-dynamic-bound",
                   "local_storage":self.resources[n],
                   "implicit_synchronization":0,"status":"prototype-checked-not-proved"} for n in effects}
    def block(self, ss: list[Stmt]) -> bool:
        saved = dict(self.env); returned=False
        for s in ss:
            if returned: fail("E-UNREACHABLE", "Statement after unconditional return.",s)
            returned = self.stmt(s)
        self.env = saved
        return returned
    def stmt(self, s: Stmt) -> bool:
        if s.tag in {"buffer", "stack"}:
            if s.name in self.env:
                fail("E-SHADOW", "Local owner name is already bound.", s)
            self.type_ok(s.ty,s)
            if s.ty.mode != "value" or s.ty.name not in NUMERIC | {"bool"}:
                fail("E-OWNER-ELEMENT", "Local buffers currently require scalar elements.", s)
            extent=s.exprs[0]
            self.expr(extent,Type("usize"))
            if extent.tag not in {"name","int"}:
                fail("E-OWNER-EXTENT", "Bind a computed capacity to an immutable usize first.", extent)
            if extent.tag=="name" and self.env[extent.val].mutable:
                fail("E-OWNER-EXTENT", "A buffer capacity must be immutable.", extent)
            if s.tag=="stack":
                if extent.tag!="int":
                    fail("E-STACK-EXTENT", "Stack storage needs a literal capacity.",extent)
                size=1 if s.ty.name=="bool" else WIDTH.get(s.ty.name,32 if s.ty.name=="f32" else 64)//8
                prior=sum(x.get("bytes",0) for x in self.resources[self.f.name] if x["kind"]=="stack")
                if prior+int(extent.val)*size>65536:
                    fail("E-STACK-LIMIT", "Explicit stack declarations total at most 65536 bytes per function.",extent)
            if extent.tag=="int" and int(extent.val)>2**63-1:
                fail("E-OWNER-EXTENT", "Capacity exceeds the native object limit.",extent)
            # Extent is a literal or immutable scalar, so this identity remains
            # valid throughout the borrow. The C++ owner is never exposed as a value.
            assert self.f is not None
            self.env[s.name]=Binding(Type(s.ty.name,"rw",extent.val))
            self.locals_by_function[self.f.name].add(s.name)
            self.resources[self.f.name].append({"name":s.name,"kind":s.tag,
                "element":s.ty.name,"capacity":extent.val,"initialization":"zeroed",
                "release":"lexical-on-normal-exit","line":s.line})
            if s.tag=="stack": self.resources[self.f.name][-1]["bytes"]=int(extent.val)*size
            self.effect("zero_init")
            if s.tag=="buffer":
                self.effect("alloc"); self.effect("free"); self.guard("allocation")
            else: self.effect("stack_storage")
        elif s.tag in {"let","reg"}:
            if s.name in self.env: fail("E-SHADOW", f"{s.name} is already bound; shadowing is forbidden in this subset.",s)
            if s.ty: self.type_ok(s.ty,s)
            ty = self.expr(s.exprs[0],s.ty)
            if ty.mode != "value" or ty.name == "void": fail("E-VIEW-ALIAS", "Local view aliases and void values are outside this subset.",s)
            s.ty = ty; self.env[s.name] = Binding(ty,s.tag=="reg")
        elif s.tag == "compact":
            out, hi, pred, value = s.exprs
            if s.name in self.env or s.binder in self.env or s.name == s.binder:
                fail("E-SHADOW", "Collector names must be fresh and distinct.", s)
            if out.val not in self.env or self.env[out.val].ty.mode != "rw":
                fail("E-WRITE-LEASE", "Compaction target must be a direct rw parameter.", out)
            t = self.env[out.val].ty
            self.expr(out); self.expr(hi, Type("usize"))
            known_extent = hi.val if hi.tag in {"name","int"} else None
            if hi.tag=="call" and hi.val=="len" and len(hi.args)==1 and hi.args[0].tag=="name":
                known_extent=self.env[hi.args[0].val].ty.extent
            if known_extent != t.extent:
                fail("E-COLLECT-CAPACITY", "Compaction requires iteration extent equal to output capacity.", hi)
            self.env[s.binder] = Binding(Type("usize"))
            self.expr(pred, Type("bool")); self.expr(value, Type(t.name))
            def mentions(e, name):
                return (e.tag == "name" and e.val == name) or any(mentions(x,name) for x in e.args)
            if mentions(pred, out.val) or mentions(value, out.val):
                fail("E-COLLECT-SELF-READ", "Collector predicate/projection cannot read its output.", s)
            del self.env[s.binder]
            self.env[s.name] = Binding(Type("usize"))
            self.effect("write:"+out.val)
            self.counts["bounded_collectors"] = self.counts.get("bounded_collectors",0)+1
        elif s.tag == "assign":
            ty = self.lvalue(s.exprs[0]); self.expr(s.exprs[1],ty)
        elif s.tag in {"break","continue"}:
            if not self.loop_depth: fail("E-LOOP-CONTROL",s.tag+" requires an enclosing loop.",s)
            return True # Stops this lexical block, not necessarily the function.
        elif s.tag == "return":
            assert self.f
            if self.f.ret.name == "void":
                if s.exprs: fail("E-RETURN", "Void function cannot return a value.",s)
            else:
                if not s.exprs: fail("E-RETURN", "Missing return value.",s)
                self.expr(s.exprs[0],self.f.ret)
            return True
        elif s.tag == "if":
            self.expr(s.exprs[0],Type("bool"))
            a=self.block(s.body); b=self.block(s.other)
            return bool(s.other) and a and b
        elif s.tag == "match":
            ty=self.expr(s.exprs[0])
            if ty.mode!="value" or ty.name not in self.p.enums and ty.name not in self.p.sums:
                fail("E-MATCH-TYPE", "match requires a declared enum or tagged sum.",s)
            variants=dict(self.p.sums[ty.name]) if ty.name in self.p.sums else {v:None for v in self.p.enums[ty.name]}
            expected={ty.name+"."+v for v in variants}
            given=[a.variant for a in s.arms]
            if len(set(given))!=len(given): fail("E-MATCH-DUPLICATE","A variant may appear only once.",s)
            if set(given)!=expected:
                fail("E-MATCH-COVERAGE", "Every variant must have exactly one arm.",s,
                     missing_variants=sorted(expected-set(given)),unknown_variants=sorted(set(given)-expected))
            returns=[]
            for arm in s.arms:
                payload=variants[arm.variant.split(".")[1]]
                if bool(arm.binder)!=(payload is not None):
                    fail("E-MATCH-BINDING", "A payload arm binds exactly one value; a nullary arm binds none.",arm)
                if arm.binder:
                    if arm.binder in self.env: fail("E-SHADOW","Payload binder must be fresh.",arm)
                    self.env[arm.binder]=Binding(payload)
                returns.append(self.block(arm.body))
                if arm.binder: del self.env[arm.binder]
            self.guard("tag")
            return all(returns)
        elif s.tag == "while":
            self.expr(s.exprs[0],Type("bool")); self.effect("diverge")
            self.loop_depth+=1; self.block(s.body); self.loop_depth-=1
        elif s.tag == "for":
            self.expr(s.exprs[0],Type("usize")); self.expr(s.exprs[1],Type("usize"))
            if s.name in self.env: fail("E-SHADOW", f"Loop binder {s.name} already exists.",s)
            self.env[s.name]=Binding(Type("usize"))
            self.loop_depth+=1; self.block(s.body); self.loop_depth-=1
            del self.env[s.name]
        elif s.tag == "expr":
            ty = self.expr(s.exprs[0])
            if s.exprs[0].tag != "call": fail("E-DISCARD", "Only calls may be used as discarded expression statements.",s)
            if ty.name != "void": fail("E-DISCARD", "Nonvoid result must be bound or returned.",s)
        else: fail("E-INTERNAL", f"Unknown statement {s.tag}.",s)
        return False
    def lvalue(self, e: Expr) -> Type:
        if e.tag == "name":
            if e.val not in self.env or not self.env[e.val].mutable:
                fail("E-IMMUTABLE", f"{e.val} is not a mutable local.",e)
            return self.expr(e)
        if e.tag == "index":
            base=e.args[0]
            if base.tag != "name" or base.val not in self.env or self.env[base.val].ty.mode != "rw":
                fail("E-WRITE-LEASE", "Indexed assignment requires a direct rw slice parameter.",e)
            self.effect("write:"+base.val)
            return self.index(e, False)
        if e.tag == "field":
            self.lvalue(e.args[0])
            return self.expr(e)
        fail("E-LVALUE", "Assignment requires a mutable variable, field, or rw element.",e)
    def index(self, e: Expr, read: bool=True) -> Type:
        a,i=e.args
        ty=self.expr(a)
        if ty.mode == "value": fail("E-INDEX", "Only views can be indexed.",e)
        self.expr(i,Type("usize")); self.guard("bounds")
        if read: self.effect("read:" + a.val)
        length=ty.extent if ty.extent.isdigit() else "v_"+ty.extent
        e.ty=Type(ty.name); e.cpp=f"cr::at({a.cpp}, {i.cpp}, {length})"
        return e.ty
    def expr(self, e: Expr, expected: Type|None=None) -> Type:
        self.nodes += 1
        if self.nodes > MAX_NODES: fail("E-AST-LIMIT", "Expanded typechecking exceeds 200000 expression visits.",e)
        tag=e.tag
        if tag == "int":
            ty = expected if expected and expected.mode=="value" and expected.name in NUMERIC else Type("u64")
            n=int(e.val)
            if ty.name in WIDTH:
                bits=WIDTH[ty.name]-(1 if ty.name in SIGNED else 0)
                if n >= 2**bits: fail("E-LITERAL-RANGE",f"Literal is not representable in {ty.name}.",e)
            elif n > 2**53: fail("E-LITERAL-RANGE","Large integer-to-float literals require a checked explicit conversion.",e)
            e.cpp=f"static_cast<{ty.cpp()}>({n}{'ULL' if n < 2**64 else ''})"
        elif tag == "float":
            ty=expected if expected and expected.name in FLOAT else Type("f64")
            import math
            v=float(e.val)
            if not math.isfinite(v) or (ty.name=="f32" and abs(v)>3.4028234663852886e38):
                fail("E-LITERAL-RANGE", "Nonfinite/overflowing floating literal.",e)
            e.cpp=e.val + ("f" if ty.name=="f32" else "")
        elif tag == "bool": ty=Type("bool"); e.cpp=e.val
        elif tag == "name":
            if e.val not in self.env: fail("E-UNBOUND", f"Unbound name {e.val}.",e, available_names=sorted(self.env), expected_type=expected.display() if expected else None)
            b=self.env[e.val]; ty=b.ty
            e.cpp=f"static_cast<std::size_t>({b.constant}ULL)" if b.constant is not None else "v_"+e.val
        elif tag == "index":
            ty=self.index(e)
        elif tag == "field":
            a=e.args[0]
            if a.tag=="name" and a.val in self.p.sums and a.val not in self.env:
                ty=self.constructor(a.val,e.val,[],e)
            elif a.tag=="name" and a.val in self.p.enums and a.val not in self.env:
                if e.val not in self.p.enums[a.val]: fail("E-ENUM-VARIANT", f"Unknown {a.val}.{e.val}.",e)
                ty=Type(a.val); e.cpp="ct_"+a.val+"::v_"+e.val
            else:
                at=self.expr(a)
                if at.mode!="value" or at.name not in self.p.records: fail("E-FIELD", "Field access requires a record.",e)
                fs=dict(self.p.records[at.name])
                if e.val not in fs: fail("E-FIELD", f"Unknown field {e.val}.",e)
                ty=fs[e.val]; e.cpp=f"({a.cpp}).v_{e.val}"
        elif tag == "unary":
            a=e.args[0]; ty=self.expr(a, Type("bool") if e.val=="!" else expected)
            if ty.mode!="value": fail("E-OPERATOR", "Unary operator on a view.",e)
            if e.val=="!": self.expect(ty,Type("bool"),e); e.cpp=f"(!{a.cpp})"
            elif e.val=="~":
                if ty.name not in UNSIGNED: fail("E-OPERATOR", "Bitwise complement requires an unsigned integer.",e)
                e.cpp=f"static_cast<{ty.cpp()}>(~{a.cpp})"
            elif ty.name in FLOAT: e.cpp=f"(-{a.cpp})"
            elif ty.name in SIGNED:
                self.guard("overflow"); e.cpp=f"cr::sub<{ty.cpp()}>(0, {a.cpp})"
            else: fail("E-OPERATOR", "Negation requires a signed integer or floating value.",e)
        elif tag == "binary":
            a,b=e.args; op=e.val
            logical=op in {"&&","||"}; compare=op in {"==","!=","<","<=",">",">="}
            hint=Type("bool") if logical else (None if compare else expected)
            # Literals adapt to their nonliteral peer; no general implicit conversion.
            if a.tag in {"int","float"} and b.tag not in {"int","float"}:
                right=self.expr(b,hint); left=self.expr(a,right)
            else:
                left=self.expr(a,hint); right=self.expr(b,left)
            self.expect(right,left,e)
            if left.mode!="value": fail("E-OPERATOR", "View operators are not implicit loops.",e)
            if logical:
                self.expect(left,Type("bool"),e); ty=left; e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif compare:
                if left.name not in NUMERIC|{"bool"} and not (op in {"==","!="} and left.name in self.p.enums):
                    fail("E-OPERATOR", "Comparison requires scalars or equality on enums.",e)
                ty=Type("bool"); e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif op in {"&","|","^"}:
                if left.name not in UNSIGNED: fail("E-OPERATOR", "Bitwise operation requires unsigned scalars.",e)
                ty=left; e.cpp=f"static_cast<{ty.cpp()}>({a.cpp} {op} {b.cpp})"
            elif left.name in FLOAT and op!="%":
                ty=left; e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif left.name in INT:
                ty=left; fn={"+":"add","-":"sub","*":"mul","/":"divide","%":"remainder"}[op]
                self.guard("division" if op in {"/","%"} else "overflow")
                e.cpp=f"cr::{fn}<{ty.cpp()}>({a.cpp}, {b.cpp})"
            else: fail("E-OPERATOR", f"{op} not defined on {left.name}.",e)
        elif tag == "call":
            ty=self.call(e,expected)
        else: fail("E-INTERNAL", f"Unknown expression {tag}.",e)
        e.ty=ty
        if expected: self.expect(ty,expected,e)
        if self.capture_sites and e.start >= 0:
            self.sites.append({"symbol": self.f.name if self.f else "", "start":e.start,
                "end":e.end, "tag":e.tag, "type":ty.display(),
                "expected_type":expected.display() if expected else None,
                "bindings":{n:{"type":b.ty.display(), "mutable":b.mutable,
                    "constant":b.constant} for n,b in self.env.items()}})
        return ty
    def constructor(self, name: str, variant: str, args: list[Expr], e: Expr) -> Type:
        variants=dict(self.p.sums[name])
        if variant not in variants:
            fail("E-ENUM-VARIANT", f"Unknown {name}.{variant}.",e,available_variants=list(variants))
        payload=variants[variant]
        if len(args)!=(1 if payload else 0):
            fail("E-SUM-ARITY", "Constructor arguments must match the declared payload.",e)
        if payload: self.expr(args[0],payload)
        tag=list(variants).index(variant)
        value=args[0].cpp if payload else "0"
        e.cpp=f"ct_{name}{{{tag}, {{.v_{variant} = {value}}}}}"
        return Type(name)
    def call(self,e:Expr,expected:Type|None)->Type:
        n=e.val; args=e.args
        if "." in n:
            name,variant=n.split(".",1)
            if name not in self.p.sums or name in self.env:
                fail("E-CALLEE","Qualified calls are declared tagged-sum constructors, not methods.",e)
            return self.constructor(name,variant,args,e)
        if n == "len":
            if len(args)!=1 or args[0].tag!="name":
                fail("E-LEN", "len takes one direct borrowed view or local buffer.",e)
            t=self.expr(args[0])
            if t.mode=="value": fail("E-LEN", "len requires an array view.",e)
            e.cpp=("static_cast<std::size_t>("+t.extent+"ULL)" if t.extent.isdigit() else "v_"+t.extent)
            return Type("usize")
        if n in NUMERIC:
            if len(args)!=1: fail("E-ARITY", "Scalar conversion takes one argument.",e)
            src=self.expr(args[0]); ty=Type(n)
            if src.mode!="value" or src.name not in NUMERIC: fail("E-CAST", "Conversion requires numeric scalar.",e)
            if src.name in FLOAT and n in INT:
                fail("E-CAST", "Float-to-integer conversion is not in the bootstrap subset.",e)
            if src.name in INT and n in INT:
                self.guard("conversion"); e.cpp=f"cr::convert<{ty.cpp()}>({args[0].cpp})"
            else: e.cpp=f"static_cast<{ty.cpp()}>({args[0].cpp})"
            return ty
        if n in {"add_wrap","sub_wrap","mul_wrap","shl_wrap","shr"}:
            if len(args)!=2: fail("E-ARITY",f"{n} takes two arguments.",e)
            a,b=args
            if a.tag=="int" and b.tag!="int" and n not in {"shl_wrap","shr"}:
                t=self.expr(b,expected); self.expr(a,t)
            else:
                t=self.expr(a,expected); self.expr(b,Type("usize") if n in {"shl_wrap","shr"} else t)
            if t.mode!="value" or t.name not in UNSIGNED: fail("E-WRAP-TYPE", "Wrapping/bit shift operations require unsigned integers.",e)
            if n in {"shl_wrap","shr"}: self.guard("shift")
            e.cpp=f"cr::{n}<{t.cpp()}>({a.cpp}, {b.cpp})"; return t
        if n in {"min","max"}:
            if len(args)!=2: fail("E-ARITY",f"{n} takes two arguments.",e)
            t=self.expr(args[0],expected); self.expr(args[1],t)
            if t.name not in INT or t.mode!="value": fail("E-MINMAX", "Bootstrap min/max are integer-only; floating NaN semantics must be explicit.",e)
            e.cpp=f"std::{n}({args[0].cpp}, {args[1].cpp})"; return t
        if n in self.p.records:
            fs=self.p.records[n]
            if len(fs)!=len(args): fail("E-ARITY",f"Record {n} expects {len(fs)} fields in declaration order.",e)
            for a,(_,t) in zip(args,fs): self.expr(a,t)
            e.cpp="ct_"+n+"{"+", ".join(a.cpp for a in args)+"}"; return Type(n)
        if n not in self.fs: fail("E-CALLEE",f"Unknown callable {n}; arbitrary C++ names are not allowed.",e)
        f=self.fs[n]
        if len(args)!=len(f.params): fail("E-ARITY", f"{n} expects {len(f.params)} arguments.",e)
        subst={name:a for (name,_),a in zip(f.params,args)}
        views=[]
        for a,(name,t) in zip(args,f.params):
            if t.mode=="value": self.expr(a,t)
            else:
                at=self.expr(a)
                ext=t.extent
                if not ext.isdigit():
                    v=subst[ext]
                    if v.tag=="call" and v.val=="len" and len(v.args)==1 and v.args[0].tag=="name":
                        view=self.env.get(v.args[0].val)
                        if view is None or view.ty.mode=="value": fail("E-CALL-SHAPE", "len requires a known view.",v)
                        ext=view.ty.extent
                    elif v.tag in {"name","int"}: ext=v.val
                    else: fail("E-CALL-SHAPE", "View extent must be a name, literal or len(view).",v)
                want=Type(t.name,t.mode,ext)
                if at.mode=="rw" and want.mode=="ro": want=Type(t.name,"rw",ext)
                self.expect(at,want,a)
                if a.tag!="name": fail("E-CALL-VIEW", "Only direct view parameters may be passed.",a)
                views.append((a.val,t.mode))
        for i,(a,m) in enumerate(views):
            for b,k in views[i+1:]:
                if a==b and (m=="rw" or k=="rw"):
                    fail("E-ALIAS", "A mutable view cannot be passed to overlapping call arguments.",e)
        assert self.f is not None
        self.call_edges[self.f.name].append((n,{formal:actual.val
            for (formal,t),actual in zip(f.params,args) if t.mode != "value"}))
        self.callset.add(n); e.cpp="cf_"+n+"("+", ".join(a.cpp for a in args)+")"
        return f.ret
