// Tactical map (Leaflet): switchable basemaps, live bathymetry / backscatter mosaic, colour-coded trail,
// planned route, vehicle with sonar ping, anomaly pins with selectable footprint.
import { DEPTH_RAMP, BS_RAMP, ANOM, ANOM_NAME, trailBucket, trailColor, css } from './colors.js';

const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';
const BASEMAPS = {
  dark: { url: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, maxNativeZoom: 16, label: 'Dark' },
  sat: { url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, maxNativeZoom: 17, label: 'Satellite' },
  ocean: { url: `${ESRI}/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}`, maxNativeZoom: 13, label: 'Ocean' },
};
const ROUTE_STYLE = {
  survey: { color: '#7fb4ff', weight: 1.5, opacity: .55, dashArray: '5 7' },
  spiral: { color: '#ffd400', weight: 2, opacity: .9, dashArray: '2 5' },
  rejoin: { color: '#00ff88', weight: 1.5, opacity: .7, dashArray: '2 6' },
};
const BLANK = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

// ---------------------------------------------------------------------------- gridded mosaic
export class Mosaic {
  constructor(g) {
    Object.assign(this, { x0: -120, y0: -120, cell: 4, nx: 210, ny: 160, dmin: 100, dmax: 140, bmin: -45, bmax: -10 }, g || {});
    const N = this.nx * this.ny;
    this.n = new Float32Array(N); this.d = new Float32Array(N); this.b = new Float32Array(N);
    this.canvas = document.createElement('canvas'); this.canvas.width = this.nx; this.canvas.height = this.ny;
    this.dirty = true;
    if (g && g.depth_b64) this._load(g);
  }
  _load(g) {                                   // restore the server's snapshot (quantised uint8, 0 = no data)
    const dq = Uint8Array.from(atob(g.depth_b64), c => c.charCodeAt(0)), bq = Uint8Array.from(atob(g.bs_b64), c => c.charCodeAt(0));
    for (let i = 0; i < dq.length; i++) if (dq[i]) {
      this.n[i] = 2;
      this.d[i] = 2 * (this.dmin + (dq[i] - 1) / 254 * (this.dmax - this.dmin));
      this.b[i] = 2 * (this.bmin + (bq[i] - 1) / 254 * (this.bmax - this.bmin));
    }
  }
  add(east, north, depth, bs) {                // same 3x3 splat as backend/mosaic.py
    for (let k = 0; k < east.length; k++) {
      const ix = Math.floor((east[k] - this.x0) / this.cell), iy = Math.floor((north[k] - this.y0) / this.cell);
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const jx = ix + dx, jy = iy + dy;
        if (jx < 0 || jx >= this.nx || jy < 0 || jy >= this.ny) continue;
        const w = (2 - Math.abs(dx)) * (2 - Math.abs(dy)), i = jy * this.nx + jx;
        this.n[i] += w; this.d[i] += w * depth[k]; this.b[i] += w * bs[k];
      }
    }
    this.dirty = true;
  }
  bounds() { return [[this.x0, this.y0], [this.x0 + this.nx * this.cell, this.y0 + this.ny * this.cell]]; }
  render(mode) {
    const { nx, ny, n, d, b } = this, ctx = this.canvas.getContext('2d'), img = ctx.createImageData(nx, ny), px = img.data;
    const depthAt = (x, y) => { x = Math.min(nx - 1, Math.max(0, x)); y = Math.min(ny - 1, Math.max(0, y)); const i = y * nx + x; return n[i] > .5 ? d[i] / n[i] : null; };
    for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
      const i = y * nx + x, o = ((ny - 1 - y) * nx + x) * 4;      // row 0 of the image = north
      if (n[i] < .5) continue;
      let c, shade = 1;
      if (mode === 'bs') c = BS_RAMP((b[i] / n[i] - this.bmin) / (this.bmax - this.bmin));
      else {
        const z = d[i] / n[i];
        c = DEPTH_RAMP((z - this.dmin) / (this.dmax - this.dmin));
        const zE = depthAt(x + 1, y) ?? z, zW = depthAt(x - 1, y) ?? z, zN = depthAt(x, y + 1) ?? z, zS = depthAt(x, y - 1) ?? z;
        const sx = (zE - zW) / (2 * this.cell), sy = (zN - zS) / (2 * this.cell);
        shade = .62 + Math.max(-.35, Math.min(.5, 9 * (-.7 * sx + .7 * sy)));       // light from the north-west
      }
      px[o] = Math.min(255, c[0] * shade); px[o + 1] = Math.min(255, c[1] * shade); px[o + 2] = Math.min(255, c[2] * shade);
      px[o + 3] = 120 + 120 * Math.min(1, n[i] / 6);
    }
    ctx.putImageData(img, 0, 0); this.dirty = false;
    return this.canvas.toDataURL();
  }
}

