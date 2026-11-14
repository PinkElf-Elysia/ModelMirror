import {ChatView} from '../../../experiments/ai-rpg-engine/card-replica/src/chat';
import {useState} from 'react';
import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {useVoiceInput} from '../../../experiments/ai-rpg-engine/card-replica/src/voice-input';
import {appendTranscript,SETTINGS_KEY} from '../../../experiments/ai-rpg-engine/card-replica/src/voice-input-client.mjs';
let stopped:number,posts:number,release:((v:Response)=>void)|undefined;
class Recorder {
 static isTypeSupported(){return true;} state='inactive';mimeType='audio/webm';ondataavailable=(_e:{data:Blob})=>{};onstop=()=>{};onerror=()=>{};
 start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable({data:new Blob(['audio'],{type:this.mimeType})});this.onstop();});}
}
function Harness({scope='test',disabled=false}:{scope?:string;disabled?:boolean}){
 const [draft,setDraft]=useState('原草稿');
 const v=useVoiceInput(scope,disabled,text=>{setDraft(s=>appendTranscript(s,text));return true;});
 return <div className="rpg-chat-workspace"><textarea aria-label="草稿" value={draft} onChange={e=>setDraft(e.target.value)}/>{v.button}{v.toolbar}{v.feedback}{v.modal}<button disabled={v.active}>发送故事</button></div>;
}
beforeEach(()=>{
 localStorage.clear();stopped=0;posts=0;release=undefined;
 HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 vi.stubGlobal('isSecureContext',true);vi.stubGlobal('MediaRecorder',Recorder);
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:vi.fn(async()=>({getTracks:()=>[{stop:()=>stopped++}]}))}});
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('audio/models'))return Response.json({microphone_enabled:true,profiles:[{model_id:'test/stt',display_name:'测试语音',chat_modes:['transcribe'],invocable:true,interaction_status:'ready',input_formats:['webm']}]});
  if(url.includes('provider-workload-control'))return Response.json({feature_enabled:true,status:'managed_required',available:true,certified_input_formats:['webm']});
  if(url==='/api/multimodal/transcriptions'){posts++;return new Promise<Response>(r=>release=r);}
  throw Error('unexpected '+url);
 }));
});
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});
async function record(){fireEvent.click(screen.getByRole('button',{name:'语音输入'}));await screen.findByRole('button',{name:'停止录音并转写'});}
it('one-click records, appends to newest draft, never sends story',async()=>{
 render(<Harness/>);expect(fetch).not.toHaveBeenCalled();await record();expect(screen.getByRole('button',{name:'发送故事'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('草稿'),{target:{value:'录音中修改后的草稿'}});
 fireEvent.click(screen.getByRole('button',{name:'停止录音并转写'}));await waitFor(()=>expect(posts).toBe(1));expect(stopped).toBeGreaterThan(0);
 await act(async()=>release!(Response.json({text:'识别文字',request_id:'receipt'})));
 expect(screen.getByLabelText('草稿')).toHaveValue('录音中修改后的草稿\n识别文字');expect(posts).toBe(1);expect(screen.getByRole('button',{name:'发送故事'})).not.toBeDisabled();
 expect(JSON.parse(localStorage.getItem('rpg-voice-request:test')!)).toMatchObject({text:'识别文字',applied:true});
});
it('cancel releases microphone without audio submission; denied permission preserves draft',async()=>{
 render(<Harness/>);await record();fireEvent.click(screen.getByRole('button',{name:'取消录音'}));await act(async()=>{});expect(stopped).toBeGreaterThan(0);expect(posts).toBe(0);expect(screen.getByLabelText('草稿')).toHaveValue('原草稿');
 vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new DOMException('denied','NotAllowedError'));
 fireEvent.click(screen.getByRole('button',{name:'语音输入'}));await screen.findByText(/未获得麦克风权限/);expect(posts).toBe(0);
});
it('settings model, language and limit persist separately from composer',async()=>{
 render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'语音输入设置'}));await screen.findByRole('option',{name:'测试语音'});
 fireEvent.change(screen.getByLabelText('转写模型'),{target:{value:'test/stt'}});fireEvent.change(screen.getByLabelText('识别语言'),{target:{value:'zh'}});fireEvent.change(screen.getByLabelText('单次录音上限'),{target:{value:'30'}});fireEvent.click(screen.getByRole('button',{name:'保存设置'}));
 expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!)).toMatchObject({model:'test/stt',language:'zh',seconds:30});expect(posts).toBe(0);
});
it('unconfirmed request blocks new paid work until acknowledged',async()=>{
 localStorage.setItem('rpg-voice-request:test',JSON.stringify({id:'old-operation',model:'test/stt',at:new Date().toISOString()}));
 render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'语音输入'}));await screen.findByText('待核对的转写请求');expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();expect(posts).toBe(0);
 fireEvent.click(screen.getByRole('button',{name:'我已核对，允许新录音'}));expect(localStorage.getItem('rpg-voice-request:test')).toBeNull();
});
it('late response after unmount cannot fill another conversation',async()=>{
 const view=render(<Harness/>);await record();fireEvent.click(screen.getByRole('button',{name:'停止录音并转写'}));await waitFor(()=>expect(posts).toBe(1));
 view.unmount();render(<Harness scope="another"/>);await act(async()=>release!(Response.json({text:'迟到回复'})));
 expect(screen.getByLabelText('草稿')).toHaveValue('原草稿');expect(JSON.parse(localStorage.getItem('rpg-voice-request:test')!)).not.toHaveProperty('text');expect(posts).toBe(1);
});
it('chat disabling cancels recording',async()=>{
 const view=render(<Harness/>);await record();view.rerender(<Harness disabled/>);await act(async()=>{});expect(stopped).toBeGreaterThan(0);expect(posts).toBe(0);
});
it('saved transcript can be inserted once and survives remount without duplicate insertion',async()=>{
 localStorage.setItem('rpg-voice-request:test',JSON.stringify({id:'id',model:'test/stt',at:new Date().toISOString(),text:'未插入的文字'}));
 const view=render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'语音输入设置'}));await screen.findByRole('button',{name:'插入草稿'});fireEvent.click(screen.getByRole('button',{name:'插入草稿'}));expect(screen.getByLabelText('草稿')).toHaveValue('原草稿\n未插入的文字');
 view.unmount();render(<Harness/>);expect(screen.getByLabelText('草稿')).toHaveValue('原草稿');expect(posts).toBe(0);
});


