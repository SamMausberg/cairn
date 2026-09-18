#!/usr/bin/env python3
"""Independent Python oracles against the actual compiled shared libraries."""
from __future__ import annotations
from collections import Counter
import ctypes as C
import bisect
import json
import math
import os
from pathlib import Path
import random
import resource
import signal
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
U64=C.c_uint64; U32=C.c_uint32; U8=C.c_uint8; SZ=C.c_size_t
F32=C.c_float; F64=C.c_double; I64=C.c_int64
MASK=(1<<64)-1
class Vec3(C.Structure): _fields_=[('x',F64),('y',F64),('z',F64)]
class Header(C.Structure): _fields_=[('version',U32),('flags',U32),('length',U64)]
SIGS={
'saxpy':([SZ,C.POINTER(F32),C.POINTER(F32),C.POINTER(F32),F32],None),
'dot':([SZ,C.POINTER(F64),C.POINTER(F64)],F64),
'sum_wrap':([SZ,C.POINTER(U64)],U64),
'prefix':([SZ,C.POINTER(U64),C.POINTER(U64)],None),
'count_gt':([SZ,C.POINTER(F32),F32],SZ),
'histogram':([SZ,C.POINTER(U64),C.POINTER(U8)],None),
'compact_even':([SZ,C.POINTER(U64),C.POINTER(U64)],SZ),
'lower_bound':([SZ,C.POINTER(U64),U64],SZ),
'gcd':([U64,U64],U64),
'factorial_wrap':([U64],U64),
'distance_sq':([Vec3,Vec3],F64),
'header_valid':([Header,U64],C.c_bool),
'op_class':([U32],U32),
'decode_le32':([SZ,C.POINTER(U8),SZ],U32),
'copy_twice':([SZ,C.POINTER(F32),C.POINTER(F32)],None),
'delegated':([SZ,C.POINTER(F32),C.POINTER(F32)],None),
'checked_add':([U64,U64],U64),
'checked_div':([I64,I64],I64),
'checked_at':([SZ,C.POINTER(U64),SZ],U64),
'checked_shift':([U32,SZ],U32),
'narrow':([U64],U8),
'square_sum':([SZ,C.POINTER(F64)],F64),
}

def load(path=None):
    path = path or Path(os.environ.get('CAIRN_NATIVE_LIB', str(ROOT/'results/libnative.so')))
    lib=C.CDLL(str(path))
    for name,(args,ret) in SIGS.items():
        f=getattr(lib,'cf_'+name); f.argtypes=args; f.restype=ret
    lib.cf_translated.argtypes=[Vec3,Vec3]; lib.cf_translated.restype=Vec3
    return lib

def array(typ,x): return (typ*len(x))(*x)
def f32(x): return F32(x).value
def bits(x): return struct.pack('d',x)

def trap_case(name):
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    l=load(); a=array(U64,[1,2]); b=array(F32,[1,2,3]); u=array(U8,[1,2,3,4])
    cases={
      'overflow':lambda:l.cf_checked_add(MASK,1),
      'division_zero':lambda:l.cf_checked_div(1,0),
      'division_overflow':lambda:l.cf_checked_div(-(1<<63),-1),
      'bounds':lambda:l.cf_checked_at(2,a,2),
      'null':lambda:l.cf_checked_at(1,None,0),
      'alias':lambda:l.cf_copy_twice(2,b,b),
      'partial_alias':lambda:l.cf_copy_twice(2,b,C.cast(C.byref(b,C.sizeof(F32)),C.POINTER(F32))),
      'unaligned':lambda:l.cf_checked_at(1,C.cast(C.byref(a,1),C.POINTER(U64)),0),
      'extent_overflow':lambda:l.cf_checked_at(MASK,a,0),
      'narrow':lambda:l.cf_narrow(256),
      'shift':lambda:l.cf_checked_shift(1,32),
      'enum':lambda:l.cf_op_class(3),
      'decode_bounds':lambda:l.cf_decode_le32(4,u,1),
      'decode_offset_overflow':lambda:l.cf_decode_le32(4,u,MASK),
    }
    cases[name]()
    raise AssertionError('Expected abort did not occur')