// ---------------------------------------------------------------------------- map
export class TacticalMap {
  constructor(el, { onSelect } = {}) {
    this.el = el; this.onSelect = onSelect || (() => {});
    this.map = L.map(el, { zoomControl: true, attributionControl: false, preferCanvas: true, maxZoom: 20, zoomSnap: .25, zoomDelta: .5 });
    this.map.setView([27.002, 34.053], 15);       // a view must exist before layers are added (canvas renderer needs it)
    L.control.scale({ metric: true, imperial: false, position: 'bottomleft' }).addTo(this.map);
    L.control.attribution({ prefix: false, position: 'bottomright' }).addTo(this.map);
    this.base = null; this.baseKey = null;
    this.pad = { tl: [0, 0], br: [0, 0] };
    this.mosaic = new Mosaic(); this.overlayMode = 'depth'; this.overlay = null;
    this.blockLayer = null; this.routeLayer = L.layerGroup().addTo(this.map);
    this.trailLayer = L.layerGroup().addTo(this.map);
    this.anomLayer = L.layerGroup().addTo(this.map);
    this.truthLayer = L.layerGroup();
    this.swath = L.polyline([], { color: '#00ff88', weight: 2, opacity: .85 }).addTo(this.map);
    this.vehicle = null; this.anoms = new Map(); this.selected = null;
    this._bucket = -1; this._line = null; this._last = null;
    this.legend = this._makeLegend();
    setInterval(() => this._refreshOverlay(), 500);
  }

  // ---- geometry
  local(e, n) {
    const s = this.survey;
    return [s.sw[0] + n / 111320, s.sw[1] + e / (111320 * Math.cos(s.sw[0] * Math.PI / 180))];
  }

  // ---- lifecycle
  init(h) {
    this.survey = h.survey;
    const b = L.latLngBounds(h.survey.sw, h.survey.ne);
    if (this.blockLayer) this.map.removeLayer(this.blockLayer);
    this.blockLayer = L.rectangle(b, { color: '#00f3ff', weight: 1, fillOpacity: .02, dashArray: '2 6', interactive: false }).addTo(this.map);
    this.setBasemap(this.baseKey || 'dark');
    this.resetData(h);
    if (!this.vehicle) {
      this.vehicle = L.marker(b.getSouthWest(), { icon: L.divIcon({ className: 'veh-icon', iconSize: [44, 44], iconAnchor: [22, 22],
        html: '<span class="ping"></span><span class="ping p2"></span><svg class="arrow" viewBox="-14 -14 28 28"><path d="M0-11 L7 9 L0 5 L-7 9Z"/></svg>' }),
        zIndexOffset: 1000, interactive: false }).addTo(this.map);
    }
    this.fitSurvey(false);
  }

  resetData(h) {
    this.mosaic = new Mosaic(h.mosaic);
    this._ensureOverlay(); this._refreshOverlay(true);
    this.trailLayer.clearLayers(); this._bucket = -1; this._line = null; this._last = null;
    for (const p of h.track || []) this.addTrail(...this.local(p[0], p[1]), p[3] || 0);
    this.swath.setLatLngs([]);
    this.anomLayer.clearLayers(); this.anoms.clear(); this.selected = null;
    this.setAnomalies(h.anomalies || []);
    this.setRoute(h.route || []);
    this.setTruth(h.truth);
  }

