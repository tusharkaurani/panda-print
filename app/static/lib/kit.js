// The toolbox handed to every tool's mount(root, kit).
import { api } from './api.js';
import { h, svg, clear } from './dom.js';
import { icon } from './icons.js';
import * as fmt from './fmt.js';
import * as ui from './ui.js';
import { store } from './printer-store.js';

export function makeKit(root, toolId, tabId) {
  return {
    toolId, tabId, api, h, svg, clear, icon, fmt, ui,
    toast: ui.toast,
    // the selected printer for tools with needs_printer: kit.printer.current is {id, name, ...};
    // subscribe(fn) calls fn(printer) whenever the selection or the printer's settings change
    printer: { get current() { return store.current; }, subscribe: (fn) => store.subscribe(fn) },
    // poll while this tool is the one on screen
    poll: (fn, ms) => ui.poll(fn, ms, () => !root.hidden),
  };
}
