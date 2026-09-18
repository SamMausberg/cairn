import sys
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(R/'compiler'))
from scalar_semantics import equivalent,prepared,Concrete,Formula,Symbolic,conj,same,neg,constant
from smt_bridge import Solver


def fn(body,params='x:u64',ret='u64'):
    return f'fn f({params})->{ret}{{{body}}}'

def check(a,b,expected='smt-equivalent',**kw):
    r=equivalent(a,b,'f',**kw)
    assert r['status']==expected,r
    assert not r['lean_verified'] and not r['native_verified']
    return r

@pytest.mark.parametrize('ty',['u8','u16','u32','u64','usize'])
def test_wrap_and_checked_boundary(ty):
    r=check(fn('return add_wrap(x,1);',f'x:{ty}',ty),fn('return x+1;',f'x:{ty}',ty),'counterexample')
    assert r['counterexample']['x']==(1<<({'u8':8,'u16':16,'u32':32}.get(ty,64)))-1
    assert r['expected']['return']==0 and r['actual']['defined']==False

@pytest.mark.parametrize('ty',['u8','u16','u32','u64','usize','i32','i64'])
def test_min_branch(ty):
    check(fn('if x<y{return x;}else{return y;}',f'x:{ty},y:{ty}',ty),fn('return min(x,y);',f'x:{ty},y:{ty}',ty))

@pytest.mark.parametrize('ty',['u8','u16','u32','u64'])
def test_average_total(ty):
    check(fn('return (x/2)+(y/2)+((x&1)&(y&1));',f'x:{ty},y:{ty}',ty),
          fn('return (x&y)+shr(x^y,1);',f'x:{ty},y:{ty}',ty))

@pytest.mark.parametrize('op',['+','-','*','/','%'])
def test_partial_arithmetic_identity(op):
    a=fn(f'return x{op}y;','x:u8,y:u8','u8')
    check(a,a,allow_reference_traps=True)

@pytest.mark.parametrize('ty',['i32','i64'])
def test_signed_negation(ty):
    r=check(fn('return x;',f'x:{ty}',ty),fn('return -(-x);',f'x:{ty}',ty),'counterexample')
    assert r['counterexample']['x']==-(1<<(int(ty[1:])-1))

@pytest.mark.parametrize('params,ref,other',[('x:bool,y:bool','x<y','(!x)&&y'),
 ('x:bool,y:bool','x<=y','(!x)||y'),('x:bool,y:bool','x>y','x&&(!y)'),
 ('x:bool,y:bool','x>=y','x||(!y)'),('x:bool,y:bool','x==y','!(x!=y)')])
def test_bool_order(params,ref,other):
    check(fn('return '+ref+';',params,'bool'),fn('return '+other+';',params,'bool'))

@pytest.mark.parametrize('ty',['u8','u16','u32','u64'])
def test_saturating_add(ty):
    m=(1<<int(ty[1:]))-1
    a=fn(f'if x>{m}-y{{return {m};}}return x+y;',f'x:{ty},y:{ty}',ty)
    b=fn(f'let z=add_wrap(x,y);if z<x{{return {m};}}return z;',f'x:{ty},y:{ty}',ty)
    check(a,b)

@pytest.mark.parametrize('expr,correct',[('x!=0 && x/x==1','x!=0'),('x==0 || x/x==1','true')])
def test_short_circuit(expr,correct):
    check(fn('return '+expr+';',ret='bool'),fn('return '+correct+';',ret='bool'))

def test_eager_condition_is_not_lazy():
    check(fn('return x==0 || x/x==1;',ret='bool'),fn('return x/x==1 || x==0;',ret='bool'),'counterexample')

@pytest.mark.parametrize('src,dst',[('u8','u32'),('u8','i32'),('i32','i64'),('u32','i64')])
def test_widen_roundtrip(src,dst):
    check(fn('return x;',f'x:{src}',src),fn(f'return {src}({dst}(x));',f'x:{src}',src))

