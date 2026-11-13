// @vitest-environment-options {"url":"http://127.0.0.1:5173/"}
import {render, screen, within} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import {afterEach, expect, it, vi} from 'vitest';
import type {ReactNode} from 'react';
import RpgPage from './RpgPage';
import {worlds} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
import {localAuthoringHref} from './rpgAuthoringLink';

vi.mock('../components/PageContainer', () => ({default: ({children}: {children: ReactNode}) => <main>{children}</main>}));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each([undefined, '', '0', '65536', '123.4', ' 123', '05', '5/x', '5173', 'http://example.com'])(
  'hides an absent, invalid or self-referencing authoring port %s', port => {
    expect(localAuthoringHref(port, window.location)).toBeUndefined();
  }
);
it.each(['http://example.com', 'https://127.0.0.1', 'https://localhost', 'http://127.0.0.1.evil.example', 'http://localhost.evil.example'])(
  'limits the workbench link to supported HTTP loopback sites, excluding %s', origin => {
    expect(localAuthoringHref('18624', new URL(origin))).toBeUndefined();
  }
);
it.each([
  ['http://localhost:5173/rpg', 'http://127.0.0.1:5173/rpg/authoring'],
  ['http://localhost/rpg', 'http://127.0.0.1/rpg/authoring'],
  ['http://127.0.0.1:5173/rpg/authoring', 'http://127.0.0.1:18624/'],
])('uses a same-site handoff from %s', (url, href) => {
  expect(localAuthoringHref('18624', new URL(url))).toBe(href);
});
it.each([true, false])('keeps the card library and gates the workbench link (enabled %s)', async enabled => {
  vi.stubEnv('VITE_RPG_AUTHORING_PORT', enabled ? '18624' : '');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({cards: [{id: 'god'}]})));
  render(<MemoryRouter><RpgPage/></MemoryRouter>);
  await screen.findByRole('heading', {name: '神临凡尘'});
  expect(screen.getByRole('link', {name: '进入中世纪真实人生'})).toHaveAttribute('href', '/rpg/medieval');
  expect(screen.getByRole('link', {name: '进入斗罗大陆'})).toHaveAttribute('href', '/rpg/douluo');
  expect(screen.getByRole('link', {name: '进入蓝色大肥鱼'})).toHaveAttribute('href', '/rpg/blue-fish');
  expect(screen.getAllByRole('link', {name: /^进入/})).toHaveLength(13);
  expect(screen.getAllByRole('link', {name: /^进入/})[0]).toHaveAttribute('href', '/rpg/elderland');
  expect(screen.getByRole('link', {name: '插件市场'})).toHaveAttribute('href', '/rpg/plugins');
  const link = screen.queryByRole('link', {name: /打开创作工作台/});
  if (enabled) expect(link).toHaveAttribute('href', 'http://127.0.0.1:18624/?library=http%3A%2F%2F127.0.0.1%3A5173%2Frpg');
  else expect(link).toBeNull();
});

it('shows a return link when the workbench launch route is unavailable', () => {
  vi.stubEnv('VITE_RPG_AUTHORING_PORT', '');
  const fetchCards = vi.fn();
  vi.stubGlobal('fetch', fetchCards);
  render(<MemoryRouter initialEntries={['/rpg/authoring']}>
    <Routes><Route path="/rpg/:cardId" element={<RpgPage/>}/></Routes>
  </MemoryRouter>);
  expect(screen.getByRole('status')).toHaveTextContent('此地址未配置创作工作台');
  expect(screen.getByRole('link', {name: '返回故事馆'})).toHaveAttribute('href', '/rpg');
  expect(fetchCards).not.toHaveBeenCalled();
});

it('keeps new releases, existing worlds and saved stories accessible when a host is unavailable', async () => {
  const sessionId='b177c90f-5821-4be7-9313-fd620117ac93';
  vi.stubGlobal('fetch', vi.fn(async (url:string) => {
    if(url.includes('/god/'))throw new Error('unavailable');
    return Response.json(url.endsWith('/sessions')?(url.includes('/dragon-raja/')?[{id:sessionId,name:'林澈的来信',turns:1}]:[]):{budget:{remaining:0}});
  }));
  render(<MemoryRouter><RpgPage/></MemoryRouter>);
  expect(await screen.findByRole('link',{name:/林澈的来信/})).toHaveAttribute('href','/rpg/dragon-raja/chat/'+sessionId);
  const library=within(screen.getByRole('region',{name:'全部作品'}));
  for(const world of worlds)expect(library.getByRole('link',{name:'进入'+world.name})).toHaveAttribute('href','/rpg/'+world.id);
  expect(await screen.findByRole('button',{name:'重新读取'})).toBeEnabled();
});
