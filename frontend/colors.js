// Shared palette + colour ramps (one source of truth for map, 3D twin and echograms).
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
export const clamp01 = t => Math.min(1, Math.max(0, t));

/** Build a 256-step lookup ramp from [[t, '#rrggbb'], ...] stops; returns t(0..1) -> [r, g, b]. */
export function makeRamp(stops) {
  const S = stops.map(([t, h]) => [t, hex(h)]);
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255; let k = 0;
    while (k < S.length - 2 && t > S[k + 1][0]) k++;
    const [t0, c0] = S[k], [t1, c1] = S[k + 1], f = clamp01((t - t0) / (t1 - t0));
    for (let j = 0; j < 3; j++) lut[i * 3 + j] = c0[j] + (c1[j] - c0[j]) * f;
  }
  return t => { const i = Math.round(clamp01(t) * 255) * 3; return [lut[i], lut[i + 1], lut[i + 2]]; };
}
export const css = ([r, g, b], a = 1) => `rgba(${r | 0},${g | 0},${b | 0},${a})`;

// Bathymetry: shallow = pale gold, deep = indigo (kept clear of the cyan/blue vehicle trail)
export const DEPTH_RAMP = makeRamp([[0, '#ffeaa3'], [.25, '#f9a03f'], [.5, '#d6456b'], [.75, '#7a2aa8'], [1, '#26186a']]);
// Backscatter: side-scan style, weak = dark teal, strong = pale gold
export const BS_RAMP = makeRamp([[0, '#0a1224'], [.45, '#23788c'], [.75, '#9fd8c6'], [1, '#fff4c9']]);
// Water column: noise -> teal -> cyan -> neon green (gas) -> white (seabed return)
export const WC_RAMP = makeRamp([[0, '#050b18'], [.3, '#0a3a52'], [.55, '#00b8d4'], [.72, '#00ff88'], [.86, '#fff36b'], [1, '#ffffff']]);
// Sub-bottom: weak = violet, strong reflector = amber, bright spot = white
export const SBP_RAMP = makeRamp([[0, '#080a18'], [.25, '#2a1450'], [.5, '#a02d8c'], [.72, '#ff7a45'], [.88, '#ffd400'], [1, '#ffffff']]);

export const COLORS = { cyan: '#00f3ff', green: '#00ff88', red: '#ff3366', amber: '#ffd400', blue: '#2f8bff', violet: '#b36bff', bg: '#0a0f1d' };
// Trail: blue = standard survey; yellow -> red as the adaptive spiral tightens on the anomaly
export const TRAIL = { normal: '#2f8bff', spiral: ['#ffd400', '#ffae1a', '#ff7a2e', '#ff3366'] };
export const trailBucket = phase => (phase > 0 ? 1 + Math.min(3, Math.floor(phase * 4)) : 0);
export const trailColor = b => (b === 0 ? TRAIL.normal : TRAIL.spiral[b - 1]);
export const ANOM = { gas_plume: '#ff3366', shallow_gas: '#b36bff' };
export const ANOM_NAME = { gas_plume: 'Gas plume', shallow_gas: 'Shallow gas pocket' };
