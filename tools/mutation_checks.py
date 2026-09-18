#!/usr/bin/env python3
"""Inject one type-correct behavioral defect per algorithm family.

These are hand-authored mutations, not model completions or an unbiased mutation
score. Independent finite tests must detect all listed defects.
"""
from pathlib import Path
import json,sys
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'src'),str(R/'tools')]
from cairn.cairnc import compile_source
from task_eval import evaluate
from cairn.agent_tools import digest

def main():
 tasks=json.loads((R/'training/source/all_tasks_with_oracles.json').read_text())
 muts={
 'affine':lambda s:s.replace('add_wrap(mul_wrap(x, 2), 1)','add_wrap(mul_wrap(x, 2), 2)'),
 'count_gt':lambda s:s.replace('x[i] > threshold','x[i] >= threshold'),
 'prefix':lambda s:s.replace('total = add_wrap(total, x[i]);\n    out[i] = total;', 'out[i] = total;\n    total = add_wrap(total, x[i]);'),
 'compact_gt':lambda s:s.replace('x[i] > threshold','x[i] >= threshold'),
 'clamp':lambda s:s.replace('min(max(x, 2), 3)','min(max(x, 2), 4)'),
 'interval':lambda s:s.replace('x[i] < hi','x[i] <= hi'),
 'rotate_xor':lambda s:s.replace(' ^ key',' | key'),
 'delta':lambda s:s.replace('sub_wrap(x[i], x[(i - 1)])','add_wrap(x[i], x[(i - 1)])'),
 }
 rows=[]
 for family,change in muts.items():
  task=next(t for t in tasks if t['id']==family+'_0');old=task['source'];new=change(old)
  if old==new:raise AssertionError('Mutation failed to change '+family+'\n'+old)
  compile_source(new)
  r=evaluate(new,task['contract'])
  if r['status']!='failed-tests':raise AssertionError(str(r))
  rows.append({'family':family,'source':new,'source_sha256':digest(new),'frontend':'typed','verdict':r})
 result={'cases':len(rows),'detected':len(rows),'mutations':rows,'model_generated':False,
         'interpretation':'Eight hand-authored defects; not a general mutation score or a model success result.'}
 (R/'results/mutation_checks.json').write_text(json.dumps(result,indent=2)+'\n')
 print('Detected',len(rows),'type-correct behavioral mutations.')
if __name__=='__main__':main()
