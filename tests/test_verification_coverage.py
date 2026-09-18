"""A proved scalar entry must not confer status on the rest of a program."""
from cairn.verification import verify_module
from cairn.cli import main
import pytest,json

REF='fn id(x:u64)->u64=x; fn twice(x:u64)->u64=add_wrap(id(x),x);'

def test_every_entry_checked():
    r=verify_module(REF,REF)
    assert r['status']=='smt-module-equivalent'
    assert r['covered']==['id','twice']
    assert r['lean_proof'] is False and r['native_proof'] is False

def test_extra_function_is_uncovered():
    r=verify_module(REF,REF+' fn hidden()->u64=0;')
    assert r['status']=='incomplete' and r['extra']==['hidden']
    assert r['uncovered']==['hidden']

def test_missing_function_blocks():
    r=verify_module(REF,'fn id(x:u64)->u64=x;')
    assert r['status']=='incomplete' and r['missing']==['twice']

def test_wrong_unused_function_cannot_hide():
    r=verify_module(REF,REF.replace('add_wrap(id(x),x)','x'))
    assert r['status']=='incomplete' and r['covered']==['id']
    assert r['results']['twice']['status']=='counterexample'

def test_owned_function_is_not_covered():
    src=REF+' fn scratch()->u64 {buffer x:u64[4]=zeroed;return x[0];}'
    r=verify_module(src,src)
    assert r['status']=='incomplete' and 'scratch' in r['uncovered']

def test_empty_module_is_not_a_proof():
    assert verify_module('','')['status']=='incomplete'

def test_signature_change_cannot_pass():
    r=verify_module('fn f(x:u64)->u64=x;','fn f(x:u32)->u32=x;')
    assert r['status']=='incomplete'

def test_trapping_reference_not_total():
    assert verify_module('fn f(x:u64)->u64=x+1;','fn f(x:u64)->u64=x+1;')['status']=='incomplete'

def test_bad_candidate():
    assert verify_module(REF,'fn not valid')['status']=='rejected'

def test_size_limit():
    assert verify_module(' '*64001,'')['status']=='incomplete'

def test_cli(tmp_path,capsys):
    a=tmp_path/'a.cairn';b=tmp_path/'b.cairn';a.write_text(REF);b.write_text(REF)
    assert main(['verify',str(a),str(b),'--all'])==0
    assert json.loads(capsys.readouterr().out)['status']=='smt-module-equivalent'
    b.write_text(REF+'fn extra()->u64=0;')
    assert main(['verify',str(a),str(b),'--all'])==2


def test_unknown_solver_cannot_cover(monkeypatch):
    import cairn.verification as v
    monkeypatch.setattr(v,'equivalent',lambda *a,**k:{'status':'unknown','reason':'solver unavailable'})
    r=v.verify_module(REF,REF)
    assert r['status']=='incomplete' and not r['covered']

def test_changed_public_type_blocks_module_status():
    r=verify_module('struct A {x:u64;} '+REF,'struct A {x:u32;} '+REF)
    assert r['status']=='incomplete' and not r['public_types_match']
    assert r['reference_intent_proved'] is False


def test_total_budget_exhaustion_is_unknown(monkeypatch):
    import cairn.verification as v
    times=iter([0,31,32])
    monkeypatch.setattr(v.time,'monotonic',lambda:next(times))
    r=v.verify_module(REF,REF)
    assert r['status']=='incomplete' and not r['covered']
