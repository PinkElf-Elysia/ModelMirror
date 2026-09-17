from pathlib import Path
import asyncio,hashlib,json,os,sys
BASE=Path(__file__).resolve().parents[1]
WORK=BASE/'.local/real'
CONFIG=Path(r'C:\tmp\modelmirror-ai-rpg-rpg04\experiments\ai-rpg-engine\.rpg04-work\f-real-20260909-01\controlled-launch-environment.json')
# Configuration stays internal to the service; no values are emitted or copied.
os.environ.clear();os.environ.update(json.loads(CONFIG.read_text()))
sys.path.insert(0,str(BASE/'.local/route-recovery-20260917/code'))
from server.model_router.service import ModelRouterService
from server.model_router.chat_certification import ProviderChatCertificationService,CHAT_TEXT_CERTIFICATION_MAX_TOKENS
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.schemas import ProviderChatControlPolicyUpdate
async def main():
 service=ModelRouterService();control=ProviderChatControlService(service);before=control.get_policy()
 routes=[r for r in before.routes if r.capability=='chat_text']
 assert before.configured_mode=='newapi_preferred' and before.stable_model_ids==['gpt-5.6-luna']
 assert len(routes)==1 and routes[0].connection_ids==['conn_e23170b51a4c4564945cce7a2415769f']
 public=control.public_status('gpt-5.6-luna','chat_text').model_dump(mode='json')
 if sys.argv[1]=='--inspect':
  print(json.dumps({'public':public,'policyRevision':before.revision,'connectionIds':routes[0].connection_ids,'certificationMaxTokens':CHAT_TEXT_CERTIFICATION_MAX_TOKENS}));return
 assert sys.argv[1]=='--reserved' and CHAT_TEXT_CERTIFICATION_MAX_TOKENS==64
 ledger=json.loads((BASE/'resources/CALL_LEDGER.json').read_text())
 entry=next(x for x in ledger['entries'] if x['id']==sys.argv[2])
 assert entry['kind']=='certification' and entry['status']=='pending'
 WORK.mkdir(exist_ok=True,parents=True)
 with (WORK/('certification-'+entry['id']+'-started.json')).open('x') as marker:json.dump({'reservationId':entry['id'],'maxTokens':64},marker)
 result=await ProviderChatCertificationService(service).run(routes[0].connection_ids[0],model_id='gpt-5.6-luna',capability='chat_text',acknowledge_billed_call=True,idempotency_key='earth-card-'+entry['id'])
 data=result.model_dump(mode='json')
 safe={k:data.get(k) for k in ('id','status','requested_model','actual_model','error_code','completed_at','checks','usage','certification_id') if k in data}
 safe['maxTokens']=64
 if safe.get('status')=='passed':
  control.update_policy(ProviderChatControlPolicyUpdate(expected_revision=before.revision,mode=before.configured_mode,auto_enabled=before.auto_enabled,stable_model_ids=before.stable_model_ids,routes=[r.model_dump() for r in before.routes]))
  safe['qualificationAfter']=control.public_status('gpt-5.6-luna','chat_text').model_dump(mode='json')
 (WORK/('certification-'+entry['id']+'-result.json')).write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(safe,ensure_ascii=False),flush=True)
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as e:
  print(json.dumps({'status':'unknown','errorType':type(e).__name__,'automaticRetry':False}),flush=True)
  sys.exit(1)
