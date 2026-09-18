"""Closed, bounded AST generators. No source evaluation or external inputs."""
from __future__ import annotations
import copy
from .syntax import *

def derive_wire(p: Program) -> Program:
    """Closed AST-to-AST generator; field names cannot inject generated source."""
    names = {f.name for f in p.functions} | set(p.records) | set(p.enums) | set(p.sums)
    def var(n): return Expr("name",n)
    def lit(n): return Expr("int",str(n))
    def call(n,*args): return Expr("call",n,list(args))
    for record in p.derivations:
        if record not in p.records:
            fail("E-DERIVE-TYPE", f"Unknown record {record}.")
        fields=p.records[record]
        if any(t.mode != "value" or t.name not in {"u8","u16","u32","u64"} for _,t in fields):
            fail("E-DERIVE-FIELD", "wire/1 supports only fixed-width unsigned scalar fields.")
        size=sum(WIDTH[t.name]//8 for _,t in fields)
        enc=[]; decoded=[]; offset=0
        for field,t in fields:
            value=Expr("field",field,[var("value")])
            parts=[]
            for byte in range(WIDTH[t.name]//8):
                bits=call("shr",copy.deepcopy(value),lit(8*byte))
                low=Expr("binary","&",[bits,lit(255)])
                enc.append(Stmt("assign",exprs=[Expr("index",args=[var("out"),lit(offset)]),call("u8",low)]))
                b=call(t.name,Expr("index",args=[var("input"),lit(offset)]))
                parts.append(call("shl_wrap",b,lit(8*byte)))
                offset+=1
            full=parts[0]
            for part in parts[1:]: full=Expr("binary","|",[full,part])
            decoded.append(full)
        functions=[
            Function("encode_"+record,[("out",Type("u8","rw",str(size))),("value",Type(record))],Type("void"),enc,source_name="derive wire for "+record),
            Function("decode_"+record,[("input",Type("u8","ro",str(size)))],Type(record),[Stmt("return",exprs=[call(record,*decoded)])],source_name="derive wire for "+record),
            Function("wire_size_"+record,[],Type("usize"),[Stmt("return",exprs=[lit(size)])],source_name="derive wire for "+record)
        ]
        for f in functions:
            if f.name in names: fail("E-DERIVE-COLLISION", f"Derived name {f.name} already exists.")
            names.add(f.name); p.functions.append(f)
    return p

def specialize(p: Program) -> Program:
    base = {f.name:f for f in p.functions if f.static}
    out = [f for f in p.functions if not f.static]
    names = {f.name for f in out} | set(p.records) | set(p.enums) | set(p.sums)
    def node_count(f):
        total = 0; todo = list(f.body)
        while todo:
            node = todo.pop(); total += 1
            if isinstance(node, Stmt): todo.extend(node.body + node.other + node.exprs + node.arms)
            elif isinstance(node, Arm): todo.extend(node.body)
            elif isinstance(node, Expr): todo.extend(node.args)
        return total
    estimated = sum(node_count(f) for f in out)
    if estimated > MAX_NODES or len(out) > MAX_FUNCTIONS:
        fail("E-EXPANSION-LIMIT", "Program exceeds the pre-expansion budget.")
    for prefix, name, lo, hi in p.families:
        if name not in base: fail("E-FAMILY-TARGET", f"{name} is not a static function template.")
        if not 0 <= lo < hi <= 2**32 or hi-lo > MAX_FAMILY:
            fail("E-FAMILY-LIMIT", "Family must be a nonempty half-open range, at most 1024 variants, below 2^32.")
        cost = (hi-lo)*node_count(base[name])
        if estimated + cost > MAX_NODES or len(out) + hi-lo > MAX_FUNCTIONS:
            fail("E-EXPANSION-LIMIT", "Family exceeds the remaining AST/function budget.")
        estimated += cost
        for k in range(lo,hi):
            f = copy.deepcopy(base[name]); f.name = f"{prefix}_{k}"; f.binding = k
            if f.name in names: fail("E-DUPLICATE", f"Family emits duplicate name {f.name}.")
            names.add(f.name); out.append(f)
    for n in base:
        if not any(name == n for _,name,_,_ in p.families):
            fail("E-UNINSTANTIATED", f"Static function {n} has no family; unused templates are not silently ignored.")
    if len(out) > MAX_FUNCTIONS: fail("E-EXPANSION-LIMIT", "Expanded program exceeds 2048 functions.")
    p.functions = out
    return p
