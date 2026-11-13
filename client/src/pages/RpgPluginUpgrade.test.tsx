import {render,screen,fireEvent,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,it,expect,vi} from 'vitest';
import RpgPluginsPage from './RpgPluginsPage';
import {SessionPlugins} from '../../../experiments/ai-rpg-engine/card-replica/src/session-plugins';
import {pluginFailure} from '../../../experiments/ai-rpg-engine/card-replica/src/plugin-version';
afterEach(()=>vi.unstubAllGlobals());
const old={version:'1.0.0',artifactSha256:'a'.repeat(64),manifestSha256:'b'.repeat(64),installationId:'old-install'};
const plugin={id:'rpg.message-tools',name:'消息工具',version:'1.1.0',installed:true,installedVersion:'1.0.0',installedBinding:old,compatibleInstallation:false,updateAvailable:true,artifactSha256:'c'.repeat(64),manifestSha256:'d'.repeat(64),permissions:['session.message.edit'],versionHistory:[]};
it('shows installed/available versions and confirms upgrade without uninstalling or enabling sessions',async()=>{
 let current=false;const posts:{url:string;body:Record<string,unknown>}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{if(opts?.method==='POST'){posts.push({url,body:JSON.parse(String(opts.body))});current=true;return Response.json({});}return Response.json({revision:8,plugins:[{...plugin,updateAvailable:!current,compatibleInstallation:current,installedVersion:current?'1.1.0':'1.0.0'}]});}));
 render(<MemoryRouter initialEntries={['/rpg/plugins?card=ancient']}><RpgPluginsPage/></MemoryRouter>);
 await screen.findByText(/已安装 1.0.0 · 可用 1.1.0/);fireEvent.click(screen.getByRole('button',{name:'可升级 1'}));fireEvent.click(screen.getByRole('button',{name:'升级插件'}));expect(posts).toHaveLength(0);
 expect(screen.getByRole('region',{name:'确认升级'})).toHaveTextContent('编辑已保存的回复');fireEvent.click(screen.getByRole('button',{name:'确认升级并保留配置'}));await screen.findByText(/升级完成/);
 expect(posts).toHaveLength(1);expect(posts[0].url).toBe('/rpg-app/ancient/chat/api/plugins/rpg.message-tools/upgrade');expect(posts[0].body).toMatchObject({expectedInstalled:old,permissions:plugin.permissions,expectedRegistryRevision:8,version:'1.1.0'});expect(posts[0].body).not.toHaveProperty('sessionId');
 expect(screen.getByText('已安装插件均为当前版本。')).toBeInTheDocument();
});
it('uninstalls mismatched packages with installed hashes rather than current release hashes',async()=>{
 const requests:Record<string,unknown>[]=[];vi.stubGlobal('fetch',vi.fn(async(_url:string,opts?:RequestInit)=>{if(opts?.method==='POST'){requests.push(JSON.parse(String(opts.body)));return Response.json({});}return Response.json({revision:8,plugins:[plugin]});}));render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button',{name:'卸载插件'}));await screen.findByText(/已卸载/);expect(requests[0]).toMatchObject({version:old.version,artifactSha256:old.artifactSha256,manifestSha256:old.manifestSha256});
});
it('reports a busy upgrade precisely and never automatically retries writes',async()=>{
 let posts=0;vi.stubGlobal('fetch',vi.fn(async(_url:string,opts?:RequestInit)=>{if(opts?.method==='POST'){posts++;return Response.json({error:'PLUGIN_UPGRADE_BUSY'},{status:409});}return Response.json({revision:8,plugins:[plugin]});}));render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button',{name:'升级插件'}));fireEvent.click(screen.getByRole('button',{name:'确认升级并保留配置'}));expect(await screen.findByRole('alert')).toHaveTextContent('PLUGIN_UPGRADE_BUSY');fireEvent.click(screen.getByRole('button',{name:'重新读取'}));await waitFor(()=>expect(screen.queryByRole('alert')).toBeNull());expect(posts).toBe(1);
});
it('session setting directs incompatible installations to upgrade instead of a failing toggle',()=>{
 const change=vi.fn();render(<SessionPlugins rows={[{id:plugin.id,name:plugin.name,enabled:false,installed:true,known:true,supported:true,locked:false,upgradeRequired:true,version:'1.1.0',installedVersion:'1.0.0'}]} change={change} open={()=>{}} market="/rpg/plugins?card=ancient"/>);
 const row=screen.getByRole('article',{name:'消息工具'});expect(within(row).getByRole('link',{name:'去升级 ↗'})).toHaveAttribute('href','/rpg/plugins?card=ancient');expect(within(row).queryByRole('switch')).toBeNull();expect(change).not.toHaveBeenCalled();
});
it('safe error mapping distinguishes conflicts and does not expose arbitrary server details',()=>{
 expect(pluginFailure(Error('PLUGIN_SESSION_CONFLICT'))).toContain('会话状态已变化');expect(pluginFailure(Error('PLUGIN_UPGRADE_REQUIRED'))).toContain('插件市场升级');expect(pluginFailure(Error('private stack or credential'))).not.toContain('private');
});
