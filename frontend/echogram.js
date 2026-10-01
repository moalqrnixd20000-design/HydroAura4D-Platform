// Live signal canvases.
//  Echogram  - one scrolling section, newest ping at the right edge:
//                upper band  MBES water-column backscatter, absolute depth (gas plumes rise from the seabed echo)
//                lower band  SBP sub-bottom reflections, metres below the seabed (bright spot + blanking = shallow gas)
//  SwathFan  - the current ping's cross-track water-column fan with the seabed profile.
import { WC_RAMP, SBP_RAMP, COLORS } from './colors.js';

const BG = [8, 12, 24];
const GUT = 44;                 // css px of non-scrolling depth gutter
const dprNow = () => window.devicePixelRatio || 1;

function sizeTo(cv) {
  const dpr = dprNow(), w = Math.max(16, Math.round(cv.clientWidth * dpr)), h = Math.max(16, Math.round(cv.clientHeight * dpr));
  if (cv.width === w && cv.height === h) return false;
  cv.width = w; cv.height = h; return true;
}

export class Echogram {
  constructor(cv, { d0 = 10, d1 = 146, sbpM = 40, pxPerPing = 2 } = {}) {
    this.cv = cv; this.ctx = cv.getContext('2d', { willReadFrequently: false });
    this.d0 = d0; this.d1 = d1; this.sbpM = sbpM; this.pxPerPing = pxPerPing;
    this.proj = new Float32Array(Math.ceil(d1 - d0) + 3);
    this.pendingMark = null;
    this.ov = document.createElement('canvas');                // static overlay: depth ticks + band titles
    this.ov.className = 'echo-overlay'; cv.parentElement.appendChild(this.ov);
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(cv);
    this.resize();
  }

  resize() {
    const changed = sizeTo(this.cv);
    this.ov.width = this.cv.width; this.ov.height = this.cv.height;
    this.ov.style.width = this.cv.clientWidth + 'px'; this.ov.style.height = this.cv.clientHeight + 'px';
    const dpr = dprNow(), H = this.cv.height;
    this.hWc = Math.round(H * 0.56); this.gap = Math.round(4 * dpr); this.hSbp = H - this.hWc - this.gap;
    this.gut = Math.round(GUT * dpr);
    if (changed) this.reset();
    this.drawOverlay();
  }

  reset() {
    const { ctx, cv } = this;
    ctx.fillStyle = `rgb(${BG})`; ctx.fillRect(0, 0, cv.width, cv.height);
    this.drawOverlay();
  }

  drawOverlay() {
    const o = this.ov.getContext('2d'), dpr = dprNow(), { hWc, hSbp, gap, gut } = this;
    o.clearRect(0, 0, this.ov.width, this.ov.height);
    o.fillStyle = 'rgba(10,15,29,.92)'; o.fillRect(0, 0, gut, this.ov.height);
    o.font = `${10 * dpr}px "JetBrains Mono", ui-monospace, monospace`; o.textAlign = 'right';
    o.strokeStyle = 'rgba(0,243,255,.35)'; o.lineWidth = 1;
    const yWc = d => ((d - this.d0) / (this.d1 - this.d0)) * hWc;
    for (let d = 20; d < this.d1; d += 20) {
      const y = Math.round(yWc(d)) + .5;
      o.beginPath(); o.moveTo(gut - 6 * dpr, y); o.lineTo(gut, y); o.stroke();
      o.fillStyle = '#7b8fab'; o.fillText(d, gut - 9 * dpr, y + 3 * dpr);
      o.fillStyle = 'rgba(0,243,255,.07)'; o.fillRect(gut, y - .5, this.ov.width - gut, 1);
    }
    const y0 = hWc + gap;
    for (let z = 0; z <= this.sbpM; z += 10) {
      const y = Math.round(y0 + (z / this.sbpM) * hSbp) - (z === this.sbpM ? 1 : 0) + .5;
      o.beginPath(); o.moveTo(gut - 6 * dpr, y); o.lineTo(gut, y); o.stroke();
      o.fillStyle = '#7b8fab'; o.fillText(`+${z}`, gut - 9 * dpr, y + 3 * dpr);
      o.fillStyle = 'rgba(179,107,255,.10)'; o.fillRect(gut, y - .5, this.ov.width - gut, 1);
    }
    o.textAlign = 'left'; o.font = `600 ${10 * dpr}px "Chakra Petch", system-ui, sans-serif`;
    o.fillStyle = 'rgba(0,243,255,.9)'; o.fillText('WATER COLUMN  ·  MBES  ·  depth m', gut + 8 * dpr, 13 * dpr);
    o.fillStyle = 'rgba(255,170,90,.95)'; o.fillText('SUB-BOTTOM  ·  SBP  ·  m below seabed', gut + 8 * dpr, y0 + 13 * dpr);
    o.fillStyle = 'rgba(10,15,29,.9)'; o.fillRect(0, hWc, gut, gap);
    o.fillStyle = 'rgba(0,243,255,.25)'; o.fillRect(gut, hWc + gap / 2 - .5, this.ov.width - gut, 1);
  }

