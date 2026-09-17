"""Card-owned loopback route using frozen ModelMirror transport, not the legacy newAPI control policy."""
from pathlib import Path
import asyncio, hashlib, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
BASE=Path(__file__).resolve().parents[1]
OLD_WORK=BASE/'.local/gemini-real'
RETEST=os.environ.get('EARTH_RETEST')=='1'
WORK=BASE/'.local/gemini-retest' if RETEST else OLD_WORK
PORT=18308 if RETEST else 18307
LEDGER=BASE/'resources'/('RETEST_LEDGER.json' if RETEST else 'CALL_LEDGER.json')
LIMIT=1 if RETEST else 4
sys.dont_write_bytecode=True
sys.path.insert(0,str(OLD_WORK/'transport'))
from mm_transport.provider_chat import ProviderChatTransport,ProviderChatTarget
from mm_transport.egress import ProviderEgressPolicy
import httpx
from dotenv import dotenv_values
MODEL='google/gemini-3.8-flash'
ROUTE=f'http://127.0.0.1:{PORT}'
KEY=dotenv_values(Path(r'C:\Users\21547\Documents\模型浏览器\server\.env')).get('OPENROUTER_API_KEY','').strip()
if not KEY: raise SystemExit('CREDENTIAL_UNAVAILABLE')
TARGET=ProviderChatTarget.create(source='managed',provider_kind='openrouter',base_url='https://openrouter.ai/api/v1',api_key=KEY,connection_id='earth-card-gemini')
TRANSPORT=ProviderChatTransport(ProviderEgressPolicy())
LOCK=threading.Lock()
QUALIFIED=False
def sha(x): return hashlib.sha256(x).hexdigest()
def readj(p): return json.loads(p.read_text(encoding='utf-8'))
def prior_qualification():
 q=readj(OLD_WORK/'qualification.json')
 if not (q['status']=='complete' and q['done'] and q['finishReason']=='stop' and q['requestedModel']==MODEL and q['actualProvider']=='Google AI Studio'):raise ValueError('QUALIFICATION_INVALID')
 for row in readj(OLD_WORK/'freeze.json')['files']:
  if '/transport/' in row['path'].replace('\\','/') and sha(Path(row['path']).read_bytes())!=row['sha256']:raise ValueError('TRANSPORT_DRIFT')
 return q

if RETEST:
 prior_qualification()
 QUALIFIED=True

def verify():
 f=readj(WORK/'freeze.json')
 for row in f['files']:
  if sha(Path(row['path']).read_bytes())!=row['sha256']: raise ValueError('SOURCE_DRIFT')
 for kind,digest in f['requests'].items():
  if sha((WORK/(kind+'-request.json')).read_bytes())!=digest: raise ValueError('REQUEST_DRIFT')
 return f
