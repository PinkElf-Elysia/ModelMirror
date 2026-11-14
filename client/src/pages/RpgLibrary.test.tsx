// @vitest-environment-options {"url":"http://127.0.0.1:18483/"}
import {render,screen,fireEvent,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,it,expect,vi} from 'vitest';
import RpgPage from './RpgPage';
import {readPublishedWorks,workbenchHref} from './rpgLibrary';
const work={id:'work-'+'a'.repeat(64),versionId:'version-'+'b'.repeat(64),versionNumber:1,publishedAt:'2026-11-06T10:00:00Z',name:'雾港来信',description:'从一封来信开始。',tags:['本地冒险'],cover:null};
const catalog={format:'modelmirror.rpg.library/1',works:[work]};
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs();});
it('automatically lists published works, filters and refreshes without creating sessions or duplicate cards',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','18484');let current=catalog;
 const fetcher=vi.fn(async(url:string,_options?:RequestInit)=>Response.json(url.endsWith('/library')?current:url.endsWith('/sessions')?[]:{}));vi.stubGlobal('fetch',fetcher);
 render(<MemoryRouter><RpgPage/></MemoryRouter>);
 const entry=await screen.findByRole('article',{name:'雾港来信'});
 expect(entry).toHaveClass('rpg-story-card');expect(within(entry).getByRole('link')).toHaveAttribute('href',workbenchHref('18484',window.location,work));
 expect(screen.getAllByRole('article')).toHaveLength(15);
 fireEvent.change(screen.getByRole('searchbox',{name:'搜索作品'}),{target:{value:'本地冒险'}});expect(screen.getAllByRole('article')).toHaveLength(1);
 fireEvent.change(screen.getByRole('searchbox',{name:'搜索作品'}),{target:{value:''}});
 fireEvent.change(screen.getByRole('combobox',{name:'来源'}),{target:{value:'published'}});expect(screen.getAllByRole('article')).toHaveLength(1);
 current={...catalog,works:[{...work,versionId:'version-'+'c'.repeat(64),versionNumber:2,name:'雾港来信 · 第二版'}]};
 fireEvent(window,new Event('focus'));
 await screen.findByRole('article',{name:'雾港来信 · 第二版'});expect(screen.queryByRole('article',{name:'雾港来信'})).toBeNull();expect(screen.getAllByRole('article')).toHaveLength(1);
 expect(fetcher.mock.calls.every(call=>!(call[1] as RequestInit|undefined)?.method)).toBe(true);
});
it('failed refresh preserves the last published entries and a manual refresh can recover',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','18484');let failed=false;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{if(url.endsWith('/library')){if(failed)throw Error();return Response.json(catalog);}return Response.json([]);}));
 render(<MemoryRouter><RpgPage/></MemoryRouter>);await screen.findByRole('article',{name:work.name});failed=true;
 fireEvent.click(screen.getByRole('button',{name:'刷新作品'}));await screen.findByText(/本地作品暂时无法更新/);expect(screen.getByRole('article',{name:work.name})).toBeInTheDocument();
 failed=false;fireEvent.click(screen.getByRole('button',{name:'刷新作品'}));await waitFor(()=>expect(screen.queryByText(/本地作品暂时无法更新/)).toBeNull());
});
it('rejects external cover URLs, malformed identities and duplicate publications',()=>{
 for(const works of [[{...work,cover:'https://example.com/tracking.png'}],[{...work,id:'../private'}],[work,work]])expect(()=>readPublishedWorks({...catalog,works},'18484',window.location)).toThrow();
 expect(workbenchHref('18484',new URL('http://localhost:18483'),work)).toContain('http://127.0.0.1:18484/play?');
 expect(workbenchHref('18484',new URL('https://example.org'),work)).toBeUndefined();
});
it('unavailable artwork uses the same placeholder as a work with no cover',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','');vi.stubGlobal('fetch',vi.fn(async()=>Response.json([])));
 render(<MemoryRouter><RpgPage/></MemoryRouter>);const card=screen.getByRole('article',{name:'金庸大世界'});
 const img=card.querySelector('img')!;fireEvent.error(img);expect(card.querySelector('img')).toBeNull();expect(card.querySelector('.rpg-story-art')).toHaveTextContent('金庸大世界');
 await screen.findByRole('article',{name:'地球online'});
});

