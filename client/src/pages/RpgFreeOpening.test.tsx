// @vitest-environment-options {"url":"http://127.0.0.1:5173/"}
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {createChatDraft} from '../../../experiments/ai-rpg-engine/card-replica/src/start-chat';
import {ChatView} from '../../../experiments/ai-rpg-engine/card-replica/src/chat';
import {readClientState} from '../../../experiments/ai-rpg-engine/card-replica/src/session-client';
const cards=['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','blue-fish','douluo'];
afterEach(()=>{vi.unstubAllGlobals();localStorage.clear();window.history.replaceState({},'','/');});
function host(card:string){
 const root='/rpg-app/'+card+(['ancient','cultivation','lord-of-mysteries','blue-fish','douluo'].includes(card)?'/chat/':'/');
 window.history.replaceState({},'',root);
 const session={id:'00000000-0000-4000-8000-000000000001',name:'新故事',characterText:'',mode:'offline',revision:0,runtime:{compatible:true},turns:[],requests:{}};
 const writes:{path:string;body:any}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  const body=options?.body?JSON.parse(String(options.body)):null;
  if(body){writes.push({path,body});if(path.endsWith('/send'))return Response.json({...session,revision:1,turns:[{requestId:body.requestId,input:body.input,raw:'已保存的剧情',html:'已保存的剧情'}],requests:{[body.requestId]:{status:'complete'}}});}
  if(path.endsWith('/status'))return Response.json({providerEnabled:false,budget:{remaining:100},offlineBudget:{remaining:100}});
  if(path.endsWith('/plugins'))return Response.json({revision:0,plugins:[]});
  if(path.endsWith('/sessions'))return Response.json(body?session:[]);
  return Response.json(session);
 }));
 return {root,session,writes};
}
it.each(cards)('%s enters with no bound archive and no model request',async card=>{
 const {root,session,writes}=host(card);
 expect(await createChatDraft()).toBe(session.id);
 expect(writes).toEqual([{path:root+'api/sessions',body:card==='earth'?{characterText:'',world:'表世界',params:{},mode:'offline'}:card==='god'?{setup:null,mode:'offline'}:{opening:''}}]);
 expect(readClientState(session.id).draft).toBe('');
});
it.each(cards)('%s keeps the optional archive editable and sends only the final composer text',async card=>{
 const {session,writes}=host(card);await createChatDraft('原档案，仅作为草稿');
 render(<ChatView sessionId={session.id} exit={()=>{}} opened={()=>{}}/>);
 const input=await screen.findByRole('textbox',{name:'对话输入'});
 await waitFor(()=>expect(screen.getByRole('button',{name:/^发送$/})).toBeEnabled());
 expect(input).toHaveValue('原档案，仅作为草稿');expect(input).not.toHaveAttribute('readonly');
 expect(screen.queryByText('开局角色资料')).not.toBeInTheDocument();
 fireEvent.change(input,{target:{value:''}});expect(readClientState(session.id).draft).toBe('');
 fireEvent.change(input,{target:{value:'完全不同的开场'}});fireEvent.click(screen.getByRole('button',{name:/^发送$/}));
 await waitFor(()=>expect(writes.filter(x=>x.path.endsWith('/send'))).toHaveLength(1));
 expect(writes.at(-1)!.body.input).toBe('完全不同的开场');
 await waitFor(()=>expect(input).toHaveValue(''));
});
it('rejects an oversized draft before creating any session',async()=>{
 const {writes}=host('earth');await expect(createChatDraft('字'.repeat(20001))).rejects.toThrow('超过 20000');expect(writes).toHaveLength(0);
});
