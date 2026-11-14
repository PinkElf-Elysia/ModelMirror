import {SessionRecords} from '../../authoring/session-storage.mjs';
import {digest,requestBody,check} from './contracts.mjs';
// Explicit offline fixture. No network, no character impersonation claim.
export class GroupFixtureProvider {
 #records;get mode(){return 'fixture';}constructor(records){this.#records=records;}
 static async open(directory,{recoverDeadOwner=false}={}){const records=await SessionRecords.open(directory,{scope:'group-fixture-provider',recoverDeadOwner});await records.reconcile();return new GroupFixtureProvider(records);}
 async dispatch(r){check(!(await this.#records.get(r.id)),'FIXTURE_ALREADY_DISPATCHED');const name=r.model.label;const result={format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,modelHash:digest(r.model),status:'completed',dispatched:true,actualModel:r.model.id,content:`【离线测试回复】${name}已收到这次手动发言请求。此内容用于验证消息归属、保存和恢复，不代表角色真实表现。`,partial:'',costMicroUsd:0,evidenceHash:digest({fixture:r.id})};await this.#records.put(r.id,null,result);return result;}
 async lookup(r){return (await this.#records.get(r.id))?.data??null;}
 close(){return this.#records.close();}
}