it('keeps a published card in its default position while updating its version badge and URL',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','18484');
 const second={...work,id:'work-'+'c'.repeat(64),name:'另一部作品',publishedAt:'2026-11-07T10:00:00Z'};
 let current={...catalog,works:[second,work]};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json(url.endsWith('/library')?current:[])));
 render(<MemoryRouter><RpgPage/></MemoryRouter>);
 const first=await screen.findByRole('article',{name:work.name});
 expect(screen.getAllByRole('article')[0]).toBe(first);
 expect(within(first).getByText('我的作品 · v1')).toBeInTheDocument();
 expect(within(first).getByRole('link')).toContainElement(within(first).getByRole('heading'));
 current={...catalog,works:[{...work,versionId:'version-'+'d'.repeat(64),versionNumber:2,publishedAt:'2026-11-13T10:00:00Z'},second]};
 fireEvent.click(screen.getByRole('button',{name:'刷新作品'}));
 await within(first).findByText('我的作品 · v2');
 expect(screen.getAllByRole('article')[0]).toBe(first);
 expect(within(first).getByRole('link')).toHaveAttribute('href',expect.stringContaining('version-'+ 'd'.repeat(64)));
 expect(screen.getByRole('link',{name:/新建作品/})).toHaveAttribute('href',workbenchHref('18484',window.location));
});
it('consolidates failed archive reads before the cards without repeating warnings inside cards',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','');vi.stubGlobal('fetch',vi.fn().mockRejectedValue(Error('offline')));
 render(<MemoryRouter><RpgPage/></MemoryRouter>);
 const warning=await screen.findByText(/部分存档暂时读不到/);
 expect(screen.getAllByText(/部分存档暂时读不到/)).toHaveLength(1);
 expect(warning.compareDocumentPosition(screen.getAllByRole('article')[0])&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 for(const card of screen.getAllByRole('article'))expect(card).not.toHaveTextContent(/暂不可用|读取中|打开作品/);
 expect(screen.queryByText('下一段人生，从这里开始。')).toBeNull();
});

it('uses the supplied Harry Potter cover in the library while retaining the card entry',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','');const fetcher=vi.fn(async(_url:string,_options?:RequestInit)=>Response.json([]));vi.stubGlobal('fetch',fetcher);
 render(<MemoryRouter><RpgPage/></MemoryRouter>);
 const card=screen.getByRole('article',{name:'哈利波特大世界（霍格沃兹）'});
 expect(card.querySelector('img')).toHaveAttribute('src','/rpg-covers/harry-potter-user.png');
 expect(card.querySelector('img')).toHaveStyle({objectPosition:'50% 0%'});
 expect(within(card).getByRole('link')).toHaveAttribute('href','/rpg/harry-potter');
 await screen.findByRole('article',{name:'地球online'});
 expect(fetcher.mock.calls.every(call=>!call[1]?.method)).toBe(true);
});

it('keeps legacy worlds and filters character publications without losing their fixed version link',async()=>{
 vi.stubEnv('VITE_RPG_AUTHORING_PORT','18484');
 const character={...work,id:'work-'+'e'.repeat(64),name:'测试夹具角色',kind:'character'};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json(url.endsWith('/library')?{...catalog,works:[work,character]}:[])));
 render(<MemoryRouter><RpgPage/></MemoryRouter>);await screen.findByRole('article',{name:character.name});
 fireEvent.change(screen.getByRole('combobox',{name:'作品类型'}),{target:{value:'character'}});
 expect(screen.getAllByRole('article')).toHaveLength(1);expect(screen.getByRole('article',{name:character.name})).toHaveTextContent('单角色卡');
 expect(within(screen.getByRole('article',{name:character.name})).getByRole('link')).toHaveAttribute('href',expect.stringContaining(character.versionId));
 fireEvent.change(screen.getByRole('combobox',{name:'作品类型'}),{target:{value:'world'}});expect(screen.queryByRole('article',{name:character.name})).toBeNull();expect(screen.getByRole('article',{name:work.name})).toBeInTheDocument();
});