async def transmit(payload,kind,folder):
 raw_parts=[];stream=bytearray();buffer='';actual=None;provider=None;finish=None;done=False;usage=None;response_id=None
 started=time.monotonic();first=None;http_status=None;error=None;wire_hash=None
 def event(block):
  nonlocal actual,provider,finish,done,usage,response_id,first
  data='\n'.join(line[5:].lstrip(' ') for line in block.splitlines() if line.startswith('data:'))
  if not data:return
  if done:raise ValueError('EVENT_AFTER_DONE')
  if data=='[DONE]':done=True;return
  obj=json.loads(data)
  if obj.get('error'):raise ValueError('UPSTREAM_ERROR')
  if obj.get('model'):actual=obj['model']
  if obj.get('provider'):provider=obj['provider']
  if obj.get('id'):response_id=obj['id']
  if obj.get('usage'):usage=obj['usage']
  choices=obj.get('choices',[])
  if not choices:return
  if len(choices)!=1:raise ValueError('CHOICE_COUNT')
  c=choices[0];delta=c.get('delta') or {}
  if delta.get('tool_calls') or delta.get('function_call'):raise ValueError('UNEXPECTED_TOOL')
  content=delta.get('content')
  if isinstance(content,str):
   if content and first is None:first=(time.monotonic()-started)*1000
   raw_parts.append(content)
  if c.get('finish_reason'):finish=c['finish_reason']
 try:
  async with httpx.AsyncClient(**ProviderChatTransport.client_kwargs(certification=kind=='certification')) as client:
   authorized=await TRANSPORT.authorize_managed_target(TARGET)
   request=TRANSPORT.build_authorized_stream_request(client,TARGET,authorized,payload,headers={'Accept':'text/event-stream','Content-Type':'application/json'})
   wire=request.content
   if json.loads(wire)!=payload:raise ValueError('WIRE_MISMATCH')
   wire_hash=sha(wire);(folder/'wire-request.json').write_bytes(wire)
   response=await TRANSPORT.send_authorized_stream(client,request)
   try:
    http_status=response.status_code
    if http_status!=200 or 'text/event-stream' not in response.headers.get('content-type',''):raise ValueError('HTTP_OR_TYPE')
    import codecs
    decoder=codecs.getincrementaldecoder('utf-8')()
    async for part in response.aiter_bytes():
     stream.extend(part)
     if len(stream)>16*1024*1024:raise ValueError('STREAM_SIZE')
     buffer+=decoder.decode(part);buffer=buffer.replace('\r\n','\n')
     while '\n\n' in buffer:
      block,buffer=buffer.split('\n\n',1);event(block)
    buffer+=decoder.decode(b'',final=True)
    if buffer.strip():raise ValueError('INCOMPLETE_EVENT')
   finally:await response.aclose()
  if not done or finish!='stop' or not ''.join(raw_parts).strip():raise ValueError('INCOMPLETE_STREAM')
  if actual not in [MODEL,'google/gemini-3.8-flash-20260902'] or provider!='Google AI Studio':raise ValueError('MODEL_OR_PROVIDER_MISMATCH')
 except BaseException as exc:
  error=type(exc).__name__
 raw=''.join(raw_parts)
 if KEY in raw or KEY.encode() in stream:
  raw='';stream=bytearray();error='CREDENTIAL_ECHO_BLOCKED'
 return {'status':'complete' if error is None else 'unknown','raw':raw,'stream':bytes(stream),'httpStatus':http_status,'requestedModel':MODEL,'actualModel':actual,'actualProvider':provider,'finishReason':finish,'done':done,'usage':usage,'responseId':response_id,'latencyMs':(time.monotonic()-started)*1000,'ttftMs':first,'error':error,'wireRequestHash':wire_hash,'transport':'ModelMirror ProviderChatTransport','retries':0,'fallbacksAllowed':False}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def respond(self,status,obj):
  data=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def allowed(self):
  return self.headers.get('Host')==f'127.0.0.1:{PORT}' and self.headers.get('Sec-Fetch-Site')!='cross-site' and self.headers.get('Origin') in (None,ROUTE)
 def do_GET(self):
  if not self.allowed():self.respond(403,{'error':'ORIGIN'});return
  try:
   verify()
   if self.path=='/api/status':self.respond(200,{'model':MODEL,'qualified':QUALIFIED,'keyPresent':bool(KEY),'transport':'ModelMirror ProviderChatTransport','retries':0});return
   kind=self.path.removeprefix('/api/preview?kind=')
   if kind not in ['generation','certification']:self.respond(404,{'error':'NOT_FOUND'});return
   b=(WORK/(kind+'-request.json')).read_bytes();self.respond(200,{'requestHash':sha(b),'payload':json.loads(b),'qualified':QUALIFIED})
  except Exception:self.respond(409,{'error':'FREEZE_DRIFT'})
 def do_POST(self):
  global QUALIFIED
  if not self.allowed() or self.headers.get('Origin')!=ROUTE or self.headers.get('Content-Type')!='application/json' or self.path!='/api/dispatch':self.respond(403,{'error':'ORIGIN_OR_PATH'});return
  if not LOCK.acquire(blocking=False):self.respond(409,{'error':'BUSY'});return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<300:raise ValueError()
   data=json.loads(self.rfile.read(size))
   if set(data)!={'reservationId'}:raise ValueError()
   f=verify();ledger=readj(LEDGER)
   if ledger['model']!=MODEL or ledger['route']!=ROUTE or ledger['used']!=len(ledger['entries']) or not 0<ledger['used']<=LIMIT or ledger['limit']!=LIMIT or ledger['remaining']!=LIMIT-ledger['used']:raise ValueError()
   entry=next(e for e in ledger['entries'] if e['id']==data['reservationId'])
   if entry!=ledger['entries'][-1] or entry['status']!='pending' or entry['freezeHash']!=sha((WORK/'freeze.json').read_bytes()):raise ValueError()
   kind=entry['kind']
   if kind not in ['certification','generation'] or (kind=='generation' and not QUALIFIED):raise ValueError()
   req=(WORK/(kind+'-request.json')).read_bytes()
   if entry['requestHash']!=sha(req):raise ValueError()
   folder=WORK/'dispatches'/('slot-'+str(entry['slot']));folder.mkdir(parents=True,exist_ok=False)
   (folder/'request.json').write_bytes(req);(folder/'freeze.json').write_bytes((WORK/'freeze.json').read_bytes())
   # This exclusive directory is the no-replay marker. Unknown results cannot use a new request id.
   async def bounded():return await asyncio.wait_for(transmit(json.loads(req),kind,folder),timeout=300)
   result=asyncio.run(bounded())
   raw=result.pop('raw');stream=result.pop('stream')
   result.update({'reservationId':entry['id'],'requestHash':sha(req),'rawHash':sha(raw.encode()),'kind':kind})
   (folder/'output.txt').write_text(raw,encoding='utf-8',newline='')
   (folder/'stream.sse').write_bytes(stream)
   (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
   if kind=='certification' and result['status']=='complete':QUALIFIED=True;(WORK/'qualification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
   self.respond(200,result)
  except Exception:
   self.respond(409,{'error':'DISPATCH_NOT_CONFIRMED_NO_RETRY'})
  finally:LOCK.release()
if __name__=='__main__':
 verify()
 server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
 (WORK/'route-owner.json').write_text(json.dumps({'pid':os.getpid(),'route':ROUTE,'transport':'frozen ModelMirror ProviderChatTransport','credentialCopied':False}),encoding='utf-8')
 print('Gemini controlled route listening; prior qualification reused' if RETEST else 'Gemini controlled route listening; qualification not run',flush=True)
 server.serve_forever()
