import json,subprocess,sqlite3,pathlib,os,secrets,sys,atexit
from pathlib import Path
root=pathlib.Path.cwd();run=root/"experiments/ai-rpg-engine/.rpg03-work/tools-isolation-mock"
if sys.argv[1:] != ["--mock-only"]: raise SystemExit("Explicit --mock-only required; this harness cannot call a real provider")
run.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root));sys.path.insert(0,str(Path(__file__).resolve().parent))
dest=run/"router";dest.mkdir(exist_ok=True)
assert not (dest/"router.sqlite3").exists(),"Do not reset a previous runtime"
remote=r'''
import sqlite3,json,os
from pathlib import Path
from model_router.repository import SQLiteRouterRepository
conn="conn_49cedf36714247c9b9c50cf60978c277";model="google/gemini-3.8-flash"
d=sqlite3.connect("file:/app/model_router/storage/router.sqlite3?mode=ro",uri=True);d.row_factory=sqlite3.Row
schemas=[dict(r) for r in d.execute("select type,name,sql from sqlite_master where sql is not null and name not like 'sqlite_%'")]
names={r["name"] for r in schemas if r["type"]=="table"}
rows={}
for t in ["router_metadata","router_connections","router_policies","provider_chat_stable_policies","provider_chat_capability_routes","provider_chat_model_qualifications","provider_chat_certifications","provider_catalog_models","provider_catalog_offerings","provider_chat_gate_epochs","provider_chat_gate_approvals","provider_chat_runs","provider_chat_attempts","router_decisions"]:
 if t not in names:continue
 cols=[r[1] for r in d.execute('pragma table_info("'+t+'")')]
 filters=[];args=[]
 if t=="router_connections":filters.append("id=?");args.append(conn)
 elif "connection_id" in cols and t!="provider_chat_capability_routes":filters.append("connection_id=?");args.append(conn)
 for c in ["requested_model","model_id"]:
  if c in cols:filters.append(c+"=?");args.append(model)
 rows[t]=[dict(r) for r in d.execute('select * from "'+t+'"'+(" where "+" and ".join(filters) if filters else ""),args)]
# Preserve the ordered route references. Unselected entries carry NO credentials and are disabled.
public_cols=[r[1] for r in d.execute("pragma table_info(router_connections)") if r[1] not in ("api_key_ciphertext","masked_key")]
for ref in {r["connection_id"] for r in rows["provider_chat_capability_routes"]}-{conn}:
 row=d.execute("select "+",".join(public_cols)+" from router_connections where id=?",(ref,)).fetchone()
 assert row is not None,"Route reference missing"
 item=dict(row);item.update(api_key_ciphertext="",masked_key="",enabled=0)
 rows["router_connections"].append(item)
rows["provider_catalog_refreshes"]=[dict(r) for r in d.execute("select * from provider_catalog_refreshes where id in (select last_refresh_id from provider_catalog_models where connection_id=? and model_id=?)",(conn,model))]
rows["provider_qualification_series"]=[dict(r) for r in d.execute("select * from provider_qualification_series where connection_id=? and model_id=?",(conn,model))]
ids=[r["id"] for r in rows["provider_qualification_series"]]
for t in ["provider_qualification_events","provider_qualification_intervals"]:
 rows[t]=[dict(r) for r in d.execute("select * from "+t+" where series_id in ("+",".join("?" for _ in ids)+")",ids)]
certs=[r["certification_id"] for r in rows["provider_qualification_events"]]
for t in ["provider_qualification_observations","provider_certification_call_contexts"]:
 rows[t]=[dict(r) for r in d.execute("select * from "+t+" where certification_id in ("+",".join("?" for _ in certs)+")",certs)]
key=os.getenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY","").strip() or os.getenv("MODEL_ROUTER_CREDENTIAL_MASTER_KEY","").strip() or Path("/app/model_router/storage/credential-master.key").read_bytes().strip()
assert json.loads(rows["provider_catalog_models"][0]["metadata_json"])["rpg_provider_tag"]=="google-ai-studio"
print(json.dumps({"schemas":schemas,"rows":rows,"key":SQLiteRouterRepository._normalize_key(key).decode(),"version":d.execute("pragma user_version").fetchone()[0]}))
'''
p=subprocess.run(["docker","exec","-i","modelmirror-server","python","-"],input=remote,text=True,capture_output=True,check=True)
data=json.loads(p.stdout);del p
endpoint=json.loads((root/"experiments/ai-rpg-engine/.rpg03-work/tools-real-dns-v1/dns-preflight.json").read_text(encoding="utf-8"))["endpoint"]
before=[]
for row in data["rows"]["provider_catalog_models"]:
 meta=json.loads(row["metadata_json"]);before=meta["supported_parameters"];meta["supported_parameters"]=endpoint["supported_parameters"];row["metadata_json"]=json.dumps(meta,ensure_ascii=False,separators=(",",":"))
