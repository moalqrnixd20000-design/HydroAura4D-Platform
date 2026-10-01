// HUD instruments: speed gauge (knots), depth ladder (water column / seabed / penetration), backscatter sparkline.
const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
};
export const MS_TO_KN = 1.943844;

// ---------------------------------------------------------------------------- speed
export class SpeedGauge {
  constructor(host, max = 30) {
    this.max = max;
    const cx = 70, cy = 68, r = 54, a0 = 135, a1 = 405;
    const p = a => [cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    const d = `M${x0} ${y0} A${r} ${r} 0 1 1 ${x1} ${y1}`;
    this.len = r * (a1 - a0) * Math.PI / 180;
    const s = svg('svg', { viewBox: '0 0 140 112', role: 'img', 'aria-label': 'Speed gauge' }, host);
    svg('path', { d, class: 'g-track' }, s);
    this.arc = svg('path', { d, class: 'g-arc', 'stroke-dasharray': `0 ${this.len}` }, s);
    for (let v = 0; v <= max; v += 5) {
      const a = a0 + (a1 - a0) * v / max, major = v % 10 === 0, ro = r + 4, ri = r + (major ? 11 : 8);
      const c = Math.cos(a * Math.PI / 180), sn = Math.sin(a * Math.PI / 180);
      svg('line', { x1: cx + ro * c, y1: cy + ro * sn, x2: cx + ri * c, y2: cy + ri * sn, class: major ? 'g-tick major' : 'g-tick' }, s);
      if (major) { const t = svg('text', { x: cx + (r + 19) * c, y: cy + (r + 19) * sn + 3, class: 'g-num' }, s); t.textContent = v; }
    }
    this.val = svg('text', { x: cx, y: cy + 6, class: 'g-val' }, s);
    svg('text', { x: cx, y: cy + 21, class: 'g-unit' }, s).textContent = 'KNOTS';
    this.sub = svg('text', { x: cx, y: cy + 38, class: 'g-sub' }, s);
    this.set(0);
  }
  set(ms) {
    const kn = ms * MS_TO_KN;
    this.arc.setAttribute('stroke-dasharray', `${this.len * Math.min(1, kn / this.max)} ${this.len}`);
    this.val.textContent = kn.toFixed(1);
    this.sub.textContent = `${ms.toFixed(1)} m/s`;
  }
}

// ---------------------------------------------------------------------------- depth ladder
export class DepthLadder {
  constructor(host, maxDepth = 180) {
    this.max = maxDepth;
    this.top = 14; this.h = 186;
    const s = svg('svg', { viewBox: '0 0 112 214', role: 'img', 'aria-label': 'Depth ladder' }, host);
    const defs = svg('defs', {}, s);
    const g1 = svg('linearGradient', { id: 'gWater', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
    svg('stop', { offset: '0%', 'stop-color': '#00f3ff', 'stop-opacity': .05 }, g1);
    svg('stop', { offset: '100%', 'stop-color': '#00f3ff', 'stop-opacity': .32 }, g1);
    const g2 = svg('linearGradient', { id: 'gSed', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
    svg('stop', { offset: '0%', 'stop-color': '#b36bff', 'stop-opacity': .55 }, g2);
    svg('stop', { offset: '100%', 'stop-color': '#b36bff', 'stop-opacity': .04 }, g2);
    this.water = svg('rect', { x: 40, width: 46, fill: 'url(#gWater)' }, s);
    this.sed = svg('rect', { x: 40, width: 46, fill: 'url(#gSed)' }, s);
    for (let d = 0; d <= maxDepth; d += 20) {
      const y = this.y(d);
      svg('line', { x1: 34, x2: d % 40 === 0 ? 24 : 28, y1: y, y2: y, class: 'g-tick major' }, s);
      if (d % 40 === 0) svg('text', { x: 20, y: y + 3, class: 'g-num end' }, s).textContent = d;
    }
    svg('line', { x1: 40, x2: 40, y1: this.y(0), y2: this.y(maxDepth), class: 'g-tick major' }, s);
    this.seaLine = svg('line', { x1: 40, x2: 90, class: 'l-sea' }, s);
    this.penLine = svg('line', { x1: 40, x2: 90, class: 'l-pen' }, s);
    this.seaTxt = svg('text', { x: 92, class: 'g-lab sea' }, s);
    this.penTxt = svg('text', { x: 92, class: 'g-lab pen' }, s);
    this.veh = svg('path', { d: 'M0 0 L-12 -6 L-12 6Z', class: 'l-veh' }, s);
    this.vehTxt = svg('text', { x: 44, class: 'g-lab veh' }, s);
    this.alt = svg('text', { x: 63, class: 'g-sub' }, s);
    this.set(15, 120, 25);
  }
  y(d) { return this.top + (Math.min(d, this.max) / this.max) * this.h; }
  set(depth, seabed, pen) {
    const ys = this.y(seabed), yp = this.y(seabed + pen);
    this.water.setAttribute('y', this.y(0)); this.water.setAttribute('height', Math.max(0, ys - this.y(0)));
    this.sed.setAttribute('y', ys); this.sed.setAttribute('height', Math.max(0, yp - ys));
    for (const [l, y] of [[this.seaLine, ys], [this.penLine, yp]]) { l.setAttribute('y1', y); l.setAttribute('y2', y); }
    this.seaTxt.setAttribute('y', ys - 3); this.seaTxt.textContent = `SEABED ${seabed.toFixed(0)}`;
    this.penTxt.setAttribute('y', yp + 10); this.penTxt.textContent = `SBP +${pen.toFixed(0)} m`;
    const yv = this.y(depth);
    this.veh.setAttribute('transform', `translate(40 ${yv})`);
    this.vehTxt.setAttribute('y', yv + 3); this.vehTxt.textContent = `${depth.toFixed(0)} m`;
    this.alt.setAttribute('y', (ys + this.y(depth)) / 2 + 3);
    this.alt.textContent = `ALT ${(seabed - depth).toFixed(0)} m`;
  }
}

// ---------------------------------------------------------------------------- sparkline
export class Sparkline {
  constructor(cv, n = 160, lo = -45, hi = -10, color = '#00f3ff') {
    this.cv = cv; this.ctx = cv.getContext('2d'); this.n = n; this.lo = lo; this.hi = hi; this.color = color; this.v = [];
  }
  push(x) { this.v.push(x); if (this.v.length > this.n) this.v.shift(); this.draw(); }
  reset() { this.v = []; this.draw(); }
  draw() {
    const cv = this.cv, ctx = this.ctx, dpr = window.devicePixelRatio || 1;
    const w = Math.round(cv.clientWidth * dpr) || 10, h = Math.round(cv.clientHeight * dpr) || 10;
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    ctx.clearRect(0, 0, w, h);
    if (this.v.length < 2) return;
    const X = i => (i / (this.n - 1)) * w, Y = x => h - 2 - ((x - this.lo) / (this.hi - this.lo)) * (h - 4);
    ctx.beginPath();
    this.v.forEach((x, i) => (i ? ctx.lineTo(X(i + this.n - this.v.length), Y(x)) : ctx.moveTo(X(this.n - this.v.length), Y(x))));
    ctx.strokeStyle = this.color; ctx.lineWidth = 1.5 * dpr; ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(X(this.n - this.v.length), h); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, 'rgba(0,243,255,.25)'); g.addColorStop(1, 'rgba(0,243,255,0)');
    ctx.fillStyle = g; ctx.fill();
  }
}
