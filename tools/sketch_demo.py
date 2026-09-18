#!/usr/bin/env python3
"""Executable host-bound sketch demo. Deterministic search, not an AI trial."""
from pathlib import Path
import json,sys
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'compiler'),str(R/'tools')]
from agent_tools import EditSession,stable_json
from sketches import Sketch,ScalarContract,solve_finite
from task_eval import evaluate

REFERENCE='''fn average(x:u64,y:u64)->u64 {
  return (x/2)+(y/2)+((x%2+y%2)/2);
}
'''
SOURCE='''// The host owns this signature and the task.
fn average(x:u64,y:u64)->u64 {
  return (x+y)/2;
}
'''
TASK={'symbol':'average','task':'For all u64 inputs, return floor((x+y)/2) over mathematical integers without overflow or traps.'}
CHOICES={'value':['x+y','(x+y)/2','add_wrap(x,y)/2','(x&y)+shr(x^y,1)']}

def make():
    return Sketch(SOURCE,'average',task=TASK,semantic=ScalarContract(REFERENCE,'average')).hole('value','(x+y)/2')

def main():
    folder=R/'examples/sketch';folder.mkdir(exist_ok=True)
    results=R/'results';results.mkdir(exist_ok=True)
    sk=make();packet=sk.packet()
    (folder/'reference.cairn').write_text(REFERENCE);(folder/'before.cairn').write_text(SOURCE)
    (folder/'packet.json').write_text(json.dumps(packet,indent=2)+'\n')
    outputs=[]
    for cache in [False,True]:
        r=solve_finite(make(),CHOICES,use_counterexample_cache=cache,timeout_ms=10000)
        assert r['status']=='smt-equivalent'
        r['counterexample_cache']=cache;outputs.append(r)
    candidate=outputs[-1]['candidate'];(folder/'after.cairn').write_text(candidate)
    (folder/'choices.json').write_text(json.dumps(outputs[-1]['choices'],indent=2)+'\n')
    queries=[];certificate=sk.check_semantics(sk.fill(**outputs[-1]['choices']),query_log=queries)
    for i,q in enumerate(queries):(folder/f'average_{i}_{q["stage"]}.smt2').write_text(q['smt2'])
    (folder/'semantic_receipt.json').write_text(json.dumps(certificate,indent=2)+'\n')
    m=2**64-1;edges=[0,1,2,3,2**32,2**63-1,2**63,m-1,m]
    task={'schema':'cairn.task/1',**TASK,'cases':[{'args':{'x':x,'y':y},'return':(x+y)//2} for x in edges for y in edges]}
    native=evaluate(candidate,task)
    if native['status']!='passed-finite-tests':raise AssertionError(native)
    (folder/'finite_task.json').write_text(json.dumps(task,indent=2)+'\n')
    (results/'sketch_search.json').write_text(json.dumps({'runs':outputs,'native':native},indent=2)+'\n')
    # Same scalar task, same source context/rule cards, different transport.
    old=EditSession(SOURCE,'average',TASK);site=next(k for k,v in old.sites.items() if v['source']=='(x+y)/2')
    oldpacket=old.packet(site);oldpacket['semantic_contract']=packet['semantic_contract'];oldreply={'protocol':'cairn.edit/1','session':old.session,'kind':'expr','site':site,'replacement':outputs[-1]['choices']['value']}
    def tokens(x):return len((json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode('utf-8'))
    density={'tokenizer':'ByT5 plain UTF-8 byte encoding; no special tokens; not frontier BPE',
        'serialization':'Sorted compact JSON, one final newline, all fields retained',
        'old_expression_packet':tokens(oldpacket),'named_choices_packet':tokens(packet),
        'old_expression_reply':tokens(oldreply),'named_choices_reply':tokens(outputs[-1]['choices']),
        'same_visible_source':packet['source']=='\n\n'.join(x['source'] for x in oldpacket['context']),
        'same_rule_cards':packet['rule_cards']==oldpacket['rule_cards'],
        'same_semantic_contract':packet['semantic_contract']==oldpacket['semantic_contract'],
        'baseline_augmentation':'The expression protocol receives the same new reference/domain capsule for equal-information comparison.',
        'host_setup_source_bytes':len(Path(__file__).read_bytes()),
        'reference_source_bytes':len(REFERENCE.encode()),
        'limitation':'Constructed transport comparison, not a model interaction or a full cold-start win. Host setup/reference authoring, semantic instructions and repair feedback are additional costs.'}
    (results/'sketch_density.json').write_text(json.dumps(density,indent=2)+'\n')
    print(json.dumps({'status':'passed','searches':[{'cache':r['counterexample_cache'],'candidates':len(r['attempts']),'semantic_checker_calls':r['solver_calls'],'smt_queries':r['smt_queries'],'cache_rejections':r['cache_rejections']} for r in outputs],
                      'native_cases':len(task['cases']),'density':density,'model_used':False},indent=2))
if __name__=='__main__':main()
