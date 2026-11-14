import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import VoiceLibraryControl from './VoiceLibraryControl';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('reads central policy and saves model/budget selection without a synthesis call',async()=>{
 const policy={revision:2,enabled:false,featureEnabled:false,connectionId:'cloud',modelIds:['cosyvoice-v3-plus'],supportedModels:['cosyvoice-v3-plus'],limit:8,spent:3};
 const calls:{url:string;init?:RequestInit}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  calls.push({url,init});
  if(url.endsWith('/connections'))return Response.json([{id:'cloud',name:'测试音频连接',scopes:['audio'],enabled:true},{id:'text',name:'文本连接',scopes:['chat'],enabled:true}]);
  return Response.json(policy);
 }));
 render(<VoiceLibraryControl csrfToken="synthetic-csrf"/>);
 expect(await screen.findByText('服务器尚未开放音色库真实调用。可以保存路由设置，创建、云端状态查询、删除和试听仍会被阻止。')).toBeTruthy();
 expect(screen.queryByRole('option',{name:'文本连接'})).toBeNull();
 fireEvent.click(screen.getByLabelText('允许音色库使用此路由'));
 fireEvent.click(screen.getByRole('button',{name:'保存音色库路由'}));
 await waitFor(()=>expect(calls.some(c=>c.init?.method==='PUT')).toBe(true));
 const write=calls.find(c=>c.init?.method==='PUT')!;
 expect(write.url).toBe('/api/router/voice-library/control');
 expect(write.init?.headers).toEqual({'Content-Type':'application/json','X-ModelMirror-CSRF':'synthetic-csrf'});
 expect(JSON.parse(String(write.init?.body))).toMatchObject({expectedRevision:2,enabled:true,connectionId:'cloud',modelIds:['cosyvoice-v3-plus'],limit:8});
 expect(calls.every(c=>c.url.startsWith('/api/router/'))).toBe(true);
});