  // ---- basemap / overlay
  setBasemap(key) {
    this.baseKey = key;
    if (this.base) { this.map.removeLayer(this.base); this.base = null; }
    const cfg = BASEMAPS[key];
    if (cfg) {
      this.base = L.tileLayer(cfg.url, { maxNativeZoom: cfg.maxNativeZoom, maxZoom: 20, attribution: 'Tiles © Esri', className: 'basemap' }).addTo(this.map);
      this.base.bringToBack();
    }
  }
  setOverlay(mode) { this.overlayMode = mode; this._ensureOverlay(); this._refreshOverlay(true); this._updateLegend(); }
  _ensureOverlay() {
    if (this.overlay) { this.map.removeLayer(this.overlay); this.overlay = null; }
    if (this.overlayMode === 'off' || !this.survey) return;
    const [sw, ne] = this.mosaic.bounds();
    this.overlay = L.imageOverlay(BLANK, [this.local(...sw), this.local(...ne)], { opacity: .9, interactive: false, className: 'mosaic-img' }).addTo(this.map);
    if (this.base) this.base.bringToBack();
  }
  _refreshOverlay(force) {
    if (!this.overlay || !(force || this.mosaic.dirty)) return;
    this.overlay.setUrl(this.mosaic.render(this.overlayMode));
  }
  addSoundings(sw) { this.mosaic.add(sw.east_m, sw.north_m, sw.depth_m, sw.backscatter_db); }

  _makeLegend() {
    const self = this, C = L.Control.extend({
      onAdd() { const d = L.DomUtil.create('div', 'map-legend glass-lite'); self._legendEl = d; return d; },
    });
    const c = new C({ position: 'bottomleft' }); c.addTo(this.map); this._updateLegend(); return c;
  }
  _updateLegend() {
    const m = this.mosaic, bs = this.overlayMode === 'bs', off = this.overlayMode === 'off';
    const ramp = bs ? BS_RAMP : DEPTH_RAMP;
    const stops = Array.from({ length: 8 }, (_, i) => css(ramp(i / 7))).join(',');
    this._legendEl.innerHTML = off ? '' : `<b>${bs ? 'BACKSCATTER' : 'SEABED DEPTH'}</b>
      <i style="background:linear-gradient(90deg,${stops})"></i>
      <span>${bs ? `${m.bmin} dB` : `${m.dmin} m`}</span><span>${bs ? `${m.bmax} dB` : `${m.dmax} m`}</span>`;
    this._legendEl.style.display = off ? 'none' : '';
  }

  // ---- viewport (panels float over the map, so frame the *visible* region)
  setPadding(tl, br) { this.pad = { tl, br }; }
  fitSurvey(animate = true) {
    if (!this.survey) return;
    this.map.invalidateSize();
    this.map.fitBounds(L.latLngBounds(this.survey.sw, this.survey.ne), { paddingTopLeft: this.pad.tl, paddingBottomRight: this.pad.br, animate, maxZoom: 19 });
  }
  focus(a) {
    const ll = L.latLng(a.lat, a.lon);
    this.map.invalidateSize();
    this.map.fitBounds(ll.toBounds(a.radius_m * 5), { paddingTopLeft: this.pad.tl, paddingBottomRight: this.pad.br, maxZoom: 19, animate: true, duration: .8 });
  }
  resize() { this.map.invalidateSize(); }

