// @vitest-environment-options {"url":"http://127.0.0.1:5173/"}
import {render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, expect, it, vi} from 'vitest';
import type {ReactNode} from 'react';
import RpgPage from './RpgPage';
import {localAuthoringHref} from './rpgAuthoringLink';

vi.mock('../components/PageContainer', () => ({default: ({children}: {children: ReactNode}) => <main>{children}</main>}));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each([undefined, '', '0', '65536', '123.4', ' 123', '05', '5/x', '5173', 'http://example.com'])(
  'hides an absent, invalid or self-referencing authoring port %s', port => {
    expect(localAuthoringHref(port, window.location)).toBeUndefined();
  }
);
it.each(['http://example.com', 'https://127.0.0.1', 'http://localhost', 'http://127.0.0.1.evil.example'])(
  'requires the canonical local site before linking from %s', origin => {
    expect(localAuthoringHref('18624', new URL(origin))).toBeUndefined();
  }
);
it.each([true, false])('keeps the card library and gates the workbench link (enabled %s)', async enabled => {
  vi.stubEnv('VITE_RPG_AUTHORING_PORT', enabled ? '18624' : '');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({cards: [{id: 'god'}]})));
  render(<MemoryRouter><RpgPage/></MemoryRouter>);
  await screen.findByRole('heading', {name: '神临凡尘 · 上帝模拟器'});
  expect(screen.getByRole('link', {name: '插件市场'})).toHaveAttribute('href', '/rpg/plugins');
  const link = screen.queryByRole('link', {name: '创作工作台'});
  if (enabled) expect(link).toHaveAttribute('href', 'http://127.0.0.1:18624/');
  else expect(link).toBeNull();
});
