#!/usr/bin/env python3
from pathlib import Path
import csv, hashlib, json, os, platform, re, statistics, subprocess
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
FLAGS=['-std=c++20','-O3','-march=x86-64-v3','-ffp-contract=off','-fno-fast-math','-fno-exceptions','-fno-rtti','-ffunction-sections','-Wall','-Wextra','-Werror']
commands=[]
def run(cmd,**kw):
    commands.append(cmd)
    return subprocess.run(cmd,check=True,text=True,capture_output=True,**kw)
for file,out in [('results/native.cpp','results/native.o'),('bench/reference.cpp','results/reference.o'),('bench/driver.cpp','results/driver.o')]:
    run(['clang++',*FLAGS,'-c',file,'-o',out])
run(['clang++','results/native.o','results/reference.o','results/driver.o','-o','results/benchmark'])
avail=sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0,{avail[0]})
raw=run(['results/benchmark']).stdout
(ROOT/'results/timing_raw.csv').write_text(raw)
rows=list(csv.DictReader(raw.splitlines())); groups={}
for row in rows: groups.setdefault((row['kernel'],int(row['n']),row['pattern']),[]).append(row)
summary=[]
for (name,n,pattern),rr in groups.items():
    aa=[float(x['cairn_ns']) for x in rr]; bb=[float(x['cpp_ns']) for x in rr]
    ratios=[b/a for a,b in zip(aa,bb)]
    summary.append({'kernel':name,'n':n,'pattern':pattern,'pairs':len(rr),'cairn_median_ns':statistics.median(aa),'cpp_median_ns':statistics.median(bb),
                    'speed_ratio_cpp_over_cairn':statistics.median(ratios),
                    'ratio_min':min(ratios),'ratio_max':max(ratios)})
(ROOT/'results/timing_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
# Compare section bytes AND relocation targets/types/offsets, not disassembly text alone.
equivalence=[]
for name in ['saxpy','dot','sum_wrap','prefix','count_gt','histogram','compact_even','lower_bound','gcd']:
    extracts=[]; rels=[]
    for prefix,obj in [('cf','native'),('cc','reference')]:
        out=f'results/{obj}_{name}.bin'; sec=f'.text.{prefix}_{name}'
        run(['objcopy',f'--dump-section={sec}={out}',f'results/{obj}.o'])
        extracts.append((ROOT/out).read_bytes())
        r=run(['objdump','-r','-j',sec,f'results/{obj}.o']).stdout
        rel=[]
        for line in r.splitlines():
            m=re.match(r'^([0-9a-f]+)\s+(R_\S+)\s+(\S+)',line)
            if m: rel.append(tuple(x.replace('cf_','FUNC_').replace('cc_','FUNC_') for x in m.groups()))
        rels.append(rel)
    equivalence.append({'kernel':name,'cairn_bytes':len(extracts[0]),'cpp_bytes':len(extracts[1]),
                        'bytes_equal':extracts[0]==extracts[1],'relocations_equal':rels[0]==rels[1],
                        'cairn_sha256':hashlib.sha256(extracts[0]).hexdigest(),'cpp_sha256':hashlib.sha256(extracts[1]).hexdigest(),
                        'relocations':rels})
(ROOT/'results/codegen_equivalence.json').write_text(json.dumps(equivalence,indent=2)+'\n')
(ROOT/'results/benchmark_environment.json').write_text(json.dumps({'compiler':run(['clang++','--version']).stdout,
  'flags':FLAGS,'pinned_cpu':avail[0],'available_cpus':avail,'platform':platform.platform(),
  'cpu':run(['lscpu']).stdout,'commands':commands,
  'limitations':'Shared virtualized CPU; no frequency or host-isolation control. 11 alternating paired rounds per case, no LTO. Two integer-input patterns: LCG low-bit alternating parity and high-bit-mixed parity. Same FFI entry checks; C++ inner operations rely on algorithm invariants. Ratios above one favor CAIRN. Not expert hand-tuned CPU/GPU baselines.'},indent=2)+'\n')
print(json.dumps(summary,indent=2))
print('identical_function_sections',sum(x['bytes_equal'] and x['relocations_equal'] for x in equivalence),'/',len(equivalence))
