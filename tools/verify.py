#!/usr/bin/env python3
"""Regenerate and test the native artifact. CPU-only, Linux x86-64-v3 profile.

No downloads. Benchmarks are optional and overwrite timing result files.
A failed child command stops immediately; a success log is never fabricated.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import os
import subprocess
import sys
R=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bench',action='store_true')
    ap.add_argument('--gcc',action='store_true')
    ap.add_argument('--sanitize',action='store_true')
    a=ap.parse_args(); os.chdir(R); (R/'results').mkdir(exist_ok=True)
    log=[]
    def run(command,output=None,env=None):
        cp=subprocess.run(command,text=True,capture_output=True,env=env)
        log.append({'command':command,'exit_code':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
        if output: (R/output).write_text(cp.stdout+(cp.stderr if cp.returncode else ''))
        (R/'results/verification_run.json').write_text(json.dumps(log,indent=2)+'\n')
        if cp.returncode: raise SystemExit(cp.stderr or cp.stdout or f'Failed: {command}')
        return cp.stdout
    py=sys.executable
    for stem in ['native','family','wire']:
        run([py,'tools/build.py',f'examples/{stem}.cairn','--out','results'])
    run(['clang++','-std=c++20','-O3','-march=x86-64-v3','-ffp-contract=off','-fno-fast-math',
         '-Wall','-Wextra','-Werror','-shared','-fPIC','bench/family_template.cpp','-o','results/libtemplate.so'])
    run([py,'-m','pytest','tests','-q'],'results/compiler_tests.txt')
    run([py,'tests/native_checks.py'],'results/native_tests.json')
    run([py,'tests/collector_wire_checks.py'],'results/collector_wire_tests.json')
    run([py,'tests/template_checks.py'],'results/template_tests.json')
    if a.gcc:
        for stem in ['native','family']:
            run(['g++','-std=c++20','-O3','-march=x86-64-v3','-ffp-contract=off','-fno-fast-math',
                 '-Wall','-Wextra','-Werror','-shared','-fPIC',f'results/{stem}.cpp','-o',f'results/lib{stem}_gcc.so'])
        env=dict(os.environ,CAIRN_FAMILY_LIB=str(R/'results/libfamily_gcc.so'))
        run([py,'tests/native_checks.py',str(R/'results/libnative_gcc.so')],'results/gcc_native_tests.json',env)
    if a.sanitize:
        run(['clang++','-std=c++20','-O1','-g','-ffp-contract=off','-fno-fast-math',
             '-fsanitize=address,undefined','-fno-omit-frame-pointer','tests/sanitize.cpp','-o','results/sanitize'])
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1',UBSAN_OPTIONS='halt_on_error=1')
        run(['results/sanitize'],'results/sanitizer_tests.txt',env)
    if a.bench:run([py,'bench/run.py'],'results/benchmark_run.txt')
    run([py,'tools/density.py'],'results/density_run.txt')
    print(json.dumps({'status':'all requested checks passed','commands':len(log),'formal_status':'not-verified'}))
if __name__=='__main__':main()
