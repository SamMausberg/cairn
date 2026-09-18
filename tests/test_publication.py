"""No GitHub network calls. Exercise the exact publication orchestration with fakes."""
from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from publish_private import publish, PublishError, validate_private
from audit_repository import audit

SHA='a'*40
class Fake:
    def __init__(self,root,change=None):self.root=root;self.calls=[];self.change=change
    def __call__(self,args,*,cwd):
        self.calls.append(args)
        if args[:3]==['git','rev-parse','--show-toplevel']:return str(self.root)
        if args[:2]==['git','status']:return '?? secret.txt' if self.change=='dirty' else ''
        if args[:2]==['git','branch']:return 'main'
        if args==['git','remote']:return 'origin' if self.change=='remote' else ''
        if args[:3]==['git','rev-parse','HEAD']:return SHA
        if args[0]=='git':return ''
        if args[-1]=='user':return json.dumps({'login':'Other' if self.change=='owner' else 'TestOwner','type':'User'})
        if 'POST' in args and self.change=='collision':raise PublishError('Name conflict')
        if args[-1].endswith('/git/ref/heads/main'):return json.dumps({'object':{'sha':SHA}})
        return json.dumps({'id':123,'full_name':'TestOwner/cairn','private':self.change!='public',
            'visibility':'public' if self.change=='public' else 'private','fork':False,
            'owner':{'login':'TestOwner','type':'User'},'html_url':'https://github.com/TestOwner/cairn'})

def clean(root):return {'findings':[]}

def test_default_never_contacts_github(tmp_path):
    runner=Fake(tmp_path)
    result=publish(tmp_path,'TestOwner/cairn',run=runner,audit_fn=clean)
    assert result['remote_created'] is False
    assert all(c[0]=='git' for c in runner.calls)

@pytest.mark.parametrize('cause',['dirty','remote','owner','public','collision'])
def test_unsafe_state_never_pushes(tmp_path,cause):
    runner=Fake(tmp_path,cause)
    with pytest.raises(PublishError):publish(tmp_path,'TestOwner/cairn',execute=True,run=runner,audit_fn=clean)
    assert all('push' not in c for c in runner.calls)

def test_publication_order_private_before_push(tmp_path):
    runner=Fake(tmp_path)
    result=publish(tmp_path,'TestOwner/cairn',execute=True,run=runner,audit_fn=clean)
    assert result['status']=='published-private'
    push=next(i for i,c in enumerate(runner.calls) if 'push' in c)
    create=next(i for i,c in enumerate(runner.calls) if 'POST' in c)
    gets=[i for i,c in enumerate(runner.calls) if c[-1]=='repos/TestOwner/cairn']
    assert create<gets[0]<push<gets[1]
    assert 'private=true' in runner.calls[create]
    assert all('--force' not in c for c in runner.calls)
    assert runner.calls[-1][:3]==['git','remote','add']

@pytest.mark.parametrize('target',['x','../repo','owner/repo;echo','owner/a b','owner/','/repo','owner/a/b'])
def test_bad_target_has_no_actions(tmp_path,target):
    runner=Fake(tmp_path)
    with pytest.raises(PublishError):publish(tmp_path,target,execute=True,run=runner,audit_fn=clean)
    assert not runner.calls

def test_private_flag_must_be_boolean_true():
    with pytest.raises(PublishError):validate_private({'private':'true'},'a','b')

def test_secret_found_stops_before_github(tmp_path):
    runner=Fake(tmp_path)
    with pytest.raises(PublishError):publish(tmp_path,'TestOwner/cairn',execute=True,run=runner,audit_fn=lambda p:{'findings':['secret']})
    assert all(c[0]=='git' for c in runner.calls)

def test_historical_secret_is_not_printed(tmp_path):
    import subprocess
    def git(*args):subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init','-b','main');git('config','user.name','test');git('config','user.email','test@local.invalid')
    secret='ghp_'+'A'*36
    (tmp_path/'config.txt').write_text(secret)
    git('add','.');git('commit','-m','fixture')
    (tmp_path/'config.txt').write_text('clean')
    git('add','.');git('commit','-m','remove')
    result=audit(tmp_path)
    assert result['status']=='blocked'
    assert secret not in json.dumps(result)
    assert any(x['rule']=='github-token' for x in result['findings'])

@pytest.mark.parametrize('mutation',['private','id','owner','name'])
def test_creation_readback_failure_stops_before_upload(tmp_path, mutation):
    base=Fake(tmp_path)
    def runner(args, *, cwd):
        text=base(args,cwd=cwd)
        if args[-1]=='repos/TestOwner/cairn':
            data=json.loads(text)
            if mutation=='private':data['private']=False;data['visibility']='public'
            if mutation=='id':data['id']=456
            if mutation=='owner':data['owner']['login']='SomeoneElse'
            if mutation=='name':data['full_name']='TestOwner/other'
            return json.dumps(data)
        return text
    with pytest.raises(PublishError):
        publish(tmp_path,'TestOwner/cairn',execute=True,run=runner,audit_fn=clean)
    assert not any('push' in call for call in base.calls)


def test_final_visibility_change_never_registers_origin(tmp_path):
    base=Fake(tmp_path)
    def runner(args, *, cwd):
        text=base(args,cwd=cwd)
        if args[-1]=='repos/TestOwner/cairn' and any('push' in call for call in base.calls):
            data=json.loads(text);data['private']=False;data['visibility']='public'
            return json.dumps(data)
        return text
    with pytest.raises(PublishError):
        publish(tmp_path,'TestOwner/cairn',execute=True,run=runner,audit_fn=clean)
    assert any('push' in call for call in base.calls)
    assert not any(call[:3]==['git','remote','add'] for call in base.calls)
