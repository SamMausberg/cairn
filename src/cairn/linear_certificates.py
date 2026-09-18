"""Exact linear-inequality certificates for the bounded collector.

A successful check establishes a nonnegative-combination identity over integers.
This small checker and the compiler-to-rule correspondence remain trusted Python,
not Lean-verified code. No solver, sampling, or native arithmetic is used here.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

# An affine form c + k*K + i*I + n*N + m*M, interpreted as form >= 0.
Form = tuple[int, int, int, int, int]
ZERO: Form = (0,0,0,0,0)

@dataclass(frozen=True)
class Rule:
    name: str
    assumptions: tuple[Form,...]
    conclusion: Form

@dataclass(frozen=True)
class Certificate:
    weights: tuple[int,...]
    nonnegative_constant: int = 0


def check(rule: Rule, certificate: Certificate) -> bool:
    """The trusted arithmetic checker: exact coefficients, never rounded numbers."""
    if type(rule) is not Rule or type(certificate) is not Certificate: return False
    if type(rule.assumptions) is not tuple: return False
    forms=(*rule.assumptions,rule.conclusion)
    if not 0 <= len(rule.assumptions) <= 64: return False
    if any(type(f) is not tuple or len(f)!=5 or
           any(type(x) is not int or x.bit_length()>4096 for x in f) for f in forms): return False
    if type(certificate.weights) is not tuple or len(certificate.weights)!=len(rule.assumptions): return False
    values=(*certificate.weights,certificate.nonnegative_constant)
    if any(type(x) is not int or x<0 or x.bit_length()>4096 for x in values): return False
    derived=[certificate.nonnegative_constant,0,0,0,0]
    for coefficient,assumption in zip(certificate.weights,rule.assumptions):
        for j,value in enumerate(assumption): derived[j]+=coefficient*value
    return tuple(derived)==rule.conclusion


def collector_rules() -> tuple[tuple[Rule,Certificate],...]:
    # M is the maximum representable cursor; invariant 0 <= K <= I <= N <= M.
    inv=((0,1,0,0,0),(0,-1,1,0,0),(0,0,-1,1,0),(0,0,0,-1,1))
    active=(*inv,(-1,0,-1,1,0)) # I < N expressed exactly for integer states.
    initial=((0,0,0,1,0),(0,0,0,-1,1)) # N >= 0, M >= N; I = K = 0 substituted.
    rules=[]
    def add(name,assumptions,goal,weights,constant=0):
        rules.append((Rule(name,tuple(assumptions),goal),Certificate(tuple(weights),constant)))
    add('initial.nonnegative',initial,ZERO,(0,0))
    add('initial.cursor_before_input',initial,ZERO,(0,0))
    add('initial.input_before_capacity',initial,(0,0,0,1,0),(1,0))
    add('initial.capacity_representable',initial,(0,0,0,-1,1),(0,1))
    add('store.nonnegative',active,inv[0],(1,0,0,0,0))
    add('store.strictly_below_capacity',active,(-1,-1,0,1,0),(0,1,0,0,1))
    add('emit.cursor_increment_fits',active,(-1,-1,0,0,1),(0,1,0,1,1))
    add('step.input_increment_fits',active,(-1,0,-1,0,1),(0,0,0,1,1))
    # Substitute K'=K+1,I'=I+1 for the emitting branch.
    for j,goal in enumerate(((1,1,0,0,0),inv[1],(-1,0,-1,1,0),inv[3])):
        weight=[0]*5;weight[0 if j==0 else 1 if j==1 else 4 if j==2 else 3]=1
        add('emit.invariant.'+str(j),active,goal,weight,1 if j==0 else 0)
    # Substitute K'=K,I'=I+1 for the non-emitting branch.
    for j,goal in enumerate((inv[0],(1,-1,1,0,0),(-1,0,-1,1,0),inv[3])):
        weight=[0]*5;weight[0 if j==0 else 1 if j==1 else 4 if j==2 else 3]=1
        add('skip.invariant.'+str(j),active,goal,weight,1 if j==1 else 0)
    add('exit.output_count_bounded',inv,(0,-1,0,1,0),(0,1,1,0))
    return tuple(rules)


def audit_collector() -> dict:
    rows=[]
    for rule,certificate in collector_rules():
        if not check(rule,certificate): raise ValueError('Invalid collector certificate: '+rule.name)
        rows.append({'name':rule.name,'assumptions':rule.assumptions,'conclusion':rule.conclusion,
                     'weights':certificate.weights,'nonnegative_constant':certificate.nonnegative_constant})
    serial=json.dumps(rows,sort_keys=True,separators=(',',':'))
    return {'status':'linear-certificates-checked','rule':'bounded-collector/2',
            'certificate_count':len(rows),'sha256':hashlib.sha256(serial.encode()).hexdigest(),
            'checker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'variables':['constant','emitted','visited','capacity','cursor_max'],
            'interpretation':'Each affine form is nonnegative over mathematical integers.',
            'obligations':rows,'trusted':['Python integer arithmetic','this checker','compiler/rule correspondence'],
            'lean_verified':False,'native_refinement_proved':False}
