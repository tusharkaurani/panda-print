// The whole contract between a tool and the shell: export mount(root, kit).
// kit = { toolId, api, h, svg, icon, fmt, ui, toast, poll }
export async function mount(root, kit) {
  const { h, ui, api, toolId } = kit;
  const card = ui.card({ title: 'Hello', iconName: 'info' });
  root.append(h('div', { class: 'grid' }, card.el));

  async function load() {
    const res = await api(`/${toolId}/hello`);
    if (!res.ok) return card.set(ui.errorBox(res.error, load));
    card.set(h('p', {}, res.data.message));
    card.stamp();
  }
  kit.poll(load, 30_000);  // runs now, then every 30 s while this tool is on screen
}
