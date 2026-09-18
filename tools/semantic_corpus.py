#!/usr/bin/env python3
"""Construct same-contract scalar teaching pairs, then check every label.

All positive labels require a decided all-width SMT obligation. Every negative
must typecheck, have a distinguishing solver input, and replay in the separate
Python interpreter and BOTH instrumented native backends. No model is trained.
"""
from __future__ import annotations
import hashlib,json,random,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'src'),str(R/'tools')]
from cairn.scalar_semantics import equivalent,Concrete,prepared,outcome_key
from native_scalar import NativeScalar

def cases():
    rows=[]
    def add(family,ty,params,ret,reference,chosen,rejected,meaning):
        signature='fn task('+params+')->'+ret
        rows.append({'id':family+'_'+ty,'family':family,'width':ty,'symbol':'task','assume':'true',
                     'allow_reference_traps':False,'task':meaning,
                     'reference':signature+' {'+reference+'}',
                     'chosen':signature+' {'+chosen+'}',
                     'rejected':signature+' {'+rejected+'}',
                     'split':'evaluation' if family in {'floor_average','saturating_add','align_eight','ceiling_div_eight'} else 'training'})
    for ty in ['u8','u32','u64']:
        m=(1<<int(ty[1:]))-1;p=f'x:{ty},y:{ty}';un=f'x:{ty}'
        add('modular_increment',ty,un,ty,f'if x=={m} {{return 0;}} return x+1;','return add_wrap(x,1);','return x+1;',f'Return (x+1) modulo 2^{ty[1:]}; do not trap.')
        add('minimum',ty,p,ty,'if x<y {return x;} return y;','return min(x,y);','return max(x,y);','Return the smaller input; do not trap.')
        add('maximum',ty,p,ty,'if x>y {return x;} return y;','return max(x,y);','return min(x,y);','Return the larger input; do not trap.')
        add('absolute_difference',ty,p,ty,'if x>=y {return x-y;} return y-x;','return max(x,y)-min(x,y);','return x-y;','Return the nonnegative absolute difference; do not trap.')
        add('saturating_add',ty,p,ty,f'if y>{m}-x {{return {m};}} return x+y;',f'let z=add_wrap(x,y); if z<x {{return {m};}} return z;','return add_wrap(x,y);',f'Return min(x+y,{m}) over mathematical integers; do not trap.')
        add('floor_average',ty,p,ty,'return (x/2)+(y/2)+((x%2+y%2)/2);','return (x&y)+shr(x^y,1);','return add_wrap(x,y)/2;','Return floor((x+y)/2) over mathematical integers for every input, without overflow or traps.')
        add('align_eight',ty,un,ty,'return (x/8)*8;',f'return x & ~{ty}(7);','return x/8;','Round x down to the greatest multiple of eight not exceeding x; do not trap.')
        add('clear_lowest_bit',ty,un,ty,'if x==0 {return 0;} return x&(x-1);','return x&sub_wrap(x,1);','return x^sub_wrap(x,1);','Clear the least significant set bit; zero stays zero; do not trap.')
        add('power_of_two',ty,un,'bool','return x!=0 && (x&sub_wrap(x,1))==0;','if x==0 {return false;} return (x&sub_wrap(x,1))==0;','return (x&sub_wrap(x,1))==0;','Return whether x is a positive power of two. Zero is false; do not trap.')
        add('ceiling_div_eight',ty,un,ty,'let base=x/8; if x%8!=0 {return base+1;} return base;','return (x/8)+min(x%8,1);','return add_wrap(x,7)/8;','Return ceil(x/8) over mathematical integers; do not trap.')
        add('low_nibble',ty,un,ty,'return x%16;','return x&15;','return x%15;','Return the low four bits of x; do not trap.')
        add('zero_safe_division',ty,p,ty,'if y==0 {return 0;} return x/y;','if y!=0 {return x/y;} return 0;','return x/y;','Return zero when y is zero, otherwise floor(x/y); do not trap.')
    for ty in ['i32','i64']:
        add('signed_signum',ty,f'x:{ty}',ty,'if x<0 {return -1;} if x>0 {return 1;} return 0;',f'let mut z:{ty}=0; if x!=0 {{z=1; if x<0 {{z=-1;}}}} return z;','if x<=0 {return -1;} return 1;','Return -1 for negative, 0 for zero, 1 for positive; do not trap.')
        add('signed_identity',ty,f'x:{ty}',ty,'return x;','return x+0;','return -(-x);','Return x unchanged for every signed input, including the minimum; do not trap.')
    return rows