def run(libpath=None):
    l=load(libpath); r=random.Random(20260917)
    count=Counter()
    lengths=[0,1,2,3,7,15,16,17,31,32,33,63,64,65,255,256,257,1024]
    for case in range(180):
        n=lengths[case%len(lengths)] if case<90 else r.randrange(0,513)
        x=[r.randrange(-256,257)/8 for _ in range(n)]
        y=[r.randrange(-256,257)/8 for _ in range(n)]
        u=[r.getrandbits(64) for _ in range(n)]
        a=r.randrange(-32,33)/8
        xf=array(F32,x); yf=array(F32,y); of=array(F32,[0]*n)
        xd=array(F64,x); yd=array(F64,y)
        ux=array(U64,u); ou=array(U64,[0]*n)
        l.cf_saxpy(n,of,xf,yf,a)
        assert list(of)==[f32(f32(a*q)+v) for q,v in zip(x,y)]; count['saxpy']+=1
        expected=0.0
        for q,v in zip(x,y): expected=expected+q*v
        assert bits(l.cf_dot(n,xd,yd))==bits(expected); count['dot']+=1
        assert l.cf_sum_wrap(n,ux)==sum(u)&MASK; count['sum_wrap']+=1
        expected=[]; running=0
        for q in u: running=(running+q)&MASK; expected.append(running)
        l.cf_prefix(n,ou,ux); assert list(ou)==expected; count['prefix']+=1
        assert l.cf_count_gt(n,xf,a)==sum(q>a for q in x); count['count_gt']+=1
        hist_in=[r.randrange(256) for _ in range(n)]
        hi=array(U8,hist_in); ho=array(U64,[99]*256)
        l.cf_histogram(n,ho,hi); exp=[0]*256
        for q in hist_in: exp[q]+=1
        assert list(ho)==exp; count['histogram']+=1
        got=l.cf_compact_even(n,ou,ux); exp=[q for q in u if q%2==0]
        assert got==len(exp) and list(ou)[:got]==exp; count['compact_even']+=1
        ordered=sorted(u); key=r.getrandbits(64); so=array(U64,ordered)
        assert l.cf_lower_bound(n,so,key)==bisect.bisect_left(ordered,key); count['lower_bound']+=1
        l.cf_copy_twice(n,of,xf); assert list(of)==[f32(2*q) for q in x]; count['copy_twice']+=1
        l.cf_delegated(n,of,xf); assert list(of)==[f32(2*q) for q in x]; count['delegated']+=1
        sq=0.0
        for q in x: sq=sq+q*q
        assert bits(l.cf_square_sum(n,xd))==bits(sq); count['square_sum']+=1
        if n:
            ix=r.randrange(n); assert l.cf_checked_at(n,ux,ix)==u[ix]; count['checked_at']+=1
        av=r.getrandbits(64); bv=r.getrandbits(64)
        assert l.cf_gcd(av,bv)==math.gcd(av,bv); count['gcd']+=1
        f=r.randrange(0,100)
        assert l.cf_factorial_wrap(f)==math.factorial(f)&MASK; count['factorial_wrap']+=1
        aa=[r.randrange(-100,100)/4 for _ in range(3)]; bb=[r.randrange(-100,100)/4 for _ in range(3)]
        d=[a-b for a,b in zip(aa,bb)]; val=d[0]*d[0]+d[1]*d[1]+d[2]*d[2]
        assert bits(l.cf_distance_sq(Vec3(*aa),Vec3(*bb)))==bits(val); count['distance_sq']+=1
        tr=l.cf_translated(Vec3(*aa),Vec3(*bb)); assert (tr.x,tr.y,tr.z)==tuple(a+b for a,b in zip(aa,bb)); count['translated']+=1
        h=Header(r.randrange(3),r.randrange(8),r.randrange(128)); avail=r.randrange(128)
        assert l.cf_header_valid(h,avail)==(h.version==1 and h.length<=avail and h.flags&0xfffffffc==0); count['header_valid']+=1
        op=r.randrange(3); assert l.cf_op_class(op)==(op+1)*10; count['op_class']+=1
        payload=bytes(r.randrange(256) for _ in range(32)); pa=array(U8,payload); off=r.randrange(29)
        assert l.cf_decode_le32(32,pa,off)==int.from_bytes(payload[off:off+4],'little'); count['decode_le32']+=1
        av=r.randrange(1<<60); bv=r.randrange(1<<60)
        assert l.cf_checked_add(av,bv)==av+bv; count['checked_add']+=1
        av=r.randrange(-(1<<60),1<<60); bv=r.choice([-1,1])*r.randrange(1,1<<30)
        exp=abs(av)//abs(bv); exp= -exp if (av<0)!=(bv<0) else exp
        assert l.cf_checked_div(av,bv)==exp; count['checked_div']+=1
        av=r.randrange(1<<32); shift=r.randrange(32)
        assert l.cf_checked_shift(av,shift)==(av<<shift)&0xffffffff; count['checked_shift']+=1
        av=r.randrange(256); assert l.cf_narrow(av)==av; count['narrow']+=1
    # Empty views may be null. Boundary checker must not dereference them.
    l.cf_saxpy(0,None,None,None,1); l.cf_prefix(0,None,None)
    assert l.cf_sum_wrap(0,None)==0
    count['empty_null_views']+=3
    family=C.CDLL(os.environ.get('CAIRN_FAMILY_LIB',str(ROOT/'results/libfamily.so')))
    for k in range(1,257):
        fn=getattr(family,'cf_gain_'+str(k)); fn.argtypes=[SZ,C.POINTER(F32),C.POINTER(F32)]; fn.restype=None
        for n in [0,1,7,31,257]:
            vals=[r.randrange(-256,257)/8 for _ in range(n)]; a=array(F32,vals); b=array(F32,[0]*n)
            fn(n,b,a); assert list(b)==[f32(v*k) for v in vals]; count['family_specialization']+=1
    traps=['overflow','division_zero','division_overflow','bounds','null','alias','partial_alias','unaligned','extent_overflow','narrow','shift','enum','decode_bounds','decode_offset_overflow']
    for name in traps:
        env=dict(os.environ)
        if libpath: env['CAIRN_NATIVE_LIB']=str(libpath.resolve())
        proc=subprocess.run([sys.executable,__file__,'--trap',name],capture_output=True,env=env)
        assert proc.returncode==-signal.SIGABRT,(name,proc.returncode,proc.stderr)
    return {'seed':20260917,'function_cases':dict(count),'total_function_cases':sum(count.values()),
            'expected_abort_cases':len(traps),'all_passed':True,
            'interpretation':'Finite native tests, not a safety or compiler-correctness proof.'}

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--trap': trap_case(sys.argv[2])
    else:
        result=run(Path(sys.argv[1]) if len(sys.argv)>1 else None)
        print(json.dumps(result,indent=2))