it('refuses paid dispatch if request journal cannot be persisted',async()=>{
 render(<Harness/>);await record();vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new DOMException('full','QuotaExceededError');});
 fireEvent.click(screen.getByRole('button',{name:'停止录音并转写'}));await screen.findByText('full');expect(posts).toBe(0);expect(screen.getByLabelText('草稿')).toHaveValue('原草稿');
});

it('shared chat appends speech to its persisted draft without posting a story turn',async()=>{
 const audioFetch=fetch,storyPosts:string[]=[];
 const session={id:'voice-chat',name:'中性会话',revision:0,mode:'offline',characterText:'旅人',turns:[],requests:{}};
 window.history.replaceState({},'', '/rpg-app/god/');
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.startsWith('/api/'))return audioFetch(url,init);
  if(init?.method==='POST')storyPosts.push(url);
  if(url.endsWith('/plugins'))return Response.json({revision:0,plugins:[]});
  if(url.endsWith('/status'))return Response.json({providerEnabled:false,offlineBudget:{remaining:100}});
  if(url.endsWith('/sessions'))return Response.json([]);
  return Response.json(session);
 }));
 render(<ChatView sessionId="voice-chat" exit={()=>{}} opened={()=>{}}/>);
 const mic=screen.getByRole('button',{name:'语音输入'});await waitFor(()=>expect(mic).toBeEnabled());
 fireEvent.change(screen.getByLabelText('对话输入'),{target:{value:'现有文字'}});fireEvent.click(mic);await screen.findByRole('button',{name:'停止录音并转写'});
 fireEvent.click(screen.getByRole('button',{name:'停止录音并转写'}));await waitFor(()=>expect(posts).toBe(1));await act(async()=>release!(Response.json({text:'识别文字'})));
 expect(screen.getByLabelText('对话输入')).toHaveValue('现有文字\n识别文字');expect(JSON.parse(localStorage.getItem('god-chat-v1:voice-chat')!).draft).toBe('现有文字\n识别文字');expect(storyPosts).toEqual([]);
});
