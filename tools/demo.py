#!/usr/bin/env python3
"""Reproduce a scripted edit/repair transcript. Does not call or train an LLM."""
from pathlib import Path
import json,sys
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'compiler'),str(R/'tools')]
from cairnc import compile_source
from agent_tools import EditSession,stable_json
from agent_loop import run

def main():
 base=R/'examples/agent';source=(base/'selection_before.cairn').read_text()
 _,receipt=compile_source(source)
 values=[[],[2],[0,2,3,2,9],[2,2,2],[0,1],[9,8,7],[2**64-1,0,2],list(range(19))]
 cases=[]
 for x in values:
  selected=[v for v in x if v>2]
  cases.append({'args':{'n':len(x),'out':[123]*len(x),'x':x,'threshold':2},
    'return':len(selected),'after':{'out':selected+[123]*(len(x)-len(selected))}})
 task={'schema':'cairn.task/1','symbol':'select_gt','task':'Stably select values STRICTLY greater than threshold. Return selected count. Preserve the unwritten output tail. Do not mutate the input.',
       'allowed_effects':receipt['functions']['select_gt']['effects'],'cases':cases}
 (base/'task.json').write_text(json.dumps(task,indent=2)+'\n')
 session=EditSession(source,'select_gt',task)
 (base/'packet.json').write_text(json.dumps(session.packet(),indent=2)+'\n')
 edit={'protocol':'cairn.edit/1','session':session.session,'kind':'body','replacement':'{ let used=compact out for i in n where x[i]>threshold yield x[i]; return used; }'}
 candidate,typed=session.check(edit)
 (base/'edit.json').write_text(json.dumps(edit,indent=2)+'\n')
 (base/'selection_after.cairn').write_text(candidate)
 (base/'typed_receipt.json').write_text(json.dumps(typed,indent=2)+'\n')
 actual,trace=run(source,task,[sys.executable,str(base/'scripted_adapter.py')],3,3,'scripted-fixture')
 assert actual==candidate
 assert trace['status']=='passed-reserved-finite-tests' and trace['attempts']==3
 assert trace['trace'][0]['feedback']['code']=='E-TYPE-MISMATCH'
 assert trace['trace'][1]['feedback']['tests']['status']=='failed-tests'
 (R/'results/scripted_demo.json').write_text(json.dumps(trace,indent=2)+'\n')
 print(json.dumps({k:v for k,v in trace.items() if k!='trace'},indent=2))
if __name__=='__main__':main()
