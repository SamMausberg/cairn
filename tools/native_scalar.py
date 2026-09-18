#!/usr/bin/env python3
"""Test-only native return/trap observer, not the production runtime.

Checked traps become C++ exceptions and noexcept is removed ONLY in this
instrumented build. That makes thousands of edge cases observable in-process.
It does not verify actual process-abort behavior or establish native refinement.
"""
from __future__ import annotations
import ctypes
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'compiler'))
from cairnc import compile_source, RUNTIME
from scalar_semantics import prepared

CT={'bool':ctypes.c_bool,'u8':ctypes.c_uint8,'u16':ctypes.c_uint16,
    'u32':ctypes.c_uint32,'u64':ctypes.c_uint64,'usize':ctypes.c_uint64,
    'i32':ctypes.c_int32,'i64':ctypes.c_int64}

class NativeScalar:
    def __init__(self,source: str,cxx='clang++'):
        self.functions=prepared(source)
        self.temp=tempfile.TemporaryDirectory(prefix='cairn_scalar_test_')
        self.root=Path(self.temp.name)
        cpp,_=compile_source(source)
        assert RUNTIME.count('std::abort();')==1
        runtime=RUNTIME.replace(' noexcept','').replace('std::abort();','throw NativeTrap{};')
        runtime='struct NativeTrap {};\n'+runtime
        cpp=cpp.replace(' noexcept','')
        for name,f in self.functions.items():
            if f.ret.name not in CT or any(t.name not in CT or t.mode!='value' for _,t in f.params):
                raise ValueError('NativeScalar accepts scalar test fixtures only.')
            params=', '.join(t.cpp()+' '+n for n,t in f.params)
            args=', '.join(n for n,_ in f.params)
            cpp+=f'\nextern "C" bool observe_{name}({params}{", " if params else ""}{f.ret.cpp()}* result) {{\n'
            cpp+=f'  try {{ *result=cf_{name}({args}); return true; }} catch(NativeTrap&) {{ return false; }}\n}}\n'
        (self.root/'cairn_runtime.hpp').write_text(runtime)
        (self.root/'scalar.cpp').write_text(cpp)
        self.command=[cxx,'-std=c++20','-O2','-ffp-contract=off','-fno-fast-math',
                      '-Wall','-Wextra','-Werror','-shared','-fPIC',str(self.root/'scalar.cpp'),'-o',str(self.root/'scalar.so')]
        cp=subprocess.run(self.command,capture_output=True,text=True,timeout=45)
        if cp.returncode:raise RuntimeError(cp.stderr)
        self.compiler=subprocess.run([cxx,'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
        self.generated_sha256=hashlib.sha256(cpp.encode()).hexdigest()
        self.runtime_sha256=hashlib.sha256(runtime.encode()).hexdigest()
        self.lib=ctypes.CDLL(str(self.root/'scalar.so'))
        for name,f in self.functions.items():
            fn=getattr(self.lib,'observe_'+name)
            fn.argtypes=[CT[t.name] for _,t in f.params]+[ctypes.POINTER(CT[f.ret.name])]
            fn.restype=ctypes.c_bool
    def outcome(self,name,args):
        f=self.functions[name];value=CT[f.ret.name]()
        ok=getattr(self.lib,'observe_'+name)(*[args[n] for n,_ in f.params],ctypes.byref(value))
        return {'defined':True,'return':value.value} if ok else {'defined':False,'trap':'instrumented-native-trap'}
    def close(self):self.temp.cleanup()
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()
