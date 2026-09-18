#!/usr/bin/env python3
"""Compile and run bounded, host-owned example contracts in a child process.

Finite tests are not proofs. The process uses resource limits, NOT a security
sandbox. Only trusted local contracts are supported; use OS isolation for
hostile code and compiler toolchains. No model-supplied commands are executed.
"""
from __future__ import annotations
import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from .cairnc import compile_source, Parser, RUNTIME, Diagnostic
from .agent_tools import digest, stable_json, load_json_strict, explain

CTYPES={'bool':C.c_bool,'u8':C.c_uint8,'u16':C.c_uint16,'u32':C.c_uint32,'u64':C.c_uint64,
        'usize':C.c_size_t,'i32':C.c_int32,'i64':C.c_int64,'f32':C.c_float,'f64':C.c_double}
LIMITS={'bool':(0,1),'u8':(0,255),'u16':(0,65535),'u32':(0,2**32-1),
        'u64':(0,2**64-1),'usize':(0,2**64-1),'i32':(-2**31,2**31-1),'i64':(-2**63,2**63-1)}
FLAGS=['-std=c++20','-O3','-march=x86-64','-ffp-contract=off','-fno-fast-math',
       '-fno-exceptions','-fno-rtti','-Wall','-Wextra','-Werror',
       '-Wno-unused-parameter','-Wno-unused-variable','-Wno-unused-but-set-variable','-shared','-fPIC']

def scalar(name,value):
    if name not in CTYPES:raise ValueError('Task runner supports scalar/view signatures only.')
    if name=='bool':
        if type(value) is not bool:raise ValueError('Boolean argument must be JSON boolean.')
    elif name in LIMITS:
        lo,hi=LIMITS[name]
        if type(value) is not int or not lo<=value<=hi:raise ValueError('Integer outside declared range.')
    elif type(value) not in {int,float} or not math.isfinite(value) or not math.isfinite(CTYPES[name](value).value):
        raise ValueError('Floating argument must fit its finite storage format.')
    return CTYPES[name](value).value

def validate_contract(source,contract):
    if not isinstance(contract,dict) or contract.get('schema')!='cairn.task/1':raise ValueError('Unsupported task schema.')
    fs={f.name:f for f in Parser(source).parse().functions}
    name=contract.get('symbol')
    if name not in fs or fs[name].static:raise ValueError('Contract needs an ordinary authored symbol.')
    f=fs[name]
    if f.ret.name not in CTYPES and f.ret.name!='void':raise ValueError('Task runner does not support record returns.')
    cases=contract.get('cases')
    if not isinstance(cases,list) or not 1<=len(cases)<=10000:raise ValueError('Need 1..10000 trusted cases.')
    for case in cases:
        if not isinstance(case,dict) or set(case)-{'args','return','after'}:raise ValueError('Invalid case keys.')
        args=case.get('args')
        if not isinstance(args,dict) or set(args)!={n for n,_ in f.params}:raise ValueError('Argument names do not match signature.')
        for n,t in f.params:
            if t.mode=='value':scalar(t.name,args[n])
            else:
                size=int(t.extent) if t.extent.isdigit() else args[t.extent]
                if type(size) is not int or not 0<=size<=100000:raise ValueError('View size exceeds runner limit.')
                if not isinstance(args[n],list) or len(args[n])!=size:raise ValueError('View size mismatch.')
                for value in args[n]:scalar(t.name,value)
        if f.ret.name=='void':
            if case.get('return') is not None:raise ValueError('Void return must be null or absent.')
        else:
            if 'return' not in case:raise ValueError('Every scalar return needs an expected value.')
            scalar(f.ret.name,case['return'])
        after=case.get('after',{})
        writable={n:t for n,t in f.params if t.mode=='rw'}
        if set(after)!=set(writable):raise ValueError('Every mutable output needs a full expected post-state.')
        for n,t in writable.items():
            if not isinstance(after[n],list) or len(after[n])!=len(args[n]):raise ValueError('Post-state size mismatch.')
            for value in after[n]:scalar(t.name,value)
    return f

def diagnostic_value(value):
    # JSON has no NaN/Infinity values. Preserve the failure without mistaking
    # diagnostic serialization for a native crash.
    if isinstance(value,float) and not math.isfinite(value):
        return {'nonfinite': 'nan' if math.isnan(value) else ('+infinity' if value>0 else '-infinity')}
    if isinstance(value,list):return [diagnostic_value(x) for x in value]
    if isinstance(value,dict):return {k:diagnostic_value(v) for k,v in value.items()}
    return value

