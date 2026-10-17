// The toolbox handed to every tool's mount(root, kit).
import { api } from './api.js';
import { h, svg, clear } from './dom.js';
import { icon } from './icons.js';
import * as fmt from './fmt.js';
import * as ui from './ui.js';

export function makeKit(root) {
  return {
    api, h, svg, clear, icon, fmt, ui,
    toast: ui.toast,
    // poll while this tool is the one on screen
    poll: (fn, ms) => ui.poll(fn, ms, () => !root.hidden),
  };
}