  /** Flag the next column (e.g. an alert) with a vertical line + label that scrolls with the data. */
  mark(text, color = COLORS.red) { this.pendingMark = { text, color }; }

  push(fused) {
    const { ctx, cv } = this, dpr = dprNow();
    const cw = Math.max(1, Math.round(this.pxPerPing * dpr)), H = cv.height, W = cv.width, gut = this.gut;
    ctx.drawImage(cv, gut + cw, 0, W - gut - cw, H, gut, 0, W - gut - cw, H);          // scroll left
    const img = ctx.createImageData(cw, H), px = img.data;
    const put = (y, c) => { for (let x = 0; x < cw; x++) { const i = (y * cw + x) * 4; px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = 255; } };

    // ---- water column band
    const proj = this.proj; proj.fill(0);
    const fan = fused.wc_fan;
    if (fan) {
      const raw = atob(fan.b64), nb = fan.beams, nk = fan.bins;
      for (let b = 0; b < nb; b++) {
        const th = fan.angles_deg[b] * Math.PI / 180, c = Math.cos(th);
        for (let k = 0; k < nk; k++) {
          const v = raw.charCodeAt(b * nk + k);
          if (v < 14) continue;
          const d = fan.veh_depth_m + (k + .5) * fan.bin_m * c, half = .5 * fan.bin_m * c;
          const i0 = Math.max(0, Math.floor(d - half - this.d0)), i1 = Math.min(proj.length - 1, Math.ceil(d + half - this.d0));
          for (let i = i0; i <= i1; i++) if (v > proj[i]) proj[i] = v;
        }
      }
    }
    const span = this.d1 - this.d0;
    for (let y = 0; y < this.hWc; y++) {
      const v = proj[Math.floor(((y + .5) / this.hWc) * span)];
      put(y, v >= 12 ? WC_RAMP((v - 10) / 34) : BG);
    }
    for (let y = this.hWc; y < this.hWc + this.gap; y++) put(y, [16, 24, 44]);

    // ---- sub-bottom band
    const s = fused.sbp, y0 = this.hWc + this.gap;
    for (let y = 0; y < this.hSbp; y++) {
      let c = BG;
      if (s) {
        const i = Math.round((((y + .5) / this.hSbp) * this.sbpM) / s.dz_m), a = Math.max(0, s.trace[i] ?? 0);
        if (a > .06) c = SBP_RAMP(Math.sqrt(a / 2.6));
      }
      put(y0 + y, c);
    }
    if (s) {                                                       // penetration limit marker
      const yy = Math.min(this.hSbp - 1, Math.round((s.penetration_m / this.sbpM) * this.hSbp));
      put(y0 + yy, [0, 255, 136]);
    }
    ctx.putImageData(img, W - cw, 0);

    if (this.pendingMark) {                                        // event flag
      const { text, color } = this.pendingMark; this.pendingMark = null;
      ctx.fillStyle = color; ctx.globalAlpha = .85; ctx.fillRect(W - cw - Math.round(dpr), 0, Math.max(1, Math.round(dpr)), H); ctx.globalAlpha = 1;
      ctx.font = `600 ${10 * dpr}px "Chakra Petch", sans-serif`; ctx.textAlign = 'right';
      const tw = ctx.measureText(text).width + 8 * dpr;
      ctx.fillStyle = 'rgba(10,15,29,.85)'; ctx.fillRect(W - cw - tw - 2 * dpr, 20 * dpr, tw, 15 * dpr);
      ctx.fillStyle = color; ctx.fillText(text, W - cw - 6 * dpr, 31 * dpr);
    }
  }
}