def child(library,source,contract):
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_CPU,(3,3))
    resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
    f=validate_contract(source,contract)
    lib=C.CDLL(str(library)); native=getattr(lib,'cf_'+f.name)
    native.argtypes=[CTYPES[t.name] if t.mode=='value' else C.POINTER(CTYPES[t.name]) for _,t in f.params]
    native.restype=None if f.ret.name=='void' else CTYPES[f.ret.name]
    for i,case in enumerate(contract['cases']):
        print(json.dumps({'stage':'case','index':i}),flush=True)
        args=[]; storage={}
        for n,t in f.params:
            value=case['args'][n]
            if t.mode=='value':args.append(CTYPES[t.name](value))
            else:
                array=(CTYPES[t.name]*len(value))(*value);storage[n]=array;args.append(array)
        actual=native(*args)
        expected=None if f.ret.name=='void' else scalar(f.ret.name,case['return'])
        after={n:list(storage[n]) for n,t in f.params if t.mode=='rw'}
        expected_after={n:[scalar(t.name,v) for v in case['after'][n]] for n,t in f.params if t.mode=='rw'}
        # Strict scalar equality; tasks use finite integer values in this release.
        if actual!=expected or after!=expected_after:
            print(json.dumps(diagnostic_value({'status':'failed-tests','case':i,'args':case['args'],
                'expected_return':expected,'actual_return':actual,
                'expected_after':expected_after,'actual_after':after}),allow_nan=False),flush=True)
            return 1
    print(json.dumps({'status':'passed-finite-tests','cases':len(contract['cases'])}),flush=True)
    return 0

def evaluate(source: str, contract: dict, cxx='clang++') -> dict:
    start=time.monotonic()
    common={'source_sha256':digest(source),'contract_sha256':digest(stable_json(contract)),
            'formal_status':'not-verified','security_sandbox':False}
    try:
        generated,receipt=compile_source(source);validate_contract(source,contract)
    except Diagnostic as e:return {**common,**explain(e,source),'stage':'frontend'}
    except (ValueError,TypeError,KeyError,OverflowError) as e:return {**common,'status':'invalid-contract','message':str(e)}
    compiler=shutil.which(cxx)
    if not compiler:return {**common,'status':'unknown','stage':'build','message':'Compiler unavailable.'}
    with tempfile.TemporaryDirectory(prefix='cairn-task-') as tmp:
        t=Path(tmp)
        (t/'candidate.cpp').write_text(generated);(t/'cairn_runtime.hpp').write_text(RUNTIME)
        (t/'source.cairn').write_text(source);(t/'contract.json').write_text(stable_json(contract))
        command=[compiler,*FLAGS,str(t/'candidate.cpp'),'-o',str(t/'libtask.so')]
        try:
            cp=subprocess.run(command,text=True,capture_output=True,timeout=30)
            build={'exit_code':cp.returncode,'flags':FLAGS,'compiler':compiler}
            if cp.returncode:return {**common,'status':'native-build-failed','build':build,'stderr':cp.stderr[:8000]}
            cmd=[sys.executable,'-m','cairn.testing','--child',str(t/'libtask.so'),str(t/'source.cairn'),str(t/'contract.json')]
            cp=subprocess.run(cmd,text=True,capture_output=True,timeout=8,env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])})
            lines=[]
            for line in cp.stdout.splitlines():
                try:lines.append(json.loads(line))
                except json.JSONDecodeError:pass
            verdict=next((x for x in reversed(lines) if 'status' in x),None)
            if verdict is None:
                verdict={'status':'native-trap-or-crash','exit_code':cp.returncode,
                         'last_case_started':lines[-1].get('index') if lines else None,'stderr':cp.stderr[:2000]}
            return {**common,**verdict,'build':build,'elapsed_seconds':time.monotonic()-start}
        except subprocess.TimeoutExpired as e:
            return {**common,'status':'unknown','stage':'timeout','message':'The bounded build or execution did not complete.'}

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--child':
        _,_,lib,src,contract=sys.argv
        return child(Path(lib),Path(src).read_text(),load_json_strict(Path(contract).read_text()))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--contract',type=Path,required=True);p.add_argument('--cxx',default='clang++');a=p.parse_args()
    result=evaluate(a.source.read_text(),load_json_strict(a.contract.read_text()),a.cxx)
    print(json.dumps(result,indent=2,allow_nan=False));return 0 if result['status']=='passed-finite-tests' else 1
if __name__=='__main__':sys.exit(main())
