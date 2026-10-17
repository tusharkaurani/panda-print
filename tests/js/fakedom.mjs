// A very small stand-in for the browser's DOM, enough to run a tool's mount() under node and look at what it builds.
// It is deliberately a little strict: it prints booleans and null as text the way a real replaceChildren() does, so a
// stray "false" or "null" in the page shows up in the tests.
class FakeNode { }

class FakeText extends FakeNode {
  constructor(text) { super(); this.data = String(text); this.parentNode = null; }
  get textContent() { return this.data; }
  remove() { this.parentNode?.removeChild(this); }
}

const toNode = (x) => (x instanceof FakeNode ? x : new FakeText(x));  // native append/replaceChildren print any value as text

function parseSelector(sel) {  // "a .b > c" is not supported; "tag.class#id[attr=v]" parts separated by spaces are
  return sel.trim().split(/\s+/).map((part) => {
    const m = { tag: null, classes: [], id: null, attrs: [] };
    const re = /([#.]?)([\w-]+)|\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]|:not\(\[([\w-]+)\]\)/g;
    let t;
    m.nots = [];
    while ((t = re.exec(part))) {
      if (t[5]) m.nots.push(t[5]);
      else if (t[3]) m.attrs.push([t[3], t[4]]);
      else if (t[1] === '.') m.classes.push(t[2]);
      else if (t[1] === '#') m.id = t[2];
      else m.tag = t[2].toLowerCase();
    }
    return m;
  });
}

class FakeEl extends FakeNode {
  constructor(tag) {
    super();
    this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase();
    this.attrs = new Map(); this.childNodes = []; this.parentNode = null; this.listeners = {};
    this.dataset = {}; this._props = {};
    const style = new Map();
    this.style = { setProperty: (k, v) => style.set(k, v), removeProperty: (k) => style.delete(k), getPropertyValue: (k) => style.get(k) ?? '' };
    this.classList = {
      add: (...c) => this.setAttribute('class', [...new Set([...this._classes(), ...c])].join(' ')),
      remove: (...c) => this.setAttribute('class', this._classes().filter((x) => !c.includes(x)).join(' ')),
      toggle: (c, force) => { const has = this._classes().includes(c); const on = force ?? !has; this.classList[on ? 'add' : 'remove'](c); return on; },
      contains: (c) => this._classes().includes(c),
    };
    this.width = 300; this.height = 150;
  }
  _classes() { return (this.attrs.get('class') || '').split(/\s+/).filter(Boolean); }
  get children() { return this.childNodes.filter((n) => n instanceof FakeEl); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get parentElement() { return this.parentNode instanceof FakeEl ? this.parentNode : null; }
  get id() { return this.attrs.get('id') || ''; }
  get className() { return this.attrs.get('class') || ''; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(v) { this.replaceChildren(String(v)); }
  get clientWidth() { return 400; }
  get innerText() { return this.textContent; }
  get title() { return this.attrs.get('title') || ''; }
  set title(v) { this.setAttribute('title', v); }
  get href() { return this.attrs.get('href') || ''; }
  get src() { return this.attrs.get('src') || ''; }
  get type() { return this.attrs.get('type') || (this.localName === 'input' ? 'text' : ''); }

  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  hasAttribute(k) { return this.attrs.has(k); }
  toggleAttribute(k, force) { const on = force ?? !this.attrs.has(k); on ? this.attrs.set(k, '') : this.attrs.delete(k); return on; }
  get hidden() { return this.attrs.has('hidden'); }
  set hidden(v) { v ? this.attrs.set('hidden', '') : this.attrs.delete('hidden'); }
  get disabled() { return this.attrs.has('disabled'); }
  set disabled(v) { v ? this.attrs.set('disabled', '') : this.attrs.delete('disabled'); }
  get open() { return this.attrs.has('open'); }
  set open(v) { v ? this.attrs.set('open', '') : this.attrs.delete('open'); }
  get checked() { return '_checked' in this._props ? this._props._checked : this.attrs.has('checked'); }
  set checked(v) { this._props._checked = Boolean(v); }
  get options() { return this.localName === 'select' ? this.querySelectorAll('option') : []; }
  get selectedOptions() { return this.options.filter((o) => o.attrs.has('selected') || false).slice(0, 1).concat(this.options.length ? [] : []); }
  get value() {
    if (this.localName === 'select') {
      if ('_value' in this._props) return this._props._value;
      const o = this.options.find((x) => x.attrs.has('selected')) || this.options[0];
      return o ? (o.attrs.has('value') ? o.attrs.get('value') : o.textContent) : '';
    }
    return '_value' in this._props ? this._props._value : (this.attrs.get('value') ?? '');
  }
  set value(v) { this._props._value = String(v); if (this.localName === 'select') for (const o of this.options) o.attrs.delete('selected'); }

  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  removeEventListener(t, fn) { this.listeners[t] = (this.listeners[t] || []).filter((f) => f !== fn); }
  dispatchEvent(ev) { ev.target ||= this; ev.currentTarget = this; for (const fn of [...(this.listeners[ev.type] || [])]) fn.call(this, ev); return true; }
  click() { if (!this.disabled) this.dispatchEvent(new FakeEvent('click', { bubbles: true })); }
  focus() {} select() {} blur() {}
  setPointerCapture() {}

  _adopt(n) { n.parentNode?.removeChild(n); n.parentNode = this; return n; }
  append(...xs) { for (const x of xs) this.childNodes.push(this._adopt(toNode(x))); }
  prepend(...xs) { this.childNodes.unshift(...xs.map((x) => this._adopt(toNode(x)))); }
  appendChild(n) { this.append(n); return n; }
  replaceChildren(...xs) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; this.append(...xs); }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  remove() { this.parentNode?.removeChild(this); }
  replaceWith(...xs) { const p = this.parentNode; if (!p) return; const i = p.childNodes.indexOf(this); p.childNodes.splice(i, 1, ...xs.map((x) => p._adopt(toNode(x)))); this.parentNode = null; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  closest(sel) { const parts = parseSelector(sel); for (let x = this; x instanceof FakeEl; x = x.parentNode) if (matchesOne(x, parts[parts.length - 1])) return x; return null; }

  getBoundingClientRect() { return { left: 0, top: 0, right: 400, bottom: 300, width: 400, height: 300, x: 0, y: 0 }; }
  getContext() { return ctxStub(); }
  toBlob(cb) { cb(new Blob(['fake-image'])); }
  showModal() { this.open = true; }
  close() { if (this.open) { this.open = false; this.dispatchEvent(new FakeEvent('close')); } }
  scrollIntoView() {}

  querySelectorAll(sel) {
    const groups = sel.split(',').map(parseSelector);
    const out = [];
    const walk = (node, ancestors) => {
      for (const c of node.childNodes) {
        if (!(c instanceof FakeEl)) continue;
        if (groups.some((parts) => matchChain(c, parts, ancestors))) out.push(c);
        walk(c, [...ancestors, c]);
      }
    };
    walk(this, [this]);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function matchesOne(el, m) {
  if (m.tag && el.localName !== m.tag) return false;
  if (m.id && el.id !== m.id) return false;
  if (!m.classes.every((c) => el._classes().includes(c))) return false;
  if (!m.attrs.every(([k, v]) => (v === undefined ? el.attrs.has(k) : el.attrs.get(k) === v))) return false;
  return !m.nots.some((k) => el.attrs.has(k));
}
function matchChain(el, parts, ancestors) {
  if (!matchesOne(el, parts[parts.length - 1])) return false;
  let at = parts.length - 2;
  for (let i = ancestors.length - 1; i >= 0 && at >= 0; i--) if (matchesOne(ancestors[i], parts[at])) at--;
  return at < 0;
}

class FakeEvent { constructor(type, init = {}) { this.type = type; Object.assign(this, init); } preventDefault() { this.defaultPrevented = true; } stopPropagation() {} }

function ctxStub() {
  const store = {};
  return new Proxy(store, { get: (t, k) => (k in t ? t[k] : () => ({ width: 10 })), set: (t, k, v) => { t[k] = v; return true; } });
}

const body = new FakeEl('body');
const documentElement = new FakeEl('html');
documentElement.append(body);
const docListeners = {};
globalThis.Node = FakeNode;
globalThis.document = {
  body, documentElement, hidden: false,
  createElement: (t) => new FakeEl(t), createElementNS: (_ns, t) => new FakeEl(t), createTextNode: (t) => new FakeText(t),
  getElementById: (id) => body.querySelector(`#${id}`),
  querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s),
  addEventListener: (t, fn) => { (docListeners[t] ||= []).push(fn); }, removeEventListener: () => {},
};
const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
globalThis.window = Object.assign(globalThis, {
  innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {}, removeEventListener() {},
});
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.createImageBitmap = async () => ({ width: 700, height: 900, close() {} });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.fetch ||= async () => ({ ok: false, status: 404, text: async () => '', json: async () => ({}) });
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true });
globalThis.PointerEvent = FakeEvent;
globalThis.KeyboardEvent = FakeEvent;
globalThis.Event = FakeEvent;

export { FakeEl, FakeText, FakeEvent };