(run/"isolated-capability-refresh.json").write_text(json.dumps({"before":before,"after":endpoint["supported_parameters"],"scope":"isolated copy only","source":"https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints"},indent=2),encoding="utf-8")
db=sqlite3.connect(dest/"router.sqlite3")
for item in sorted(data["schemas"],key=lambda r:0 if r["type"]=="table" else 1):db.execute(item["sql"])
for table,rows in data["rows"].items():
 for row in rows:db.execute('insert into "'+table+'" ('+','.join('"'+c+'"' for c in row)+') values ('+','.join("?" for _ in row)+')',list(row.values()))
assert data["version"]==20
db.execute("pragma user_version=20");db.commit();db.close()
os.environ["MODEL_MIRROR_CREDENTIAL_MASTER_KEY"]=data.pop("key");del data
auth=secrets.token_urlsafe(48)
(run/"local-auth.json").write_text(json.dumps({"token":auth}),encoding="utf-8")
os.environ["RPG_S2S_TOKEN"]=auth
os.environ["RPG_S2S_ENABLED"]="true";os.environ["RPG_LOCAL_STATE_TOOLS_ENABLED"]="true";os.environ["MODEL_CONTROL_CHAT_ENABLED"]="true"
from isolated_dns import resolve
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router import rpg_bridge
from fastapi import FastAPI
repo=SQLiteRouterRepository.open(dest,recover_chat_control_on_startup=False)
service=ModelRouterService(repository=repo,egress_policy=ProviderEgressPolicy(resolver=resolve,internal_allowlist=[]))
stable=ProviderChatStableService(service)
assert stable.readiness_scoped_certified("google/gemini-3.8-flash")== (True,None)
mock_calls=[]
import httpx
def upstream(request):
 payload=json.loads(request.content)
 assert request.headers["host"]=="openrouter.ai"
 assert payload["model"]=="google/gemini-3.8-flash"
 assert payload["provider"]["only"]==["google-ai-studio"] and payload["provider"]["allow_fallbacks"] is False
 index=len(mock_calls)+1
 assert index<=2,"No additional mock stage allowed"
 if index==1:
  assert payload["tool_choice"]=="auto"
  args=json.dumps({"queries":[{"domain":"items","kind":"丹药"},{"domain":"abilities","kind":"功法"}]},ensure_ascii=False)
  call={"index":0,"id":"isolated-native-query","type":"function","function":{"name":"query_local_state","arguments":args[:20]},"extra_content":{"google":{"thought_signature":"mock-signature-preserve-exactly"}}}
  frames=[{"model":payload["model"],"choices":[{"delta":{"tool_calls":[call]}}]},
   {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":args[20:]}}]},"finish_reason":"tool_calls"}]}]
 else:
  assert payload["tool_choice"]=="none"
  assistant=payload["messages"][-2];tool=payload["messages"][-1]
  assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"]=="mock-signature-preserve-exactly"
  assert tool["role"]=="tool" and tool["tool_call_id"]=="isolated-native-query"
  result=json.loads(tool["content"])
  assert result["stateRevision"]==0 and result["results"][0]["entries"][0]["quantity"]==5
  assert result["results"][1]["entries"][0]["id"]=="breathing"
  reply="你核对了登记的丹药与功法，取出两份养息丹服下。\n<state_delta>"+json.dumps({"operations":[{"type":"quantity","id":"medicine","delta":-2,"reason":"服用两份现有丹药"}]},ensure_ascii=False)+"</state_delta>"
  frames=[{"model":payload["model"],"choices":[{"delta":{"content":reply},"finish_reason":"stop"}]}]
 frames.append({"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}})
 sse="".join("data: "+json.dumps(f,ensure_ascii=False)+"\n\n" for f in frames)+"data: [DONE]\n\n"
 mock_calls.append({"stage":index,"mode":"mock-only","tool_choice":payload["tool_choice"],"messageCount":len(payload["messages"]),"request":payload})
 (run/"mock-outbound.json").write_text(json.dumps(mock_calls,ensure_ascii=False,indent=2),encoding="utf-8")
 return httpx.Response(200,content=sse.encode(),headers={"content-type":"text/event-stream"})
# The network boundary is replaced only by a mock transport. All dispatch guards run unchanged.
stable.transport.client_kwargs=lambda:{"transport":httpx.MockTransport(upstream),"trust_env":False,"follow_redirects":False}
print("Ready: original dispatch guards, mock transport only, zero external model POST",flush=True)
app=FastAPI();app.include_router(rpg_bridge.router);app.dependency_overrides[rpg_bridge.get_stable]=lambda:stable
atexit.register(repo.close)
import uvicorn
uvicorn.run(app,host="127.0.0.1",port=18827,access_log=False)