def test_narrow_domain_and_vacuity():
    a=fn('return u8(x);',ret='u8');b=fn('return u8(x&255);',ret='u8')
    check(a,b,assume='x<=255')
    check(a,b,'invalid-reference')
    check(a,b,'counterexample',allow_reference_traps=True)
    check(a,b,'invalid-domain',assume='x<x')
    check(a,b,'invalid-domain',assume='x/x==1')

def test_branch_assignment_and_scope():
    a=fn('let mut a=x;if x<3{let v=1;a=v;}else{a=2;}return a;')
    b=fn('if x<3{return 1;}else{return 2;}')
    check(a,b)

def test_user_call_and_early_return():
    a='fn helper(x:u64)->u64{if x>9{return 9;}return x;}'+fn('return helper(x);')
    b='fn helper(x:u64)->u64{if x>9{return 9;}return x;}'+fn('return min(x,9);')
    check(a,b)

@pytest.mark.parametrize('source',[
 fn('let mut a=x;while a>0{a=a-1;}return a;'),
 fn('return f(x);'),
 'fn f(x:f64)->f64{return x;}',
 'fn f(n:usize,x:ro<u64>[n]@host)->u64{return x[0];}',
 'fn f(x:u64){return;}',
 'fn f(x:u64)->u64{for i in 0..3{}return x;}'
])
def test_unsupported_not_proved(source):
    check(source,source,'unknown')

def test_domain_totality_shortcircuit():
    a=fn('return x/y;','x:u64,y:u64')
    check(a,a,assume='y!=0 && x/y<=x')
    check(a,a,'invalid-domain',assume='x/y<=x')

def test_type_error_not_semantic_success():
    check(fn('return x;'),fn('return true;'),'rejected')

def test_signature_mismatch():
    check(fn('return x;'),fn('return x;','x:u32','u32'),'invalid-contract')

def test_trap_equivalence_is_explicit():
    a=fn('return x/0;');b=fn('return x+1;')
    check(a,a,'invalid-reference')
    check(a,a,allow_reference_traps=True)
    check(a,b,'counterexample',allow_reference_traps=True)

def test_api_parse_error_unknown():
    with Solver() as s:
        assert s.check('(assert INVALID)',{})['status']=='unknown'

@pytest.mark.parametrize('ty',['i32','i64'])
def test_signed_remainder_not_python_modulo(ty):
    source=fn('return x%y;',f'x:{ty},y:{ty}',ty)
    c=Concrete(prepared(source))
    assert c.outcome('f',{'x':-7,'y':3})['return']==-1
    assert c.outcome('f',{'x':7,'y':-3})['return']==1
    check(source,source,allow_reference_traps=True)

@pytest.mark.parametrize('operation',['shl_wrap','shr'])
def test_shift_bounds(operation):
    a=fn(f'return {operation}(x,k);','x:u8,k:usize','u8')
    check(a,a,'invalid-reference')
    check(a,a,assume='k<8')
    check(a,a,allow_reference_traps=True)

def test_signed_unsigned_conversion_guard():
    a=fn('return x;','x:i64','i64');b=fn('return i64(u64(x));','x:i64','i64')
    check(a,b,'counterexample')
    check(a,b,assume='x>=0')

def test_solver_obligation_saved():
    logs=[]
    r=equivalent(fn('return x;'),fn('return add_wrap(x,0);'),'f',query_log=logs)
    assert r['status']=='smt-equivalent' and logs and all('(check-sat)' in x['smt2'] for x in logs)

@pytest.mark.parametrize('bad',[None,{},15,False])
def test_malformed_source_fails_closed(bad):
    assert equivalent(bad,fn('return x;'),'f')['status']=='invalid-contract'

def test_precondition_cannot_inject_a_declaration():
    r=equivalent(fn('return x;'),fn('return x;'),'f',assume='true); } fn injected()->bool {return true;')
    assert r['status']=='rejected'
