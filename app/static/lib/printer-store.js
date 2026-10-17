// The list of printers and which one is selected, shared by the shell and every tool.
const KEY = 'pp-printer';
const listeners = new Set();

export const store = {
  list: [],
  defaults: {},
  currentId: null,

  get current() { return this.list.find((p) => p.id === this.currentId) || null; },

  subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },

  // called by the shell after (re)loading /api/printers
  set(list, defaults) {
    const before = JSON.stringify(this.list), prevId = this.currentId;
    this.list = list;
    this.defaults = defaults || this.defaults;
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch { /* blocked */ }
    const wanted = [this.currentId, saved].find((id) => id && list.some((p) => p.id === id));
    this.currentId = wanted || list[0]?.id || null;
    if (before !== JSON.stringify(list) || prevId !== this.currentId) listeners.forEach((fn) => fn(this.current));
  },

  select(id) {
    if (id === this.currentId || !this.list.some((p) => p.id === id)) return;
    this.currentId = id;
    try { localStorage.setItem(KEY, id); } catch { /* blocked */ }
    listeners.forEach((fn) => fn(this.current));
  },
};
