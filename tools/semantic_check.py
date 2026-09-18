#!/usr/bin/env python3
"""Compare two scalar CAIRN implementations against an immutable reference.

Exit 0: SMT-equivalent in the reported source model. Exit 1: counterexample.
Exit 2: unsupported, unknown, invalid contract, frontend or environment failure.
An exit 0 is not Lean verification or proof of native machine code.
"""
from pathlib import Path
import argparse,json,sys
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'compiler'))
from scalar_semantics import equivalent

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('reference',type=Path);ap.add_argument('candidate',type=Path)
    ap.add_argument('--symbol',required=True);ap.add_argument('--assume',default='true',help='Semantic input restriction, not an automatically emitted runtime guard.')
    ap.add_argument('--allow-reference-traps',action='store_true')
    ap.add_argument('--timeout-ms',type=int,default=3000);ap.add_argument('--obligations',type=Path)
    a=ap.parse_args();queries=[]
    try:
        r=equivalent(a.reference.read_text(),a.candidate.read_text(),a.symbol,assume=a.assume,
                     allow_reference_traps=a.allow_reference_traps,timeout_ms=a.timeout_ms,query_log=queries)
        if a.obligations:
            a.obligations.mkdir(parents=True,exist_ok=True)
            for i,q in enumerate(queries):(a.obligations/f'{i}_{q["stage"]}.smt2').write_text(q['smt2'])
        print(json.dumps(r,indent=2))
        return 0 if r['status']=='smt-equivalent' else 1 if r['status']=='counterexample' else 2
    except (OSError,ValueError) as e:
        print(json.dumps({'status':'unknown','reason':str(e)}));return 2
if __name__=='__main__':sys.exit(main())
