// @vitest-environment-options {"url":"http://127.0.0.1:5173/"}
import {render} from '@testing-library/react';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import {afterEach,expect,it,vi} from 'vitest';
import ArchiveCardPage from './ArchiveCardPage';
import {blueFishStorage} from '../../../experiments/ai-rpg-engine/card-replica/src/blue-fish-storage';
afterEach(()=>{localStorage.clear();sessionStorage.clear();vi.restoreAllMocks();});
it('opens the dedicated editor without creating a chat',()=>{
 const request=vi.spyOn(window,'fetch');
 const {container}=render(<MemoryRouter initialEntries={['/rpg/blue-fish/editor']}><Routes><Route path="/rpg/:cardId/editor" element={<ArchiveCardPage card="blue-fish"/>}/></Routes></MemoryRouter>);
 expect(container.querySelector('iframe')).toHaveAttribute('src','/rpg-app/blue-fish/chat/?editor=1');expect(request).not.toHaveBeenCalled();
});
it('isolates editor storage and rejects other frames and oversize messages',()=>{
 const frame=document.createElement('iframe');document.body.appendChild(frame);
 try{
 const post=vi.spyOn(frame.contentWindow!,'postMessage');localStorage.setItem('unrelated','keep');
 const msg=(source:Window|null,origin:string,data:object)=>new MessageEvent('message',{source,origin,data:{type:'blue-fish-editor-v1',...data}});
 expect(blueFishStorage(msg(window,'null',{action:'save',kind:'local',values:{draft:'bad'}}),frame)).toBe(false);
 expect(blueFishStorage(msg(frame.contentWindow,location.origin,{action:'save',kind:'local',values:{draft:'bad'}}),frame)).toBe(false);
 blueFishStorage(msg(frame.contentWindow,'null',{action:'save',kind:'local',values:{draft:'内容'}}),frame);
 expect(localStorage.getItem('blue-fish-editor-v1')).toBe('{"draft":"内容"}');expect(localStorage.getItem('unrelated')).toBe('keep');
 blueFishStorage(msg(frame.contentWindow,'null',{action:'ready'}),frame);
 expect(post).toHaveBeenLastCalledWith({type:'blue-fish-editor-v1',action:'load',snapshot:{local:{draft:'内容'},session:{}}},'*');
 blueFishStorage(msg(frame.contentWindow,'null',{action:'save',kind:'local',values:{draft:'a'.repeat(4000001)}}),frame);
 expect(post).toHaveBeenLastCalledWith({type:'blue-fish-editor-v1',action:'error'},'*');expect(localStorage.getItem('blue-fish-editor-v1')).toContain('内容');
 }finally{frame.remove();}
});

it('downloads text only from the matching editor and rejects unsafe file names',()=>{
 const frame=document.createElement('iframe');document.body.appendChild(frame);
 const create=vi.fn(()=> 'blob:test'),revoke=vi.fn();vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:create,revokeObjectURL:revoke}));vi.useFakeTimers();
 const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 try{
  const msg=(name:string,source:Window|null=frame.contentWindow)=>new MessageEvent('message',{source,origin:'null',data:{type:'blue-fish-editor-v1',action:'download',name,text:'验收正文'}});
  blueFishStorage(msg('safe.md',window),frame);blueFishStorage(msg('../bad.json'),frame);expect(create).not.toHaveBeenCalled();
  blueFishStorage(msg('书店.md'),frame);expect(create).toHaveBeenCalledTimes(1);expect(click).toHaveBeenCalledTimes(1);vi.runAllTimers();expect(revoke).toHaveBeenCalledWith('blob:test');
 }finally{vi.useRealTimers();vi.unstubAllGlobals();frame.remove();}
});
