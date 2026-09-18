import copy
from pathlib import Path
import sys
import pytest
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'compiler'),str(R/'tools')]
from task_eval import validate_contract,evaluate

SRC='fn f(x:u64)->u64 { return add_wrap(x,1); }'
TASK={'schema':'cairn.task/1','symbol':'f','task':'Increment modulo 2^64.',
      'cases':[{'args':{'x':0},'return':1},{'args':{'x':2**64-1},'return':0}]}

def test_native_pass():
 r=evaluate(SRC,TASK);assert r['status']=='passed-finite-tests' and r['cases']==2

def test_typechecks_but_behavior_fails():
 r=evaluate(SRC.replace('x,1','x,2'),TASK)
 assert r['status']=='failed-tests' and r['case']==0 and r['actual_return']==2

def test_typed_overflow_traps_instead_of_passing():
 r=evaluate(SRC.replace('add_wrap(x,1)','x+1'),TASK)
 assert r['status']=='native-trap-or-crash' and r['last_case_started']==1

@pytest.mark.parametrize('value',[-1,2**64,True,1.5,'1',None])
def test_bad_integer_contract(value):
 t=copy.deepcopy(TASK);t['cases'][0]['args']['x']=value
 assert evaluate(SRC,t)['status']=='invalid-contract'

@pytest.mark.parametrize('edit',[{'schema':'wrong'},{'cases':[]},{'symbol':'other'},
 {'cases':[{'args':{'x':0}}]},{'cases':[{'args':{'y':0},'return':0}]}])
def test_invalid_contract(edit):
 t={**TASK,**edit};assert evaluate(SRC,t)['status']=='invalid-contract'

def test_all_output_tail_is_checked():
 s='fn f(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host)->usize{let used=compact out for i in n where x[i]>2 yield x[i];return used;}'
 t={'schema':'cairn.task/1','symbol':'f','cases':[{'args':{'n':3,'out':[99,99,99],'x':[1,3,2]},'return':1,'after':{'out':[3,99,99]}}]}
 assert evaluate(s,t)['status']=='passed-finite-tests'
 t['cases'][0]['after']['out']=[3,0,0]
 assert evaluate(s,t)['status']=='failed-tests'
 del t['cases'][0]['after']
 assert evaluate(s,t)['status']=='invalid-contract'

def test_frontend_failure_not_test_failure():
 r=evaluate(SRC.replace('x,1','undefined,1'),TASK)
 assert r['status']=='rejected' and r['stage']=='frontend'

def test_nonfinite_is_a_behavior_failure_not_a_runner_crash():
 s='fn f(x:f64)->f64{return x/x;}'
 t={'schema':'cairn.task/1','symbol':'f','cases':[{'args':{'x':0.0},'return':1.0}]}
 r=evaluate(s,t)
 assert r['status']=='failed-tests' and r['actual_return']=={'nonfinite':'nan'}
