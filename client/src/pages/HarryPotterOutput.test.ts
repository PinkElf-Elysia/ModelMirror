// @vitest-environment node
import {expect, it} from 'vitest';
import {JSDOM} from 'jsdom';
import {streamPresentation} from '../../../experiments/ai-rpg-engine/card-replica/lib/stream-presentation.mjs';
import {patchStreamHtml} from '../../../experiments/ai-rpg-engine/card-replica/src/stream-dom.mjs';

it('defaults panels closed while preserving user toggles across incremental rendering', () => {
  const page = new JSDOM('<main></main>');
  try {
    const root = page.window.document.querySelector('main')!;
    const partial = '<details open><summary>行囊</summary><p>23 加隆';
    patchStreamHtml(root, streamPresentation(partial, 'harry-potter'));
    const panel = root.querySelector('details')!;
    expect(panel.open).toBe(false);
    panel.querySelector('summary')!.click();
    expect(panel.open).toBe(true);
    patchStreamHtml(root, streamPresentation(partial + '，蓝色信封</p></details>', 'harry-potter'));
    expect(root.querySelector('details')).toBe(panel);
    expect(panel.open).toBe(true);
    expect(panel.textContent).toContain('23 加隆，蓝色信封');
    panel.querySelector('summary')!.click();
    patchStreamHtml(root, streamPresentation(partial + '，蓝色信封</p></details><p>继续</p>', 'harry-potter'));
    expect(panel.open).toBe(false);
  } finally { page.window.close(); }
});
