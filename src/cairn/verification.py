"""Whole-declaration coverage for restricted scalar equivalence.

A module receipt must not inherit a selected function's successful result. This
checks every entry and fails closed for missing, extra, unsupported or unknown
entries. It proves neither the reference's intent nor the C++ backend.
"""
from __future__ import annotations
import hashlib
import time
from .syntax import Parser
from pathlib import Path
from .cairnc import compile_source,Diagnostic
from .scalar_semantics import equivalent


def verify_module(reference: str, candidate: str, timeout_ms: int=3000) -> dict:
    result={'protocol':'cairn.verification-coverage/1','status':'incomplete',
            'reference_sha256':hashlib.sha256(reference.encode()).hexdigest(),
            'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
            'domain':'all declared-width scalar inputs; reference must be total',
            'scope':'all declared functions in the restricted scalar source model',
            'native_proof':False,'lean_proof':False,'results':{},
            'coverage_checker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if type(timeout_ms) is not int or not 1<=timeout_ms<=30000:
        return {**result,'reason':'Per-query timeout must be 1..30000 milliseconds.'}
    if len(reference.encode())>64000 or len(candidate.encode())>64000:
        return {**result,'reason':'Whole-module scalar source limit is 64000 bytes.'}
    try:
        _,ref=compile_source(reference);_,cand=compile_source(candidate)
    except Diagnostic as e:
        return {**result,'status':'rejected','diagnostic':e.data}
    a=Parser(reference).parse(); b=Parser(candidate).parse()
    result['public_types_match']=(a.records,a.enums,a.sums)==(b.records,b.enums,b.sums)
    result['reference_intent_proved']=False
    names=set(ref['functions']); present=set(cand['functions'])
    result.update(expected=sorted(names),missing=sorted(names-present),extra=sorted(present-names))
    if not names or len(names|present)>128:
        return {**result,'reason':'Coverage requires 1..128 declared functions.'}
    deadline=time.monotonic()+30
    for name in sorted(names & present):
        remaining=int((deadline-time.monotonic())*1000)
        if remaining < 4:
            result['results'][name]={'status':'unknown','reason':'Whole-module solver budget exhausted.'}
        else:
            # A scalar comparison has several obligations; reserve its share for all.
            allowance=min(timeout_ms,max(1,remaining//4))
            result['results'][name]=equivalent(reference,candidate,name,timeout_ms=allowance)
    result['covered']=[name for name,r in result['results'].items() if r['status']=='smt-equivalent']
    result['uncovered']=sorted((names|present)-set(result['covered']))
    if result['public_types_match'] and names==present and len(result['covered'])==len(names):
        result['status']='smt-module-equivalent'
    return result