def main():
    root=R/'training/semantic';root.mkdir(exist_ok=True);queries=root/'obligations';queries.mkdir(exist_ok=True)
    rows=cases();source=[]
    for row in rows:
        for variant in ['reference','chosen','rejected']:
            source.append(row[variant].replace('fn task(',f'fn {row["id"]}_{variant}('))
        row['contract_sha256']=hashlib.sha256(json.dumps({k:row[k] for k in ['task','reference','assume','allow_reference_traps']},sort_keys=True).encode()).hexdigest()
        for variant in ['chosen','rejected']:
            log=[];r=equivalent(row['reference'],row[variant],'task',query_log=log,timeout_ms=10000)
            expected='smt-equivalent' if variant=='chosen' else 'counterexample'
            if r['status']!=expected:raise AssertionError((row['id'],variant,r))
            row[variant+'_receipt']=r
            for i,q in enumerate(log):(queries/f'{row["id"]}_{variant}_{i}_{q["stage"]}.smt2').write_text(q['smt2'])
    full='\n\n'.join(source);(root/'audit_native.cairn').write_text(full)
    native=[];replay_count=0;positive_cases=0
    rng=random.Random(1709202604)
    all_fns=prepared(full);concrete=Concrete(all_fns)
    for compiler in ['clang++','g++']:
        with NativeScalar(full,compiler) as lib:
            for row in rows:
                args=row['rejected_receipt']['counterexample'];out={}
                for variant in ['reference','chosen','rejected']:
                    n=row['id']+'_'+variant;out[variant]=lib.outcome(n,args)
                    assert outcome_key(out[variant])==outcome_key(concrete.outcome(n,args)),(row['id'],variant,args)
                    replay_count+=1
                assert outcome_key(out['reference'])==outcome_key(out['chosen'])
                assert outcome_key(out['reference'])!=outcome_key(out['rejected'])
                row.setdefault('native_counterexample_replays',[]).append({'compiler':compiler,'outcomes':out})
                # Additional finite native sanity checks. Universal label still comes from SMT.
                params=all_fns[row['id']+'_reference'].params
                width=int(row['width'][1:]);signed=row['width'][0]=='i'
                lo=-(1<<(width-1)) if signed else 0;hi=(1<<(width-1))-1 if signed else (1<<width)-1
                for _ in range(32):
                    a={n:rng.randint(lo,hi) for n,_ in params}
                    expected=concrete.outcome(row['id']+'_reference',a)
                    for variant in ['reference','chosen']:
                        actual=lib.outcome(row['id']+'_'+variant,a)
                        assert outcome_key(expected)==outcome_key(actual)
                        positive_cases+=1
            native.append({'compiler':lib.compiler,'instrumented_runtime_sha256':lib.runtime_sha256,'generated_sha256':lib.generated_sha256})
    # Auditable answers are shipped; evaluation is NOT a secret held-out benchmark.
    (root/'audit.json').write_text(json.dumps(rows,indent=2)+'\n')
    training=[];heldout=[]
    for row in rows:
        if row['split']=='training':
            training.append({'id':row['id'],'family':row['family'],'contract_sha256':row['contract_sha256'],
                'prompt':row['task']+'\nFixed reference:\n'+row['reference'],
                'chosen':row['chosen'],'rejected':row['rejected'],
                'feedback':{k:row['rejected_receipt'][k] for k in ['counterexample','expected','actual']},
                'label':'same-contract-smt-equivalence-and-native-replayed-inequivalence',
                'trust':'translator-and-Z3-trusted; no Lean proof; no model training'})
        else:heldout.append({'id':row['id'],'family':row['family'],'task':row['task'],'reference':row['reference'],
                            'contract_sha256':row['contract_sha256']})
    for name,data in [('train_preference.jsonl',training),('evaluation_prompts.jsonl',heldout)]:
        (root/name).write_text(''.join(json.dumps(r)+'\n' for r in data))
    summary={'status':'passed','tasks':len(rows),'algorithm_families':len(set(r['family'] for r in rows)),
             'training_pairs':len(training),'evaluation_prompts':len(heldout),'positive_smt_labels':len(rows),
             'negative_smt_counterexamples':len(rows),'native_counterexample_outcomes':replay_count,
             'additional_native_positive_outcomes':positive_cases,'native':native,
             'same_signature_same_domain':True,'fine_tuning_performed':False,'fresh_model_evaluation':False,
             'evaluation_secrecy':'None. Audit answers ship with the artifact; isolate independent tasks for a real study.',
             'width_variants_not_independent_algorithms':True}
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
