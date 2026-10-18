// The little drawings on the cards: one per kind of printable. Plain SVG built with kit.svg, no images to load.
// Colours are fixed (the cards show a sheet of paper, which stays light in dark mode too).

const C = '#00aeef', M = '#ec008c', Y = '#ffd400', K = '#3b4350', G = '#c9d1cc', P = '#ffffff';

export const ARTS = {
  passport: (s) => [
    s('rect', { x: 52, y: 14, width: 56, height: 72, rx: 3, fill: '#dbeaf6', stroke: K, 'stroke-width': 1.5 }),
    s('circle', { cx: 80, cy: 42, r: 13, fill: '#f1c9a5' }),
    s('path', { d: 'M54 86c2-18 12-26 26-26s24 8 26 26z', fill: K }),
    s('path', { d: 'M52 22h-8M52 78h-8M108 22h8M108 78h8M62 14v-8M98 14v-8M62 86v8M98 86v8', stroke: M, 'stroke-width': 1.5, fill: 'none' }),
    s('rect', { x: 118, y: 94, width: 14, height: 18, rx: 1.5, fill: '#dbeaf6', stroke: K }), s('rect', { x: 136, y: 94, width: 14, height: 18, rx: 1.5, fill: '#dbeaf6', stroke: K }),
  ],
  collage: (s) => [
    s('rect', { x: 34, y: 8, width: 92, height: 104, rx: 3, fill: P, stroke: G }),
    s('rect', { x: 40, y: 14, width: 80, height: 48, rx: 2, fill: '#dbeaf6', stroke: K, 'stroke-width': 1.2 }),
    s('path', { d: 'M40 62l22-22 14 14 12-9 32 25', fill: '#9ccdb0', stroke: 'none' }), s('circle', { cx: 100, cy: 28, r: 6, fill: Y }),
    s('rect', { x: 40, y: 68, width: 38, height: 38, rx: 2, fill: '#fbd6e4', stroke: K, 'stroke-width': 1.2 }),
    s('circle', { cx: 59, cy: 82, r: 7, fill: '#f1c9a5' }), s('path', { d: 'M44 106c1-10 7-14 15-14s14 4 15 14z', fill: M }),
    s('rect', { x: 82, y: 68, width: 38, height: 38, rx: 2, fill: '#fff2c4', stroke: K, 'stroke-width': 1.2 }),
    s('path', { d: 'M82 106l14-18 10 12 6-6 8 12z', fill: '#f0b429', stroke: 'none' }),
  ],
  worksheet: (s) => [
    s('rect', { x: 38, y: 8, width: 84, height: 104, rx: 3, fill: P, stroke: G }),
    s('rect', { x: 48, y: 17, width: 40, height: 6, rx: 2, fill: C }),
    ...[0, 1, 2, 3, 4].map((i) => s('path', { d: `M48 ${38 + i * 14}h64`, stroke: G, 'stroke-width': 2 })),
    s('circle', { cx: 54, cy: 38, r: 4, fill: 'none', stroke: M, 'stroke-width': 2 }), s('path', { d: 'M52 38l2 2 4-4', stroke: M, 'stroke-width': 1.6, fill: 'none' }),
    s('path', { d: 'M96 98l16-16 5 5-16 16-7 2z', fill: Y, stroke: K, 'stroke-width': 1.2 }),
  ],
  math: (s) => {
    const sym = (x, y, d, fill) => [s('circle', { cx: x, cy: y, r: 17, fill }), s('path', { d, stroke: P, 'stroke-width': 4, 'stroke-linecap': 'round', fill: 'none' })];
    return [
      ...sym(54, 36, 'M54 28v16M46 36h16', C), ...sym(106, 36, 'M98 36h16', M),
      ...sym(54, 84, 'M47 77l14 14M61 77l-14 14', '#f59e0b'), ...sym(106, 84, 'M98 84h16', K),
      s('circle', { cx: 106, cy: 77, r: 2.4, fill: P }), s('circle', { cx: 106, cy: 91, r: 2.4, fill: P }),
    ];
  },
  handwriting: (s) => [
    s('rect', { x: 30, y: 12, width: 100, height: 96, rx: 3, fill: P, stroke: G }),
    ...[0, 1, 2].flatMap((i) => [
      s('path', { d: `M40 ${40 + i * 28}h80`, stroke: C, 'stroke-width': 1.5 }),
      s('path', { d: `M40 ${30 + i * 28}h80`, stroke: G, 'stroke-width': 1, 'stroke-dasharray': '3 3' }),
    ]),
    s('path', { d: 'M42 38c4-12 8-12 8 0s4 12 8 0M64 38c3-9 10-9 10 0s-8 9-10 0', stroke: M, 'stroke-width': 2.2, fill: 'none', 'stroke-linecap': 'round' }),
  ],
  colouring: (s) => [
    s('rect', { x: 30, y: 10, width: 100, height: 100, rx: 3, fill: P, stroke: G }),
    s('circle', { cx: 112, cy: 28, r: 9, fill: Y, stroke: K, 'stroke-width': 1.6 }),
    s('path', { d: 'M44 94V58l36-24 36 24v36z', fill: 'none', stroke: K, 'stroke-width': 2.2, 'stroke-linejoin': 'round' }),
    s('rect', { x: 72, y: 72, width: 16, height: 22, fill: M, opacity: .8 }), s('path', { d: 'M44 58h72', stroke: K, 'stroke-width': 1.5 }),
    s('path', { d: 'M96 98l-4-14 22-26 8 6z', fill: C, stroke: K, 'stroke-width': 1.2 }),
  ],
  calendar: (s) => {
    const cells = [];
    for (let r = 0; r < 4; r++) for (let c = 0; c < 6; c++) cells.push(s('rect', { x: 44 + c * 12.4, y: 44 + r * 14, width: 10, height: 11, rx: 1.5, fill: r === 1 && c === 3 ? M : '#e8eeea' }));
    return [
      s('rect', { x: 36, y: 12, width: 88, height: 96, rx: 5, fill: P, stroke: G }),
      s('path', { d: 'M36 17a5 5 0 0 1 5-5h78a5 5 0 0 1 5 5v18H36z', fill: C }),
      s('rect', { x: 52, y: 6, width: 4, height: 12, rx: 2, fill: K }), s('rect', { x: 104, y: 6, width: 4, height: 12, rx: 2, fill: K }), ...cells,
    ];
  },
  graph: (s) => {
    const lines = [];
    for (let i = 0; i <= 8; i++) lines.push(s('path', { d: `M${36 + i * 11} 14v92M36 ${14 + i * 11.5}h88`, stroke: i % 4 === 0 ? C : '#b9e3f5', 'stroke-width': i % 4 === 0 ? 1.4 : .8 }));
    return [s('rect', { x: 36, y: 14, width: 88, height: 92, fill: P }), ...lines, s('path', { d: 'M36 96l22-30 22 12 28-48 16 10', stroke: M, 'stroke-width': 2.4, fill: 'none', 'stroke-linejoin': 'round' })];
  },
  lined: (s) => [
    s('rect', { x: 36, y: 10, width: 88, height: 100, rx: 3, fill: P, stroke: G }),
    s('path', { d: 'M54 10v100', stroke: M, 'stroke-width': 1.2 }),
    ...Array.from({ length: 8 }, (_, i) => s('path', { d: `M36 ${28 + i * 11}h88`, stroke: '#9fd6ef', 'stroke-width': 1.2 })),
    ...[0, 1, 2].map((i) => s('circle', { cx: 44, cy: 30 + i * 33, r: 2.6, fill: G })),
  ],
  dots: (s) => {
    const dots = [];
    for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) dots.push(s('circle', { cx: 44 + c * 10.5, cy: 20 + r * 11, r: 1.3, fill: K }));
    return [s('rect', { x: 36, y: 10, width: 88, height: 100, rx: 3, fill: P, stroke: G }), ...dots, s('path', { d: 'M54.5 31l21 0 0 22 21 0', stroke: M, 'stroke-width': 2, fill: 'none' })];
  },
  pdf: (s) => [
    s('path', { d: 'M44 8h50l22 22v82H44z', fill: P, stroke: G, 'stroke-width': 1.5 }), s('path', { d: 'M94 8v22h22', fill: '#e8eeea', stroke: G, 'stroke-width': 1.5 }),
    s('rect', { x: 34, y: 62, width: 56, height: 26, rx: 4, fill: M }),
    s('text', { x: 62, y: 80, 'text-anchor': 'middle', 'font-size': 16, 'font-weight': 700, 'font-family': 'system-ui, sans-serif', fill: P }, 'PDF'),
    s('path', { d: 'M104 48h-10M104 52h-14M104 56h-12', stroke: G, 'stroke-width': 2 }),
  ],
  poster: (s) => {
    const tiles = [[34, 14, C], [82, 14, M], [34, 62, Y], [82, 62, K]].map(([x, y, fill]) => s('rect', { x, y, width: 44, height: 44, fill, opacity: .9, stroke: P, 'stroke-width': 1.5 }));
    return [...tiles, s('path', { d: 'M80 8v104M28 60h104', stroke: P, 'stroke-width': 2.4, 'stroke-dasharray': '4 3' }), s('circle', { cx: 100, cy: 36, r: 9, fill: P, opacity: .85 })];
  },
  labels: (s) => {
    const cells = [];
    for (let r = 0; r < 4; r++) for (let c = 0; c < 2; c++) cells.push(s('rect', { x: 36 + c * 45, y: 14 + r * 24, width: 41, height: 20, rx: 4, fill: P, stroke: [C, M, Y, K][(r + c) % 4], 'stroke-width': 1.6 }),
      s('path', { d: `M${42 + c * 45} ${22 + r * 24}h22M${42 + c * 45} ${28 + r * 24}h14`, stroke: G, 'stroke-width': 2 }));
    return [s('rect', { x: 30, y: 8, width: 100, height: 104, rx: 3, fill: '#eef1ef', stroke: G }), ...cells];
  },
  craft: (s) => [
    s('path', { d: 'M40 96l40-70 40 70z', fill: Y, stroke: K, 'stroke-width': 1.6, 'stroke-linejoin': 'round' }),
    s('path', { d: 'M80 26v70M60 61h40', stroke: K, 'stroke-width': 1.2, 'stroke-dasharray': '4 3' }),
    s('circle', { cx: 52, cy: 28, r: 6, fill: 'none', stroke: M, 'stroke-width': 2 }), s('circle', { cx: 52, cy: 44, r: 6, fill: 'none', stroke: M, 'stroke-width': 2 }),
    s('path', { d: 'M57 31l30 24M57 41l30-24', stroke: C, 'stroke-width': 2.6, 'stroke-linecap': 'round' }),
  ],
  document: (s) => [
    s('rect', { x: 40, y: 8, width: 80, height: 104, rx: 3, fill: P, stroke: G }),
    s('rect', { x: 50, y: 18, width: 36, height: 7, rx: 2, fill: C }),
    ...[0, 1, 2, 3, 4, 5].map((i) => s('path', { d: `M50 ${38 + i * 11}h${i % 3 === 2 ? 40 : 60}`, stroke: G, 'stroke-width': 2.4, 'stroke-linecap': 'round' })),
  ],
};

export function art(kit, name) {
  const { svg } = kit;
  const draw = ARTS[name] || ARTS.document;
  return svg('svg', { viewBox: '0 0 160 120', role: 'img', 'aria-hidden': 'true', preserveAspectRatio: 'xMidYMid meet' }, ...draw(svg));
}
