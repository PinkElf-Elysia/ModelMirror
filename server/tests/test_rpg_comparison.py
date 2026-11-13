"""C1-B real ASGI/SQLite routing with only synthetic MockTransport responses."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import pytest
from server.tests.test_rpg_authoring_route import route, configure, task as legacy_task, PATH, TASK
from server.tests.test_rpg_parameters_v2 import task as typed_task
from server.tests.test_rpg_bridge import HEADERS

BASE = '/api/rpg/v1/authoring/comparison-reservations'
STATUS = '/api/rpg/v1/authoring/comparison-budget'

def plan(name='batch-one', count=2, revision=1):
    return {'id':name,'hash':'a'*64,'sessionId':'session-p1','operationId':'create-'+name,'routeRevision':revision,
            'stages':[{'id':name+'-'+str(n),'name':'candidate-'+str(n),'phase':'candidate','purpose':'story'} for n in range(count)]}

def claim(client, p, number=0):
    stage=p['stages'][number]
    r=typed_task(client, request=stage['id'])
    r.update(format='modelmirror.rpg.authoring-task/3', comparison={'batchId':p['id'],'stageId':stage['id'],'planHash':p['hash']})
    response=client.post(BASE+'/'+p['id']+'/claim',headers=HEADERS,json={'stageId':stage['id'],'requestHash':r['sourceRequestHash']})
    assert response.status_code==200,response.text
    return r

def test_auth_closed_and_atomic_hold(route,monkeypatch):
    c,_,_,_,calls=route;configure(route);p=plan()
    assert c.post(BASE,json=p).status_code==401
    assert c.post(BASE,headers={**HEADERS,'Origin':'https://invalid.test'},json=p).status_code==403
    a=c.post(BASE,headers=HEADERS,json=p);assert a.status_code==200,a.text
    assert c.post(BASE,headers=HEADERS,json=p).json()==a.json()
    assert c.post(BASE,headers=HEADERS,json={**p,'hash':'b'*64}).status_code==409
    assert c.get(STATUS,headers=HEADERS).json()['reserved']==2
    rejected=c.post(TASK,headers=HEADERS,json=legacy_task(route)).json()
    assert not rejected['receipt']['dispatched'] and not calls
    assert c.post(BASE,headers=HEADERS,json=plan('second')).status_code==409
    assert c.put(PATH,headers=HEADERS,json={**route[3],'operationId':'lower','expectedRevision':1,'limit':1}).status_code==409
    monkeypatch.setenv('RPG_AUTHORING_ROUTING_ENABLED','false')
    assert c.get(STATUS,headers=HEADERS).status_code==503


def test_reserved_task_consumes_once_and_releases_remaining(route):
    c,_,_,_,calls=route;configure(route);p=plan()
    assert c.post(BASE,headers=HEADERS,json=p).status_code==200
    request=claim(c,p)
    response=c.post(TASK,headers=HEADERS,json=request)
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='completed',result
    assert result['receipt']['dispatched'] and len(calls)==1
    state=c.get(STATUS,headers=HEADERS).json()
    assert (state['used'],state['reserved'],state['remaining'])==(1,1,0)
    assert c.post(TASK,headers=HEADERS,json=request).json()==result and len(calls)==1
    settled=c.post(BASE+'/'+p['id']+'/settle',headers=HEADERS,json={'stageId':request['requestId'],'requestHash':request['sourceRequestHash'],'status':'completed','resultHash':'e'*64})
    assert settled.status_code==200,settled.text
    assert c.post(BASE+'/'+p['id']+'/settle',headers=HEADERS,json={'stageId':request['requestId'],'requestHash':request['sourceRequestHash'],'status':'not_dispatched','resultHash':'f'*64}).status_code==409
    assert c.post(BASE+'/'+p['id']+'/release',headers=HEADERS,json={'stageId':request['requestId']}).status_code==409
    assert c.post(BASE+'/'+p['id']+'/release',headers=HEADERS,json={'stageId':p['stages'][1]['id']}).status_code==200
    assert c.get(STATUS,headers=HEADERS).json()['remaining']==1
    assert c.post(TASK,headers=HEADERS,json=legacy_task(route,2)).json()['status']=='completed'
    assert c.get(PATH,headers=HEADERS).json()['spent']==2 and len(calls)==2


def test_wrong_bindings_and_released_claim_never_dispatch(route):
    c,_,_,_,calls=route;configure(route);p=plan();c.post(BASE,headers=HEADERS,json=p)
    request=claim(c,p)
    for field,value in [('sessionId','elsewhere'),('sourceRequestHash','d'*64),('purpose','memory')]:
        assert c.post(TASK,headers=HEADERS,json={**request,field:value}).status_code==409
    r=c.post(BASE+'/'+p['id']+'/settle',headers=HEADERS,json={'stageId':request['requestId'],'requestHash':request['sourceRequestHash'],'status':'not_dispatched','resultHash':'f'*64})
    assert r.status_code==200,r.text
    assert c.post(TASK,headers=HEADERS,json=request).status_code==409
    assert c.get(STATUS,headers=HEADERS).json()['remaining']==1 and not calls


def test_concurrent_batches_cannot_over_reserve(route):
    c,_,_,_,calls=route;configure(route)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda name:c.post(BASE,headers=HEADERS,json=plan(name)).status_code,['a','b']))
    assert sorted(statuses)==[200,409]
    assert c.get(STATUS,headers=HEADERS).json()['remaining']==0 and not calls


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(stages=p['stages'][:1]),
    lambda p:p.update(stages=p['stages']*2),
    lambda p:p.update(routeRevision=True),
    lambda p:p.update(url='https://invalid.test'),
    lambda p:p['stages'][0].update(purpose='memory'),
])
def test_invalid_reservations_do_not_write_or_call(route,mutate):
    c,_,_,_,calls=route;configure(route);p=plan();mutate(p)
    assert c.post(BASE,headers=HEADERS,json=p).status_code==422
    assert c.get(STATUS,headers=HEADERS).json()['reserved']==0 and not calls


def test_claim_survives_new_service_instances_and_unknown_keeps_hold(route,monkeypatch):
    c,_,_,_,calls=route;configure(route);p=plan();c.post(BASE,headers=HEADERS,json=p);r=claim(c,p)
    repeated=c.post(BASE+'/'+p['id']+'/claim',headers=HEADERS,json={'stageId':r['requestId'],'requestHash':r['sourceRequestHash']})
    assert repeated.json()['dispatch'] is False
    assert c.get(BASE+'/'+p['id'],headers=HEADERS).json()['tasks'][0]['state']=='claimed'
    assert c.get(STATUS,headers=HEADERS).json()['remaining']==0 and not calls
    # A body with a forged terminal result cannot refund a dispatched attempt.
    assert c.post(BASE+'/'+p['id']+'/settle',headers=HEADERS,json={'stageId':r['requestId'],'requestHash':r['sourceRequestHash'],'status':'completed','resultHash':'e'*64}).status_code==409


def test_maximum_nine_stages_share_one_atomic_reservation(route):
    c,_,_,config,calls=route;config['limit']=9;configure(route);p=plan(count=4)
    p['stages']=[{'id':'before-'+kind,'name':kind+'-before','phase':'before','purpose':kind} for kind in ['summary','compression']]+p['stages']+[{'id':'after-'+kind,'name':kind+'-after','phase':'after','purpose':kind} for kind in ['summary','compression','memory']]
    assert c.post(BASE,headers=HEADERS,json=p).status_code==200
    assert c.get(STATUS,headers=HEADERS).json()['reserved']==9
    assert c.post(BASE,headers=HEADERS,json=plan('extra')).status_code==409
    for stage in p['stages']:
        assert c.post(BASE+'/'+p['id']+'/release',headers=HEADERS,json={'stageId':stage['id']}).status_code==200
    assert c.get(STATUS,headers=HEADERS).json()['remaining']==9 and not calls


@pytest.mark.parametrize('field',['phase','purpose'])
def test_unhashable_plan_fields_are_rejected_before_storage(route,field):
    c,_,_,_,calls=route;configure(route);p=plan();p['stages'][0][field]=[]
    assert c.post(BASE,headers=HEADERS,json=p).status_code==422
    assert c.get(STATUS,headers=HEADERS).json()['reserved']==0 and not calls
