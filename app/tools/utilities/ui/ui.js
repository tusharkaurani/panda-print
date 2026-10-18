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
      h('span', { class: 'pu-host' }, h('span', { class: 'pu-tag ext' }, 'Other site'), tool.host));
  }

  // tools that live inside this page (they declare parent: "utilities"): they open in the app, not in a new tab
  const BUILT_IN_ART = { photosheet: 'passport', labels: 'labels' };
  function builtIn(t) {
    return h('a', { class: 'pu-card', href: `#${t.id}`, title: `Open ${t.title}` },
      h('span', { class: 'pu-thumb', 'aria-hidden': 'true' }, art(kit, BUILT_IN_ART[t.id] || 'document')),
      h('span', { class: 'pu-name' }, t.title),
      t.description && h('span', { class: 'pu-desc' }, t.description),
      h('span', { class: 'pu-host' }, h('span', { class: 'pu-tag own' }, icon('check', 12), 'Built in')));
  }

  async function load() {
    page.replaceChildren(ui.skeleton());
    const [res, toolsRes] = await Promise.all([api('/utilities/catalog'), api('/tools')]);
    if (!res.ok) { page.replaceChildren(ui.errorBox(res.error, load)); return; }
    const { categories, warnings, error, source } = res.data;
    const inside = (toolsRes.ok ? toolsRes.data.tools || [] : []).filter((t) => t.parent === 'utilities' && !t.error);
    page.replaceChildren(...[
      error && h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, error)),
      warnings?.length > 0 && h('details', { class: 'pu-notes' }, h('summary', {}, `${warnings.length} note${warnings.length === 1 ? '' : 's'} about print-utilities.yml`),
        h('ul', {}, warnings.map((w) => h('li', {}, w)))),
      inside.length > 0 && h('section', { class: 'pu-zone own', 'aria-labelledby': 'pu-builtin' },
        h('div', { class: 'pu-zone-head' },
          h('span', { class: 'pu-seal', 'aria-hidden': 'true' }, h('img', { src: '/static/favicon.svg', alt: '', width: 40, height: 40 }), h('span', { class: 'pu-seal-check' }, icon('check', 12))),
          h('div', {}, h('h2', { id: 'pu-builtin' }, 'Built in'), h('p', {}, 'Runs inside panda-print. Nothing is sent to another site.'))),
        h('div', { class: 'pu-grid' }, inside.map(builtIn))),
      categories.length > 0 && h('section', { class: 'pu-zone ext', 'aria-labelledby': 'pu-ext' },
        h('div', { class: 'pu-zone-head' }, icon('external', 20),
          h('div', {}, h('h2', { id: 'pu-ext' }, 'Free online tools'), h('p', {}, 'Other websites. Each card opens in a new tab.'))),
        ...categories.map((c, i) => h('section', { class: `pu-cat hue-${i % 6}`, 'aria-labelledby': `pu-${c.id}` },
          h('div', { class: 'pu-head' }, h('h3', { id: `pu-${c.id}` }, c.name), c.description && h('p', {}, c.description)),
          h('div', { class: 'pu-grid' }, c.tools.map(card))))),
    ].filter(Boolean));
  }
  await load();
}
