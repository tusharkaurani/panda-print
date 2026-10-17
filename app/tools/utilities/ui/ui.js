// Print Utilities: cards that open free online tools, grouped by category (at most four cards per row).
import { art } from './art.js';

export async function mount(root, kit) {
  const { h, icon, ui, api } = kit;
  root.classList.add('pu');
  const page = h('div', { class: 'pu-page' });
  root.append(page);

  function card(tool) {
    const thumb = h('span', { class: 'pu-thumb', 'aria-hidden': 'true' }, art(kit, tool.art));
    if (tool.image) {  // an image URL from the user's own list; the drawing stays if it can't load
      const img = h('img', { src: tool.image, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer', onload: () => thumb.replaceChildren(img), onerror: () => img.remove() });
    }
    return h('a', { class: 'pu-card', href: tool.url, target: '_blank', rel: 'noopener noreferrer' },
      thumb,
      h('span', { class: 'pu-name' }, tool.name, icon('external', 14)),
      tool.description && h('span', { class: 'pu-desc' }, tool.description),
      h('span', { class: 'pu-host' }, tool.host));
  }

  async function load() {
    page.replaceChildren(ui.skeleton());
    const res = await api('/utilities/catalog');
    if (!res.ok) { page.replaceChildren(ui.errorBox(res.error, load)); return; }
    const { categories, warnings, error, source } = res.data;
    page.replaceChildren(...[
      error && h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, error)),
      warnings?.length > 0 && h('details', { class: 'pu-notes' }, h('summary', {}, `${warnings.length} note${warnings.length === 1 ? '' : 's'} about print-utilities.yml`),
        h('ul', {}, warnings.map((w) => h('li', {}, w)))),
      h('p', { class: 'pu-intro' }, 'Free online tools for things worth printing. Each opens in a new tab on the tool’s own site; some have paid extras.',
        source === 'custom' ? ' This is your own list (print-utilities.yml).' : ''),
      ...categories.map((c) => h('section', { class: 'pu-cat', 'aria-labelledby': `pu-${c.id}` },
        h('div', { class: 'pu-head' }, h('h2', { id: `pu-${c.id}` }, c.name), c.description && h('p', {}, c.description)),
        h('div', { class: 'pu-grid' }, c.tools.map(card)))),
    ].filter(Boolean));
  }
  await load();
}
