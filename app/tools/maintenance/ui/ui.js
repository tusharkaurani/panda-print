// Printer Maintenance UI entry. The shell calls mount() once per tab (kit.tabId); each tab lives in its own
// module, so adding another maintenance utility means adding a file here and a ToolTab in __init__.py.
const TABS = {
  details: () => import('./details.js'),
  nozzle: () => import('./nozzle.js'),
};

export async function mount(root, kit) {
  root.classList.add('mt');
  const load = TABS[kit.tabId] || TABS.details;
  return (await load()).mount(root, kit);
}