  // ---- vehicle & trail
  updateVehicle(tel, swath, phase) {
    const ll = [tel.lat, tel.lon];
    this.vehicle.setLatLng(ll);
    const arrow = this.vehicle.getElement()?.querySelector('.arrow');
    if (arrow) arrow.style.transform = `rotate(${tel.heading_deg}deg)`;
    this.vehicle.getElement()?.classList.toggle('adaptive', phase > 0);
    this.addTrail(tel.lat, tel.lon, phase);
    if (swath) this.swath.setLatLngs([this.local(swath.east_m[0], swath.north_m[0]), this.local(swath.east_m.at(-1), swath.north_m.at(-1))]);
  }
  addTrail(lat, lon, phase) {
    const b = trailBucket(phase);
    if (b !== this._bucket || !this._line) {
      this._bucket = b;
      this._line = L.polyline(this._last ? [this._last] : [], { color: trailColor(b), weight: b ? 3.5 : 2.5, opacity: .95, lineCap: 'round' }).addTo(this.trailLayer);
    }
    this._line.addLatLng([lat, lon]); this._last = [lat, lon];
  }

  // ---- planned route
  setRoute(route) {
    this.routeLayer.clearLayers();
    if (!route.length) return;
    let kind = route[0].kind, pts = [];
    const flush = () => { if (pts.length > 1) L.polyline(pts, ROUTE_STYLE[kind] || ROUTE_STYLE.survey).addTo(this.routeLayer); };
    for (const w of route) {
      if (w.kind !== kind) { pts.push([w.lat, w.lon]); flush(); kind = w.kind; pts = [pts.at(-1)]; }
      else pts.push([w.lat, w.lon]);
    }
    flush();
  }

  // ---- anomalies
  setAnomalies(list) {
    for (const a of list) {
      const col = ANOM[a.type] || '#fff';
      let m = this.anoms.get(a.id);
      if (!m) {
        m = {
          ring: L.circle([a.lat, a.lon], { radius: a.radius_m, color: col, weight: 1.5, fillColor: col, fillOpacity: .14, interactive: false }).addTo(this.anomLayer),
          pin: L.marker([a.lat, a.lon], { icon: L.divIcon({ className: 'anom-icon', iconSize: [34, 34], iconAnchor: [17, 17],
            html: `<span class="pin" style="--c:${col}"><i class="r1"></i><i class="r2"></i><b></b></span>` }), zIndexOffset: 500 }).addTo(this.anomLayer),
        };
        m.pin.on('click', () => this.onSelect(a.id));
        m.pin.bindTooltip('', { direction: 'top', offset: [0, -14], className: 'tip' });
        this.anoms.set(a.id, m);
      }
      m.ring.setLatLng([a.lat, a.lon]).setRadius(a.radius_m).setStyle({ dashArray: a.status === 'surveyed' ? '4 5' : null });
      m.pin.setLatLng([a.lat, a.lon]);
      m.pin.setTooltipContent(`<b>${a.id}</b> · ${ANOM_NAME[a.type]}<br>severity ${a.severity.toFixed(0)} · ${(a.confidence * 100).toFixed(0)}% · ${a.status}`);
    }
    this.select(this.selected, false);
  }
  select(id, focus = false) {
    this.selected = id;
    for (const [k, m] of this.anoms) {
      m.pin.getElement()?.classList.toggle('sel', k === id);
      m.ring.setStyle({ weight: k === id ? 3 : 1.5, fillOpacity: k === id ? .3 : .14 });
    }
  }

  // ---- ground truth (demo aid)
  setTruth(t) {
    this.truthLayer.clearLayers();
    if (!t) return;
    const st = { color: '#ffffff', weight: 1, dashArray: '3 4', fillOpacity: 0, interactive: false };
    if (t.plume) L.circle([t.plume.lat, t.plume.lon], { ...st, radius: 10 }).bindTooltip('truth · plume vent').addTo(this.truthLayer);
    if (t.pocket) L.circle([t.pocket.lat, t.pocket.lon], { ...st, radius: t.pocket.radius_m }).bindTooltip('truth · gas pocket').addTo(this.truthLayer);
  }
  showTruth(on) { on ? this.truthLayer.addTo(this.map) : this.map.removeLayer(this.truthLayer); }
}