export class SwathFan {
  constructor(cv) {
    this.cv = cv; this.ctx = cv.getContext('2d');
    new ResizeObserver(() => { sizeTo(cv); }).observe(cv);
  }
  draw(fused) {
    const { cv, ctx } = this; sizeTo(cv);
    const dpr = dprNow(), W = cv.width, H = cv.height;
    ctx.fillStyle = `rgb(${BG})`; ctx.fillRect(0, 0, W, H);
    const sw = fused.swath, fan = fused.wc_fan;
    if (!sw) return;
    const half = 150, dTop = 0, dBot = Math.max(...sw.depth_m) + 10, padT = 16 * dpr, padB = 14 * dpr;
    const X = a => W / 2 + (a / half) * (W / 2 - 8 * dpr);
    const Y = d => padT + ((d - dTop) / (dBot - dTop)) * (H - padT - padB);
    ctx.strokeStyle = 'rgba(0,243,255,.12)'; ctx.lineWidth = 1;
    for (let d = 20; d < dBot; d += 20) { const y = Math.round(Y(d)) + .5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    ctx.beginPath(); ctx.moveTo(W / 2 + .5, padT); ctx.lineTo(W / 2 + .5, H - padB); ctx.stroke();
    if (fan) {
      const raw = atob(fan.b64), nb = fan.beams, nk = fan.bins, ang = fan.angles_deg;
      const cell = Math.max(2, (W / nb) * .62);
      for (let b = 0; b < nb; b++) {
        const th = ang[b] * Math.PI / 180, sn = Math.sin(th), cs = Math.cos(th);
        for (let k = 0; k < nk; k++) {
          const v = raw.charCodeAt(b * nk + k);
          if (v < 14) continue;
          const r = (k + .5) * fan.bin_m, c = WC_RAMP((v - 10) / 34);
          ctx.fillStyle = `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
          ctx.fillRect(X(r * sn) - cell / 2, Y(fan.veh_depth_m + r * cs) - cell / 2, cell, cell * 1.25);
        }
      }
    }
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5 * dpr; ctx.beginPath();
    sw.across_m.forEach((a, i) => { const x = X(a), y = Y(sw.depth_m[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
    const vy = Y(fan ? fan.veh_depth_m : 15);
    ctx.fillStyle = COLORS.cyan; ctx.beginPath(); ctx.moveTo(W / 2, vy - 6 * dpr); ctx.lineTo(W / 2 + 6 * dpr, vy + 5 * dpr); ctx.lineTo(W / 2 - 6 * dpr, vy + 5 * dpr); ctx.fill();
    ctx.font = `600 ${10 * dpr}px "Chakra Petch", sans-serif`; ctx.fillStyle = '#7b8fab';
    ctx.textAlign = 'left'; ctx.fillText('PORT', 6 * dpr, 12 * dpr); ctx.textAlign = 'right'; ctx.fillText('STBD', W - 6 * dpr, 12 * dpr);
    ctx.textAlign = 'center'; ctx.fillText(`±${half} m`, W / 2, H - 3 * dpr);
  }
}
