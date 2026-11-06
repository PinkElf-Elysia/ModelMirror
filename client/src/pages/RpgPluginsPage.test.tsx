import {render,screen,fireEvent,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,it,expect,vi} from 'vitest';
import RpgPluginsPage from './RpgPluginsPage';
import RpgPage from './RpgPage';

afterEach(()=>vi.unstubAllGlobals());
const plugin={id:'rpg.branch-save',name:'分支存档',version:'1.0.0',installed:false,compatibleInstallation:false,artifactSha256:'a'.repeat(64),manifestSha256:'b'.repeat(64),permissions:['session.completed.read','session.branch.prepare','ui.contribute'],source:'reviewed first-party',license:'UNLICENSED'};
it('installs and uninstalls with the registry identity, without enabling or deleting any conversation',async()=>{
 let installed=false;const posts:Record<string,unknown>[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  if(options?.method==='POST'){posts.push(JSON.parse(String(options.body)));installed=url.endsWith('/install');return Response.json({});}
  return Response.json({revision:installed?1:0,plugins:[{...plugin,installed}]});
 }));
 render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button',{name:'安装插件'}));
 await screen.findByRole('button',{name:'卸载插件'});
 expect(posts).toHaveLength(1);
 expect(posts[0]).toMatchObject({pluginId:plugin.id,version:plugin.version,artifactSha256:plugin.artifactSha256,manifestSha256:plugin.manifestSha256,expectedRegistryRevision:0});
 expect(posts[0].operationId).toEqual(expect.any(String));expect(posts[0]).not.toHaveProperty('sessionId');
 fireEvent.click(screen.getByRole('button',{name:'已安装 1'}));fireEvent.click(screen.getByRole('button',{name:'卸载插件'}));
 await screen.findByText(/这个世界还没有安装插件/);
 expect(posts).toHaveLength(2);expect(posts[1].expectedRegistryRevision).toBe(1);
 expect(screen.getByRole('status')).toHaveTextContent('已有内容和设置保留');
});
it('failed reads never automatically retry',async()=>{
 const fetcher=vi.fn().mockRejectedValue(Error('offline'));vi.stubGlobal('fetch',fetcher);
 render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);await screen.findByRole('alert');expect(fetcher).toHaveBeenCalledTimes(1);
 fireEvent.click(screen.getByRole('button',{name:'重新读取'}));await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));
});
it('shows eight worlds even when the status service fails, and does not dispatch a model',async()=>{
 const fetcher=vi.fn().mockRejectedValue(Error('offline'));vi.stubGlobal('fetch',fetcher);render(<MemoryRouter><RpgPage/></MemoryRouter>);
 for(const title of ['蓝色大肥鱼','斗罗大陆','诡秘之主','古代人生','地球 OL','神临凡尘','超级修仙','行间'])expect(screen.getByRole('heading',{name:title})).toBeInTheDocument();
 for(const [card,title] of [['blue-fish','蓝色大肥鱼'],['douluo','斗罗大陆']])expect(screen.getByRole('link',{name:'进入'+title})).toHaveAttribute('href','/rpg/'+card);
 expect(screen.getByRole('link',{name:'插件市场'})).toHaveAttribute('href','/rpg/plugins');
 await screen.findByText(/部分存档暂时没有读到/);expect(fetcher).toHaveBeenCalledTimes(16);
 expect(fetcher.mock.calls.every(([,options])=>!options.method)).toBe(true);
 expect(document.body).not.toHaveTextContent(/验收|复刻|本轮剩余|行间暂不兼容/);
});
it.each([
 ['douluo','/rpg-app/douluo/chat/api/plugins'],['blue-fish','/rpg-app/blue-fish/chat/api/plugins'],['earth','/rpg-app/api/plugins'],['god','/rpg-app/api/plugins'],['ancient','/rpg-app/ancient/chat/api/plugins'],
 ['cultivation','/rpg-app/cultivation/chat/api/plugins'],['lord-of-mysteries','/rpg-app/lord-of-mysteries/chat/api/plugins'],['rpg05','/rpg-app/xingjian/api/plugins'],
])('reads and changes only the selected %s installation registry',async(card,root)=>{
 const fetcher=vi.fn(async()=>Response.json({revision:8,plugins:[plugin]}));vi.stubGlobal('fetch',fetcher);
 render(<MemoryRouter initialEntries={['/rpg/plugins?card='+card]}><RpgPluginsPage/></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button',{name:'安装插件'}));await screen.findByText(/安装完成/);
 expect(fetcher).toHaveBeenCalledWith(root,expect.objectContaining({signal:expect.any(AbortSignal)}));
 expect(fetcher).toHaveBeenCalledWith(root+'/rpg.branch-save/install',expect.objectContaining({method:'POST',body:expect.stringContaining('"expectedRegistryRevision":8')}));
});
it('discloses distinct usage and fees for all eight plugins without obsolete compatibility claims',async()=>{
 const items=['model-selector','branch-save','message-tools','history-window','rolling-summary','memory-palace','illustrations','transcript'];
 vi.stubGlobal('fetch',vi.fn(async()=>Response.json({revision:0,plugins:items.map(id=>({...plugin,id:'rpg.'+id}))})));
 render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 const summary=await screen.findByRole('article',{name:'自动总结 2.0'});
 expect(within(summary).getByText('每轮小结 1 次，合并时另 1 次')).toBeInTheDocument();
 expect(within(screen.getByRole('article',{name:'记忆宫殿'})).getByText('回复后额外 1 次记忆调用')).toBeInTheDocument();
 expect(within(screen.getByRole('article',{name:'场景插画'})).getByText('主动改写或生成时计费')).toBeInTheDocument();
 expect(within(screen.getByRole('article',{name:'聊天记录'})).getByText(/导入会创建独立会话/)).toBeInTheDocument();
 expect(within(screen.getByRole('article',{name:'模型选择'})).getByText('选择不调用模型')).toBeInTheDocument();
 expect(within(screen.getByRole('article',{name:'历史窗口'})).getByText(/完整记录仍然保留/)).toBeInTheDocument();
 expect(screen.getAllByRole('article')).toHaveLength(8);
 expect(document.body).not.toHaveTextContent(/M3|行间暂不兼容|审核目录|宿主提供/);
});
it('ignores a stale catalog when the user changes worlds',async()=>{
 let resolveOld!:(value:Response)=>void;
 vi.stubGlobal('fetch',vi.fn((url:string)=>url==='/rpg-app/api/plugins'?new Promise<Response>(resolve=>{resolveOld=resolve;}):Promise.resolve(Response.json({revision:4,plugins:[{...plugin,id:'rpg.memory-palace'}]}))));
 render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 fireEvent.change(screen.getByRole('combobox'),{target:{value:'ancient'}});
 await screen.findByRole('article',{name:'记忆宫殿'});resolveOld(Response.json({revision:1,plugins:[plugin]}));
 await waitFor(()=>expect(screen.queryByRole('article',{name:'分支存档'})).toBeNull());
});
