import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {SummaryV2Chat} from './CultivationChat';
import {summaryApi, summaryPath, type SummarySession} from './rpgSummaryV2';

const id = 'c9fcc1d8-7ca0-4559-8864-5d7994c65ada';
const model = 'google/gemini-3.8-flash';
const base = summaryApi('earth');
const opening: SummarySession = {
  id, revision: 0, sequence: 0, enabled: false, blocked: false, model,
  memory: false, entryCount: 0, large: '', covered: 0, turns: [],
};

function renderChat() {
  render(<MemoryRouter initialEntries={[summaryPath('earth', id)]}>
    <Routes><Route path="/rpg/:cardId/chat/summary/:id" element={<SummaryV2Chat/>}/></Routes>
  </MemoryRouter>);
}

function withSession(initial: SummarySession, updated: SummarySession) {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === base + 'models') return Response.json([{name: 'Gemini', model}]);
    if (url !== base + 'sessions/' + id) throw Error('Unexpected URL');
    return Response.json(options?.method === 'POST' ? updated : initial);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

const dialogMethods = ['showModal', 'close'] as const;
const originalMethods = dialogMethods.map(name => Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name));

beforeEach(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {configurable: true, value(this: HTMLDialogElement) {this.open = true;}},
    close: {configurable: true, value(this: HTMLDialogElement) {this.open = false;}},
  });
});

afterEach(() => {
  dialogMethods.forEach((name, index) => {
    const original = originalMethods[index];
    if (original) Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  });
  vi.unstubAllGlobals();
  localStorage.clear();
});

it('opens v2 in the chat, links to the supplied help, and enables summaries only on save', async () => {
  const fetcher = withSession(opening, {...opening, enabled: true, revision: 1});
  renderChat();
  fireEvent.click(await screen.findByRole('button', {name: '自动总结', exact: true}));
  const modal = within(await screen.findByRole('dialog', {name: '自动总结'}));
  expect(modal.getByRole('link', {name: '帮助'})).toHaveAttribute('href', 'https://afengy.cash/docs/auto-summary.html?lang=zh-Hans');
  expect(modal.getByRole('switch')).not.toBeChecked();
  expect(modal.getByText('暂无小总结内容')).toBeVisible();
  expect(modal.getByRole('button', {name: '重新生成大总结'})).toBeDisabled();
  expect(modal.getByRole('button', {name: '保存', exact: true})).toBeDisabled();
  fireEvent.click(modal.getByRole('switch'));
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  fireEvent.click(modal.getByRole('button', {name: '保存', exact: true}));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === 'POST');
  expect(JSON.parse(String(write?.[1]?.body))).toEqual({revision: 0, action: 'settings', enabled: true, model});
});

it('shows the latest five small summaries and sends an edited large summary with settings', async () => {
  const populated: SummarySession = {
    ...opening, revision: 8, sequence: 6, enabled: true, large: '原大总结', covered: 4,
    turns: Array.from({length: 6}, (_, index) => ({
      id: index + 1, input: '行动', raw: '剧情', small: '小结记录 ' + (index + 1),
    })),
  };
  const fetcher = withSession(populated, {...populated, large: '已编辑的大总结', revision: 9});
  renderChat();
  fireEvent.click(await screen.findByRole('button', {name: '自动总结', exact: true}));
  const modal = within(await screen.findByRole('dialog', {name: '自动总结'}));
  expect(modal.queryByText('小结记录 1')).not.toBeInTheDocument();
  expect(modal.getByRole('list')).toBeVisible();
  expect(modal.getAllByRole('listitem')).toHaveLength(5);
  fireEvent.click(modal.getAllByRole('button', {name: '展开', exact: true})[0]);
  expect(modal.getByText('小结记录 2')).toBeVisible();
  fireEvent.change(modal.getByRole('textbox', {name: '大总结内容'}), {target: {value: '已编辑的大总结'}});
  fireEvent.click(modal.getByRole('button', {name: '保存', exact: true}));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === 'POST');
  expect(JSON.parse(String(write?.[1]?.body))).toEqual({
    revision: 8, action: 'settings', enabled: true, model, large: '已编辑的大总结',
  });
});

it('returns an old summary creation URL to the normal card instead of opening a separate lobby', async () => {
  render(<MemoryRouter initialEntries={[summaryPath('earth')]}><Routes>
    <Route path="/rpg/:cardId/chat/summary/:id" element={<SummaryV2Chat/>}/>
    <Route path="/rpg/earth" element={<h1>地球 OL 角色与会话</h1>}/>
  </Routes></MemoryRouter>);
  expect(await screen.findByRole('heading', {name:'地球 OL 角色与会话'})).toBeVisible();
});
